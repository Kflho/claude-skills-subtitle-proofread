"""能力清单：每个能力都要有入口、文档、触发词，否则 agent 根本激活不到。

这条测试锁的是「能力分层有没有落地」：`capabilities.json` 是 agent 每次会话
查得到的入口索引，`scripts/capabilities.py` 是它的校验器。两者任一漂移
（脚本改名、文档挪走、触发词删掉）都要在这里红。
"""

import json
from pathlib import Path

import pytest

import capabilities as cap

ROOT = Path(__file__).resolve().parents[2]


def _skill(root: Path, text: str = '# 假 skill\n\n阶段路由 能力分层 调研 获取 测试 发布\n'):
    (root / 'SKILL.md').write_text(text, encoding='utf-8')
    return root


def _doc(root: Path, rel: str = 'references/research.md'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('# 假文档\n', encoding='utf-8')
    return path


def _registry(items):
    return {'schema_version': 1, 'capabilities': items}


def _item(**kwargs):
    base = {'name': '字幕站调研', 'layer': '阶段', 'entry': '-',
            'doc': 'references/research.md', 'trigger': '调研'}
    base.update(kwargs)
    return base


def test_real_registry_activates_every_capability():
    data = cap.audit(ROOT)
    failed = [row['name'] for row in data['rows'] if not row['ok']]
    assert not failed, f'能力激活失败：{failed}'
    assert data['ok'] == data['total'] >= 20
    assert data['by_layer']['常驻'] >= 1
    assert data['by_layer']['阶段'] >= 1


def test_registry_file_is_valid_and_relative():
    data = cap.load_registry(ROOT)
    assert data['schema_version'] == 1
    for item in data['capabilities']:
        assert item['layer'] in cap.LAYERS
        for key in ('entry', 'doc'):
            value = item[key]
            assert value == '-' or ('://' not in value and not value.startswith('/'))


def test_missing_entry_is_reported(tmp_path):
    _skill(tmp_path)
    _doc(tmp_path)
    data = cap.audit(tmp_path, _registry([_item(entry='scripts/nope.py')]))
    row = data['rows'][0]
    assert not row['ok']
    assert any('入口缺失' in problem for problem in row['problems'])


def test_doc_must_be_routed_from_skill(tmp_path):
    _skill(tmp_path, '# 假 skill\n\n调研\n')
    _doc(tmp_path)
    data = cap.audit(tmp_path, _registry([_item()]))
    assert any('没被 SKILL 路由' in p for p in data['rows'][0]['problems'])

    _skill(tmp_path, '# 假 skill\n\n调研\n见 [research](references/research.md)\n')
    assert cap.audit(tmp_path, _registry([_item()]))['rows'][0]['ok']


def test_doc_inside_linked_directory_counts(tmp_path):
    _skill(tmp_path, '# 假 skill\n\n调研\n见 [design](references/design/)\n')
    _doc(tmp_path, 'references/design/oped.md')
    data = cap.audit(tmp_path, _registry([_item(doc='references/design/oped.md')]))
    assert data['rows'][0]['ok'], data['rows'][0]['problems']


def test_trigger_must_appear_in_skill(tmp_path):
    _skill(tmp_path, '# 假 skill\n\n阶段路由 调研\n')
    _doc(tmp_path)
    data = cap.audit(tmp_path, _registry([_item(trigger='从没写过的场景')]))
    assert any('触发词没进 SKILL' in p for p in data['rows'][0]['problems'])


def test_always_layer_must_be_named_in_skill(tmp_path):
    _skill(tmp_path, '# 假 skill\n\n阶段路由 调研 references/research.md\n')
    _doc(tmp_path)
    data = cap.audit(tmp_path, _registry([_item(name='编目', layer='常驻')]))
    assert any('常驻能力没写进 SKILL' in p for p in data['rows'][0]['problems'])


def test_directory_entry_is_allowed(tmp_path):
    _skill(tmp_path, '# 假 skill\n\n调研\n见 [research](references/research.md)\n')
    (tmp_path / 'tests').mkdir()
    _doc(tmp_path)
    data = cap.audit(tmp_path, _registry([_item(entry='tests')]))
    assert data['rows'][0]['ok'], data['rows'][0]['problems']


@pytest.mark.parametrize('bad', ['D:/x.py', '/abs/x.py', 'scripts\\x.py'])
def test_machine_paths_are_rejected(bad):
    with pytest.raises(cap.RegistryError):
        cap.validate_registry(_registry([_item(entry=bad)]))


def test_unknown_field_and_duplicate_name_are_rejected():
    with pytest.raises(cap.RegistryError):
        cap.validate_registry(_registry([_item(triger='拼错')]))
    with pytest.raises(cap.RegistryError):
        cap.validate_registry(_registry([_item(), _item()]))


def test_report_counts_add_up(tmp_path):
    _skill(tmp_path)
    _doc(tmp_path)
    (tmp_path / 'tests').mkdir()
    registry = _registry([
        _item(name='阶段路由', layer='常驻', doc='SKILL.md', trigger='阶段路由'),
        _item(entry='tests'),
        _item(name='发布', layer='交付前', doc='SKILL.md', trigger='发布'),
    ])
    data = cap.audit(tmp_path, registry)
    assert data['by_layer'] == {'常驻': 1, '阶段': 1, '交付前': 1}
    assert sum(data['by_layer'].values()) == data['total'] == 3
    assert json.loads(json.dumps(data, ensure_ascii=False))['total'] == 3
