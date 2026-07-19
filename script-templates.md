# 字幕校对脚本模板库

> 每类问题对应一个修复脚本模板。使用时读取对应章节，按当前项目的路径和参数调整后直接生成。

---

## 目录

1. [OP/ED 时间码匹配替换](#1-oped-时间码匹配替换)
2. [双语混合正则清理](#2-双语混合正则清理)
3. [Name 字段批量映射](#3-name-字段批量映射)
4. [感叹词/语气词替换](#4-感叹词语气词替换)
5. [专有名词全局统一](#5-专有名词全局统一)
6. [删除指定样式行](#6-删除指定样式行)
7. [绘图指令修复](#7-绘图指令修复)
8. [纯源语言行处理](#8-纯源语言行处理)
9. [机翻卡死重复检测与修复](#9-机翻卡死重复检测与修复)
10. [乱码模式批量替换](#10-乱码模式批量替换)
11. [固定格式统一](#11-固定格式统一)
12. [源语言字符残留扫描](#12-源语言字符残留扫描)
13. [批量并行精读框架](#13-批量并行精读框架)

---

## 1. OP/ED 时间码匹配替换

**问题**：OP/ED 歌词轨为严重机翻乱码，但同一文件内另有正确翻译轨。

**脚本模板**：

```python
import os, glob

TARGET_DIR = '[目标字幕目录]'
SOURCE_STYLE = 'Opening Romaji'   # 乱码轨的样式名
REF_STYLE = 'Opening Rus'         # 正确翻译轨的样式名
MATCH_TOLERANCE_MS = 500           # 时间码匹配容差

def time_to_ms(t):
    """将 ASS 时间码转为毫秒"""
    parts = t.split(':')
    h, m = int(parts[0]), int(parts[1])
    s_parts = parts[2].split('.')
    s = int(s_parts[0])
    ms = int(s_parts[1].ljust(2, '0')[:2]) * 10
    return ((h * 60 + m) * 60 + s) * 1000 + ms

def parse_dialogue(line):
    """解析 Dialogue 行，返回 (parts_dict, raw_text)"""
    if not line.startswith('Dialogue:'):
        return None
    parts = line.strip().split(',', 9)
    return {
        'format': parts[0],
        'layer': parts[1],
        'start': parts[2],
        'end': parts[3],
        'style': parts[4],
        'name': parts[5],
        'margin_l': parts[6],
        'margin_r': parts[7],
        'margin_v': parts[8],
        'text': parts[9]
    }, parts

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 收集参考轨的时间码→文本映射
    ref_map = {}  # {start_ms: text}
    for line in lines:
        d = parse_dialogue(line)
        if d is None:
            continue
        info, _ = d
        if info['style'] == REF_STYLE:
            ref_map[time_to_ms(info['start'])] = info['text']
    
    # 匹配并替换
    fixed = 0
    for i, line in enumerate(lines):
        d = parse_dialogue(line)
        if d is None:
            continue
        info, raw = d
        if info['style'] != SOURCE_STYLE:
            continue
        
        start_ms = time_to_ms(info['start'])
        # 查找容差内的最佳匹配
        best = None
        for ref_ms in ref_map:
            if abs(start_ms - ref_ms) <= MATCH_TOLERANCE_MS:
                if best is None or abs(start_ms - ref_ms) < abs(start_ms - best):
                    best = ref_ms
        
        if best is not None:
            new_text = ref_map[best]
            old_text = info['text']
            if old_text != new_text:
                # 保留原行格式，只替换文本
                new_line = ','.join(raw[:9]) + ',' + new_text + '\n'
                lines[i] = new_line
                fixed += 1
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'{fname}: {fixed} lines fixed')
```

**关键点**：
- 500ms 容差处理字幕间微小时间偏移
- `split(',', 9)` 防止歌词中逗号破坏解析
- 保留原样式名不变，仅替换文本

---

## 2. 双语混合正则清理

**问题**：中文字幕后紧跟同义源语言文本，或句中夹杂源语言词。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'

def strip_ass_tags(text):
    """移除 ASS 内联标签 {xxx}"""
    return re.sub(r'\{[^}]*\}', '', text)

def clean_mixed_text(text):
    """清理双语混合"""
    # 分离并保留 ASS 标签
    tags = re.findall(r'\{[^}]*\}', text)
    visible = strip_ass_tags(text)
    
    original = visible
    
    # 模式1：句尾英文（中文。 English sentence.）
    p1 = re.compile(
        r"([一-鿿。！？，、；：）】」》\.\!\?])"
        r"\s+"
        r"([A-Za-z0-9\s,.\'!?;:\"\-！？。，、]{3,})"
        r"$"
    )
    visible = p1.sub(r"\1", visible)
    
    # 模式2：破折号后英文（——English）
    visible = re.sub(
        r"(——)\s*[A-Za-z0-9\s,.\'!?;:\"\-]{3,}$",
        r"\1",
        visible
    )
    
    # 模式3：句中中英夹杂（中文English中文）
    # 注意用 \s* 而非 \s+ 以处理无空格的情况
    visible = re.sub(
        r"\s*[A-Za-z][A-Za-z0-9\s,.\'!?;:\"\-]{2,}\s*(?=[一-鿿])",
        "",
        visible
    )
    
    if visible == original:
        return text  # 无变化
    
    # 重建文本（标签保持原位不恢复，简化处理）
    return visible

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    fixed = 0
    for i, line in enumerate(lines):
        if not line.startswith('Dialogue:'):
            continue
        parts = line.strip().split(',', 9)
        if len(parts) < 10:
            continue
        
        # 跳过绘图指令行
        if '\\p1' in parts[9]:
            continue
        
        style = parts[4]
        if style not in ['Default', 'DefaultTop', 'Episode']:  # 只处理对话样式
            continue
        
        new_text = clean_mixed_text(parts[9])
        if new_text != parts[9]:
            parts[9] = new_text
            lines[i] = ','.join(parts) + '\n'
            fixed += 1
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'{fname}: {fixed} lines cleaned')
```

**关键点**：
- **必须先 strip ASS 标签** `{...}`，否则标签阻断正则
- 用 `\s*` 而非 `\s+`：中英文间可能无空格（如 `okay.如`）
- Python 正则用**双引号 raw string** `r"..."`，避免 `\'` 在单引号 raw string 中被错误解析
- 跳过 `\p1` 绘图指令行和 Display 特效层

---

## 3. Name 字段批量映射

**问题**：ASS 的 Name 字段（逗号分隔第 4 项，索引 5）保留源语言原名。

**脚本模板**：

```python
import os

TARGET_DIR = '[目标字幕目录]'

# 源语言 → 目标语言 Name 字段映射表
# 从参考字幕中提取，对照确认后填入
NAME_MAP = {
    '源语言名1': '目标语言名1',
    '源语言名2': '目标语言名2',
    # ... 逐步补充
}

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    fixed = 0
    for i, line in enumerate(lines):
        if not line.startswith('Dialogue:'):
            continue
        parts = line.strip().split(',', 9)
        if len(parts) < 10:
            continue
        
        name = parts[5]
        if name in NAME_MAP:
            parts[5] = NAME_MAP[name]
            lines[i] = ','.join(parts) + '\n'
            fixed += 1
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'{fname}: {fixed} names translated')
```

**如何收集 Name 映射**：
```python
# 先用此脚本扫描全部文件中出现的 Name 字段
names = set()
for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    with open(os.path.join(TARGET_DIR, fname), 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Dialogue:'):
                parts = line.strip().split(',', 9)
                if len(parts) >= 6 and parts[5]:
                    names.add(parts[5])
for n in sorted(names):
    print(f"    '{n}': '',")
```

---

## 4. 感叹词/语气词替换

**问题**：对话中遗留源语言短感叹词。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'

# 源语言感叹词 → 目标语言 （只替换对话样式的可见文本）
INTERJECTION_MAP = [
    # (源语言词, 目标语言词, 说明)
    # 示例：
    # ('А\\?', '啊？', '疑问'),
    # ('Э\\?', '诶？', '疑问'),
    # ('Я!', '我！', '强调'),
    # ('Ну', '好啦', '语气'),
    # ('Вот', '瞧', '指示'),
    # ('Да', '是的', '肯定'),
    # ('Ой', '哎哟', '惊呼'),
]

STYLES_TO_FIX = ['Default', 'DefaultTop', 'Episode', 'DefaultTop2']

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    fixed = 0
    for i, line in enumerate(lines):
        if not line.startswith('Dialogue:'):
            continue
        parts = line.strip().split(',', 9)
        if len(parts) < 10:
            continue
        if parts[4] not in STYLES_TO_FIX:
            continue
        
        text = parts[9]
        for src, tgt, _ in INTERJECTION_MAP:
            # 替换独立出现的感叹词（非单词的一部分）
            new_text = re.sub(rf'\b{src}\b', tgt, text)
            if new_text != text:
                text = new_text
        
        if text != parts[9]:
            parts[9] = text
            lines[i] = ','.join(parts) + '\n'
            fixed += 1
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'{fname}: {fixed} lines')
```

---

## 5. 专有名词全局统一

**问题**：同一节目名/角色名/术语在不同集中有不同译名。

**脚本模板**：

```python
import os

TARGET_DIR = '[目标字幕目录]'

# 名称统一映射表（所有变体 → 标准译名）
UNIFY_MAP = {
    # 示例：
    # '变体A': '标准名',
    # '变体B': '标准名',
    # '变体C': '标准名',
}

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    fixed = 0
    for variant, standard in UNIFY_MAP.items():
        count = content.count(variant)
        if count > 0:
            content = content.replace(variant, standard)
            fixed += count
    
    if fixed > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
    print(f'{fname}: {fixed} replacements')
```

**注意**：先用 Grep 扫描全部文件收集所有变体，人工确认标准译名后再填入映射表。

---

## 6. 删除指定样式行

**问题**：译者署名、发布信息等特定样式的行需全部删除。

**脚本模板**：

```python
import os

TARGET_DIR = '[目标字幕目录]'
STYLES_TO_DELETE = ['Roboto']  # 要删除的样式名列表
COMMENT_PATTERNS = [           # 要删除的 Comment 行关键词
    'Редактура',
    'перевод',
    'Translated by',
]

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    deleted = 0
    cleaned = []
    for line in lines:
        # 删除指定样式的 Dialogue 行
        if line.startswith('Dialogue:'):
            parts = line.strip().split(',', 9)
            if parts[4] in STYLES_TO_DELETE:
                deleted += 1
                continue
        
        # 删除含特定关键词的 Comment 行
        if line.startswith('Comment:'):
            parts = line.strip().split(',', 9)
            if len(parts) >= 10:
                if any(p in parts[9] for p in COMMENT_PATTERNS):
                    deleted += 1
                    continue
        
        cleaned.append(line)
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(cleaned)
    print(f'{fname}: {deleted} lines deleted')
```

---

## 7. 绘图指令修复

**问题**：ASS 矢量绘图命令被机翻按字面翻译。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'

# 绘图指令映射（机翻乱码 → 正确指令）
DRAWING_FIXES = {
    '男': 'm',   # move 被译为 male 的缩写
    # 可按需扩展
}

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    fixed = 0
    for i, line in enumerate(lines):
        if not line.startswith('Dialogue:'):
            continue
        parts = line.strip().split(',', 9)
        if len(parts) < 10:
            continue
        
        # 只处理含绘图标签的行
        if '\\p1' not in parts[9] and '\\p0' not in parts[9]:
            continue
        
        text = parts[9]
        for garbled, correct in DRAWING_FIXES.items():
            new_text = text.replace(garbled, correct)
            if new_text != text:
                text = new_text
        
        if text != parts[9]:
            parts[9] = text
            lines[i] = ','.join(parts) + '\n'
            fixed += 1
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'{fname}: {fixed} lines fixed')
```

---

## 8. 纯源语言行处理

**问题**：存在整行纯源语言（无目标语言）的对话行。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'
STYLES_TO_CHECK = ['Default', 'DefaultTop', 'Episode']

def contains_target_lang(text):
    """检测文本是否含目标语言字符"""
    # 中文为例
    return bool(re.search(r'[一-鿿]', text))

def strip_ass_tags(text):
    return re.sub(r'\{[^}]*\}', '', text)

# 已知翻译映射表（人工补充）
KNOWN_TRANSLATIONS = {
    "I'm sorry.": '对不起。',
    "Come on!": '来吧！',
    "Here.": '给你。',
    "What?": '什么？',
    "Yes.": '是的。',
    "No.": '不。',
    "Wait.": '等一下。',
    "Help!": '救命！',
}

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 收集上下文（用于判断是否可以安全删除）
    all_texts = []
    for line in lines:
        if line.startswith('Dialogue:'):
            parts = line.strip().split(',', 9)
            if len(parts) >= 10:
                all_texts.append(strip_ass_tags(parts[9]))
    
    fixed = 0
    for i, line in enumerate(lines):
        if not line.startswith('Dialogue:'):
            continue
        parts = line.strip().split(',', 9)
        if len(parts) < 10:
            continue
        if parts[4] not in STYLES_TO_CHECK:
            continue
        
        visible = strip_ass_tags(parts[9])
        
        # 跳过空行、绘图指令
        if not visible.strip() or '\\p1' in parts[9]:
            continue
        
        # 跳过含目标语言的行
        if contains_target_lang(visible):
            continue
        
        # 纯源语言行
        if visible in KNOWN_TRANSLATIONS:
            # 翻译
            parts[9] = KNOWN_TRANSLATIONS[visible]
            lines[i] = ','.join(parts) + '\n'
            fixed += 1
        elif any(visible in t for t in all_texts):
            # 上下文中有对应翻译 → 删除
            lines[i] = ''
            fixed += 1
        else:
            # 无对应翻译 → 标记待处理
            print(f'  TODO [{fname}:{i+1}]: {visible[:50]}')
    
    # 过滤掉标记为空的行
    lines = [l for l in lines if l != '']
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'{fname}: {fixed} lines handled')
```

---

## 9. 机翻卡死重复检测与修复

**问题**：MT 程序卡住，同一序列机械重复。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'
STYLES_TO_CHECK = ['Default', 'DefaultTop', 'Episode']
MIN_REPEATS = 8  # 触发阈值

def strip_ass_tags(text):
    return re.sub(r'\{[^}]*\}', '', text)

# === 第一步：检测 ===
print('=== 检测重复序列 ===')
findings = []
for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    with open(fpath, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if not line.startswith('Dialogue:'):
                continue
            parts = line.strip().split(',', 9)
            if len(parts) < 10:
                continue
            if parts[4] not in STYLES_TO_CHECK:
                continue
            
            visible = strip_ass_tags(parts[9])
            if not visible.strip():
                continue
            
            for clen in [2, 3, 4]:
                for m in re.finditer(rf'(.{{{clen}}})\\1{{{MIN_REPEATS - 1},}}', visible):
                    seq = m.group(1)
                    full = m.group(0)
                    count = len(full) // clen
                    
                    # 排除已知非错误模式
                    skip_patterns = ['Pa-', 'La-', 'Me-', 'me-', 'Ta-', 'ta-']
                    if any(seq.startswith(p) for p in skip_patterns):
                        continue
                    
                    findings.append({
                        'file': fname,
                        'line': i,
                        'seq': seq,
                        'count': count,
                        'full': full[:80],
                        'timecode': parts[2]
                    })
                    print(f'{fname}:{i} [{seq}] x{count} | {full[:60]}...')
                    break
                else:
                    continue
                break

print(f'\n共发现 {len(findings)} 处重复')

# === 第二步：对照参考字幕修复 ===
# 对每个 finding，查找参考字幕中同一时间码的文本
# 人工确认正确文本后填入 REPLACEMENTS

REPLACEMENTS = {
    # '文件名': ('旧重复文本片段', '新文本'),
    # 示例：
    # 'Episode 006.ass': (
    #     '红尘，红尘，红尘，红尘...',
    #     '红粉，红粉，红粉，瞬间变变变...'
    # ),
}

for fname, (old_part, new_text) in REPLACEMENTS.items():
    for fname2 in sorted(os.listdir(TARGET_DIR)):
        if fname2 != fname:
            continue
        fpath = os.path.join(TARGET_DIR, fname2)
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        if old_part in content:
            # 找到包含 old_part 的完整行，替换整行文本
            for line in content.split('\n'):
                if old_part in line and line.startswith('Dialogue:'):
                    parts = line.split(',', 9)
                    parts[9] = new_text
                    new_line = ','.join(parts)
                    content = content.replace(line, new_line)
                    break
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f'Fixed {fname}: {old_part[:30]}... → {new_text}')
```

---

## 10. 乱码模式批量替换

**问题**：发现特定的翻译乱码词，需全局替换。

**脚本模板**：

```python
import os

TARGET_DIR = '[目标字幕目录]'

# 乱码 → 正确 映射表（人工逐条确认后填入）
FIX_MAP = [
    # ('乱码/错误翻译', '正确翻译', '说明'),
    # 示例：
    # ('安倍晋三', '阿部', '人名幻觉'),
    # ('龟仙人', '古美', '人名幻觉'),
    # ('修女', '姐姐', '假朋友：сестричка=sister非nun'),
    # ('去你妈的', '活该', '脏话误译'),
]

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    total = 0
    for old, new, reason in FIX_MAP:
        count = content.count(old)
        if count > 0:
            content = content.replace(old, new)
            total += count
            print(f'  {old} → {new}: {count} ({reason})')
    
    if total > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
    print(f'{fname}: {total} replacements')
```

---

## 11. 固定格式统一

**问题**：预告标题、结尾语等固定短语有多种变体。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'

# 格式统一映射表
UNIFY_MAP = [
    # (旧变体列表, 标准格式, '类别'),
    # 示例：
    # (
    #     ['下一个系列', '在下一集中', '下一集预告', '下集，预告'],
    #     '下集预告',
    #     '预告标题'
    # ),
    # (
    #     ['千万不要错过', '请欣赏', '敬请收看', '敬请观赏', '不要错过'],
    #     '敬请期待',
    #     '结尾语'
    # ),
]

for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    total = 0
    for variants, standard, category in UNIFY_MAP:
        for v in variants:
            count = content.count(v)
            if count > 0:
                content = content.replace(v, standard)
                total += count
    
    if total > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
    print(f'{fname}: {total} unifications')
```

---

## 12. 源语言字符残留扫描

**问题**：对话中可能残留源语言字符，需扫描定位。

**脚本模板**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'
STYLES_TO_CHECK = ['Default', 'DefaultTop', 'Episode']

# 源语言字符范围（以俄语为例）
SOURCE_CHAR_PATTERN = re.compile(r'[А-Яа-яЁё]')

def strip_ass_tags(text):
    return re.sub(r'\{[^}]*\}', '', text)

print('=== 扫描源语言字符残留 ===')
total = 0
for fname in sorted(os.listdir(TARGET_DIR)):
    if not fname.endswith('.ass'):
        continue
    fpath = os.path.join(TARGET_DIR, fname)
    
    with open(fpath, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if not line.startswith('Dialogue:'):
                continue
            parts = line.strip().split(',', 9)
            if len(parts) < 10:
                continue
            if parts[4] not in STYLES_TO_CHECK:
                continue
            
            visible = strip_ass_tags(parts[9])
            matches = SOURCE_CHAR_PATTERN.findall(visible)
            if matches:
                print(f'{fname}:{i} [{parts[2]}] {"".join(matches)} | {visible[:60]}')
                total += 1

print(f'\n共 {total} 行含源语言字符')
```

---

## 13. 批量并行精读框架

**问题**：需对全部剧集进行精读，但单进程太慢。

**框架说明**：

1. **分批**：将全部文件按 ~18 集/批分成 N 批
2. **并行启动**：每批一个子代理，同时运行
3. **统一审查**：所有子代理完成后，主进程合并结果

**子代理 Prompt 模板**：

```
你是字幕校对专家。请对照参考字幕逐行精读以下剧集：

目标文件（机翻中文）: {目标目录}/Mahou Tsukai Sally {集号范围}.ass
参考文件（人工翻译）: {参考目录}/Mahou Tsukai Sally {集号范围}.ass

任务：
1. 逐行对比同一时间码的中文和参考字幕
2. 找出所有翻译错误、用词不当、机翻幻觉
3. 以 OLD → NEW 格式输出修复列表
4. 标注每条修复的原因

输出格式（每行一条）：
文件名 | 行号 | OLD文本 | NEW文本 | 原因
```

**主进程合并脚本**：

```python
import os, re

TARGET_DIR = '[目标字幕目录]'
BATCH_REPORTS_DIR = '[各批次报告汇总目录]'

# 1. 读取所有批次报告
all_fixes = []
for fname in os.listdir(BATCH_REPORTS_DIR):
    if not fname.endswith('.txt') and not fname.endswith('.md'):
        continue
    with open(os.path.join(BATCH_REPORTS_DIR, fname), 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '|' not in line or 'OLD' in line:
                continue
            # 格式: 文件名 | 行号 | OLD | NEW | 原因
            parts = line.split('|')
            if len(parts) >= 5:
                all_fixes.append({
                    'file': parts[0].strip(),
                    'line': parts[1].strip(),
                    'old': parts[2].strip(),
                    'new': parts[3].strip(),
                    'reason': parts[4].strip()
                })

print(f'共收集 {len(all_fixes)} 条修复建议')

# 2. 冲突检测
fixes_by_old = {}
for f in all_fixes:
    key = f['old']
    if key not in fixes_by_old:
        fixes_by_old[key] = []
    fixes_by_old[key].append(f)

# 同一 OLD 有不同 NEW → 冲突
conflicts = {k: v for k, v in fixes_by_old.items() 
             if len(set(x['new'] for x in v)) > 1}
if conflicts:
    print(f'\n⚠ 发现 {len(conflicts)} 处冲突，需人工裁定：')
    for old, fixes in conflicts.items():
        versions = set(f['new'] for f in fixes)
        print(f'  {old} → {versions}')

# 3. 去重合并
unique_fixes = {}
for f in all_fixes:
    key = (f['old'], f['new'])
    if key not in unique_fixes:
        unique_fixes[key] = f
        unique_fixes[key]['files'] = []
    unique_fixes[key]['files'].append(f['file'])

# 4. 分类：统一修改（≥3集）vs 单集
unified = [f for f in unique_fixes.values() if len(set(f['files'])) >= 3]
single = [f for f in unique_fixes.values() if len(set(f['files'])) < 3]

print(f'统一修改: {len(unified)} 条')
print(f'单集特定: {len(single)} 条')

# 5. 生成修复脚本
# 统一修改 → 用模板10（乱码模式批量替换）
# 单集特定 → 逐文件 Edit
```

**关键点**：
- 子代理 prompt 必须要求**结构化输出**（`|` 分隔格式）
- 主进程必须做**冲突检测**：同一 OLD 被不同批次建议为不同 NEW
- 特别关注节目名/角色名一致性：不同批次可能建议不同译名
- 合并后先打印修复清单供人工抽查，再执行
