#!/usr/bin/env python3
"""Merge Mullvad Speed Guard cumulative traffic totals without losing counts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def total(payload: Dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return int(value) if isinstance(value, int) and value >= 0 else 0


def merge(paths: List[Path]) -> Dict[str, Any]:
    payloads = [load(path) for path in paths]
    if not any(payloads):
        return {}

    newest_payload = max(
        ((path.stat().st_mtime if path.exists() else 0.0, payload) for path, payload in zip(paths, payloads)),
        key=lambda item: item[0],
    )[1]
    merged = dict(newest_payload)
    merged["initialized"] = True
    merged["total_download_bytes"] = max(total(payload, "total_download_bytes") for payload in payloads)
    merged["total_upload_bytes"] = max(total(payload, "total_upload_bytes") for payload in payloads)
    return merged


def save(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: sync_traffic_totals.py PATH PATH [PATH...]", file=sys.stderr)
        return 2
    paths = [Path(arg).expanduser() for arg in sys.argv[1:]]
    merged = merge(paths)
    if not merged:
        return 0
    for path in paths:
        save(path, merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
