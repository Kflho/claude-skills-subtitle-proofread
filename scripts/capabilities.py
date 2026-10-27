#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capabilities.py — 能力清单校验：每个能力都有入口、有文档、能被触发词激活。

为什么要列清单：agent 常常忘了某个能力存在。`capabilities.json` 把每个能力登记成
「层 / 入口 / 文档 / 触发词」四项，这个脚本只查**能不能被激活**：

- 入口（脚本或目录）在仓库里存在；
- 文档存在，并且被 `SKILL.md` 路由过（否则 agent 读不到）；
- 触发词至少有一个出现在 `SKILL.md`（阶段路由 / 常驻层），说明场景能对上；
- 常驻能力必须在 `SKILL.md` 里被点名，否则每次会话都会漏掉。

用法:
  python scripts/capabilities.py --report        # 矩阵 + 覆盖率
  python scripts/capabilities.py --report --json # 机器可读
  python scripts/capabilities.py --check         # 有失效项 → 退出码 2

退出码：0 全过；2 有失效项；1 清单本身不合法（schema / 绝对路径 / 重名）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_NAME = "capabilities.json"
ALWAYS, STAGE, DELIVERY = "常驻", "阶段", "交付前"
LAYERS = (ALWAYS, STAGE, DELIVERY)
FIELDS = {"name", "layer", "entry", "doc", "trigger", "note"}
PUNCT = re.compile(r"[、，,。；;：:/\s（）()【】\[\]「」]+")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


class RegistryError(ValueError):
    """清单本身不合法（不是能力失效，是文件写错了）。"""


def load_registry(root: Path) -> dict:
    path = root / REGISTRY_NAME
    if not path.is_file():
        raise RegistryError(f"找不到 {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{REGISTRY_NAME} 不是合法 JSON：{exc}") from exc
    validate_registry(data)
    return data


def validate_registry(data: dict) -> None:
    if data.get("schema_version") != 1:
        raise RegistryError("schema_version 必须是 1")
    items = data.get("capabilities")
    if not isinstance(items, list) or not items:
        raise RegistryError("capabilities 必须是非空列表")
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise RegistryError(f"第 {index} 项不是对象")
        unknown = set(item) - FIELDS
        if unknown:
            raise RegistryError(f"第 {index} 项有未知字段：{', '.join(sorted(unknown))}")
        for key in ("name", "layer", "entry", "doc", "trigger"):
            if not str(item.get(key, "")).strip():
                raise RegistryError(f"第 {index} 项缺字段：{key}")
        if item["layer"] not in LAYERS:
            raise RegistryError(f"第 {index} 项 layer 不是 {LAYERS}：{item['layer']}")
        name = item["name"].strip()
        if name in seen:
            raise RegistryError(f"能力重名：{name}")
        seen.add(name)
        for key in ("entry", "doc"):
            value = item[key].strip()
            if value == "-":
                continue
            if DRIVE.match(value) or value.startswith(("/", "\\")) or "\\" in value:
                raise RegistryError(f"{name} 的 {key} 必须是仓库内相对路径（/ 分隔）：{value}")


def skill_text(root: Path) -> str:
    path = root / "SKILL.md"
    return path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""


def trigger_tokens(trigger: str) -> list[str]:
    return [token for token in PUNCT.split(trigger) if len(token) >= 2]


def doc_is_routed(text: str, doc: str) -> bool:
    """SKILL 里直接链接了这份文档，或链接了它所在目录。"""
    if doc in text:
        return True
    if "/" not in doc:
        return False
    parent = doc.rsplit("/", 1)[0] + "/"
    return parent in text


def audit(root: Path, registry: dict | None = None) -> dict:
    registry = registry if registry is not None else load_registry(root)
    text = skill_text(root)
    rows = []
    for item in registry["capabilities"]:
        problems: list[str] = []
        entry = item["entry"].strip()
        doc = item["doc"].strip()
        if entry != "-" and not (root / entry).exists():
            problems.append(f"入口缺失：{entry}")
        if doc != "SKILL.md":
            if not (root / doc).is_file():
                problems.append(f"文档缺失：{doc}")
            elif not doc_is_routed(text, doc):
                problems.append(f"文档没被 SKILL 路由：{doc}")
        tokens = trigger_tokens(item["trigger"])
        if not tokens:
            problems.append(f"触发词太短：{item['trigger']}")
        elif not any(token in text for token in tokens):
            problems.append(f"触发词没进 SKILL：{item['trigger']}")
        if item["layer"] == ALWAYS:
            names = [item["name"].strip(), Path(entry).name if entry != "-" else ""]
            if not any(name and name in text for name in names):
                problems.append("常驻能力没写进 SKILL（每次会话都会漏）")
        rows.append({
            "name": item["name"].strip(),
            "layer": item["layer"],
            "entry": entry,
            "doc": doc,
            "trigger": item["trigger"].strip(),
            "problems": problems,
            "ok": not problems,
        })
    by_layer = {layer: sum(1 for row in rows if row["layer"] == layer) for layer in LAYERS}
    ok_count = sum(1 for row in rows if row["ok"])
    return {
        "total": len(rows),
        "ok": ok_count,
        "coverage": round(ok_count / len(rows), 3) if rows else 0.0,
        "by_layer": by_layer,
        "rows": rows,
    }


def print_report(data: dict) -> None:
    print(f"能力 {data['total']} 项：常驻 {data['by_layer'][ALWAYS]} / "
          f"阶段 {data['by_layer'][STAGE]} / 交付前 {data['by_layer'][DELIVERY]}；"
          f"可激活 {data['ok']} 项（{data['coverage']:.0%}）")
    print(f"{'能力':<22}{'层':<6}{'入口':<40}{'状态'}")
    for row in data["rows"]:
        state = "OK" if row["ok"] else "FAIL"
        print(f"{row['name']:<22}{row['layer']:<6}{row['entry']:<40}{state}")
        for problem in row["problems"]:
            print(f"    → {problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="能力清单校验（入口 / 文档 / 触发词）")
    parser.add_argument("--report", action="store_true", help="打印能力矩阵与覆盖率")
    parser.add_argument("--check", action="store_true", help="有失效项时退出码 2")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--root", default=str(ROOT), help="skill 根目录（默认本仓库）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        data = audit(root)
    except RegistryError as exc:
        print(f"清单不合法：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"root": str(root), **data}, ensure_ascii=False, indent=2))
    else:
        print_report(data)
        if args.check and data["ok"] != data["total"]:
            print(f"\n{data['total'] - data['ok']} 项失效：补入口、文档或触发词后重跑。")
    failed = data["ok"] != data["total"]
    return 2 if failed and (args.check or args.json) else 0


if __name__ == "__main__":
    raise SystemExit(main())
