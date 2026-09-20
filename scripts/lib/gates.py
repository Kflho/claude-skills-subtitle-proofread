#!/usr/bin/env python3
"""行为分级表 —— 哪些事默认做，哪些事等指令。

本项目的教训是：危险的不是「工具做错了」，而是**工具默认做对了它以为对的事**。
VAD 硬过滤丢真实台词、OP/ED 固定窗口吞对白、`--lang zh` 顺手改字形 —— 这些
都发生在「用户没说要做」的时候，且多数不报警。

所以每个会**写盘**的行为都要在这里登记一个级别：

    L0  默认必做，且只读（扫描、探测、校验、报告）
    L1  默认只检测并标注；要写盘必须显式开关
    L2  只在用户明确要求时执行（复用覆写、OP/ED 覆写、专名统一、润色、繁→简）
    L3  停下来问（删交付物、覆盖既有 .bak、整目录替换）

判据只有一条：**这一步会不会在用户没要求的情况下改变字幕内容？** 会 → 至少 L1。

只有 L1 是机器可判的：它的 ``unlock`` 必须是一个 ``--flag``，``enabled()``
靠它回答「用户点名要了吗」。L0 没有开关（只读，永远跑）；L2/L3 的 ``unlock``
是给人看的说明（可能是开关、文件，或「人工确认」），不由本模块裁决。

调用方用 ``enabled(key, args)`` 判断，别在各处重写开关名。
表本身在 run_all.py 启动时打印，用户每跑一次就能看到当前哪些闸门是开的。
"""

from dataclasses import dataclass, field

L0 = 'L0'
L1 = 'L1'
L2 = 'L2'
L3 = 'L3'

_LEVEL_DESC = {
    L0: '默认必做（只读）',
    L1: '默认只标注，写盘需开关',
    L2: '仅在明确要求时执行',
    L3: '执行前必须确认',
}


@dataclass(frozen=True)
class Gate:
    key: str            # 供 enabled() 查的键
    level: str          # L0/L1/L2/L3
    what: str           # 这一步做什么
    unlock: str         # 解锁写盘的开关；L0 写 '—'
    note: str = ''      # 为什么这么定级（通常是一条踩过的坑）
    flags: tuple = field(default=())   # 同义开关（旧名/别名）


GATES = (
    # ── L0：只读，默认必做 ──
    Gate('scan', L0, '扫描 / 编码探测 / 乱码分级', '—',
         '只读，不改字幕'),
    Gate('verify', L0, '交付校验（假名残留、专名、回显、时间轴、对数量）', '—',
         '只读。Step 4 必须跑，它是唯一能发现「整批没翻译」的关卡'),
    Gate('vad_detect', L0, 'VAD 台词↔人声匹配（含「有人声无字幕」缺口的报告）', '—',
         '只读。本作对白垫 BGM，VAD 判定只作提示。'
         '缺口只列清单——曾经会补新 cue，现已取消（凭空多出来的内容没人点头）'),

    # ── L1：默认检测，写盘要开关 ──
    Gate('vad_delete', L1, '按 VAD 删「无人声」条', '--vad-clean-apply',
         '踩坑 #3/#16：Silero 硬过滤单集丢 243/463 条真实台词；'
         '「はい」这类 2 假名真词会被判非台词。默认只出清单，不删'),

    # ── L2：只在明确要求时执行（都是「改写已有译文」）──
    Gate('fragment_escalate', L2, '按修复流程标 [???]（覆盖原文为标记）', '随修复流程执行',
         '跑 run_all / episode_workflow 本身就是「要求修复」；'
         '原文留在报告里，标记只写进 SRT'),
    Gate('oped_pre_replace', L2, 'OP/ED 预替换（用定本歌词覆写 cue）', '--skip-oped 反向关闭',
         '踩坑 #11：固定 180s 窗口吞掉正片 45 条对白且不报警。'
         '现由 _is_lyric() 三信号判定兜底，默认随翻译一起做'),
    Gate('noun_pre_replace', L2, '专名预替换', '--mappings 提供映射表',
         '不给 --mappings 就不替换'),
    Gate('reuse_tv', L2, '复用 TV 参考字幕覆写机译', 'reuse/apply.py --apply',
         '默认 dry-run。SP01 是总集篇才适用，SP02 一条都不该采纳'),
    Gate('polish', L2, 'LLM 润色（改写译文措辞）', 'polish_zh.py --apply',
         '润色改的是人已经认可的译文，必须显式发起'),
    Gate('trad_to_simp', L2, '繁→简转换', '--lang zh / --trad-to-simp',
         '整篇改字形。--lang zh 会自动带上，日志里有 [trad→simp] 一行'),
    Gate('apply_fixes', L2, '按 fixes.json 逐条改写/删条', '--fixes FILE',
         '没有 fixes.json 就什么都不做'),
    Gate('transcribe_overwrite', L2, '重跑转录（覆盖 temp/ja_raw/*.srt）',
         'whisper_batch_transcribe.py（显式发起）',
         '下游的翻译、复用、OP/ED 定本全建在这份转录上，重跑等于把它们全部作废。'
         '脚本已加 .bak，但「作废」这件事本身要人来决定'),

    # ── L3：停下问 ──
    Gate('overwrite_deliverable', L3, '覆盖已交付字幕（.ass / 定稿 SRT）', '人工确认',
         'temp/ 不受 git 管，没有撤销按钮'),
    Gate('overwrite_bak', L3, '覆盖既有 .bak', '人工确认',
         '.bak 是「最初那份」，被覆盖就再也回不去了'),
)

_BY_KEY = {g.key: g for g in GATES}
_BY_FLAG = {}
for _g in GATES:
    for _f in _g.flags:
        _BY_FLAG[_f] = _g.key


def get(key):
    """按 key 取 Gate；未知 key 抛 KeyError（拼错要立刻炸，别静默当成关）。"""
    return _BY_KEY[key]


def level_of(key):
    return _BY_KEY[key].level


def enabled(key, args=None):
    """这一步现在是否被允许写盘。

    L0/L2/L3 不由这里决定（L0 只读；L2/L3 由各自的开关或人工确认控制），
    这里只回答 L1 的问题：用户有没有显式点名要做？
    """
    gate = _BY_KEY[key]
    if gate.level != L1:
        return True
    unlock = gate.unlock
    if not unlock.startswith('--') or args is None:
        return False
    name = unlock.lstrip('-').replace('-', '_')
    return bool(getattr(args, name, False))


def format_table():
    """给人看的级别表（run_all.py 启动时打印）。"""
    lines = ['行为分级：', '']
    pad = max(len(g.key) for g in GATES) + 2
    for lvl in (L0, L1, L2, L3):
        group = [g for g in GATES if g.level == lvl]
        if not group:
            continue
        lines.append(f'  {lvl} — {_LEVEL_DESC[lvl]}')
        for g in group:
            lines.append(f'    {g.key:<{pad}} {g.what}'
                         f'{"" if g.unlock == "—" else f"  [{g.unlock}]"}')
        lines.append('')
    return '\n'.join(lines)
