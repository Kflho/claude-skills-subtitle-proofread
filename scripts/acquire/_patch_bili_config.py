"""Patch Bili23's config.json without ever introducing a UTF-8 BOM.

Bili23 parses its config with orjson, which rejects a BOM at byte 0; when it
hits one it treats the file as legacy, "patches" it, and resets cookies, the MCP
token and the download path.  PowerShell's ``Set-Content -Encoding UTF8`` adds
that BOM, so the edit has to happen at the byte level instead.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

CONFIG = pathlib.Path.home() / "AppData" / "Roaming" / "Bili23 Downloader" / "config.json"


def load(path: pathlib.Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SystemExit(f"{path} starts with a BOM; restore the backup first")
    return json.loads(raw.decode("utf-8"))


def set_values(path: pathlib.Path, updates: dict[str, object]) -> None:
    """Replace scalar values in place, preserving formatting and encoding."""
    text = path.read_text(encoding="utf-8")
    for key, value in updates.items():
        marker = f'"{key}": '
        start = text.find(marker)
        if start < 0:
            raise SystemExit(f"key not found: {key}")
        value_start = start + len(marker)
        value_end = value_start
        while value_end < len(text) and text[value_end] not in ",}\r\n":
            value_end += 1
        old = text[value_start:value_end].strip()
        text = text[:value_start] + str(value) + text[value_end:]
        print(f"  {key}: {old} -> {value}")
    path.write_bytes(text.encode("utf-8"))  # no BOM, by construction


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    backup = pathlib.Path(argv[1])
    if backup.is_file():
        shutil.copyfile(backup, CONFIG)
        print(f"restored {CONFIG.name} from {backup.name} "
              f"({CONFIG.stat().st_size} bytes)")
    before = load(CONFIG)
    print("before:", {"parallel": before["Download"].get("download_parallel"),
                      "threads": before["Download"].get("download_thread"),
                      "mcp": before.get("MCP", {}).get("mcp_enabled"),
                      "path": before["Download"].get("download_path")})
    print("patching:")
    set_values(CONFIG, {"download_parallel": 4, "download_thread": 8})
    after = load(CONFIG)
    print("after:", {"parallel": after["Download"].get("download_parallel"),
                     "threads": after["Download"].get("download_thread"),
                     "mcp": after.get("MCP", {}).get("mcp_enabled"),
                     "path": after["Download"].get("download_path"),
                     "logged_in_cookie": bool(after.get("Cookie", {}).get("SESSDATA"))})
    raw = CONFIG.read_bytes()
    print("BOM present:", raw.startswith(b"\xef\xbb\xbf"), "| size", len(raw))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
