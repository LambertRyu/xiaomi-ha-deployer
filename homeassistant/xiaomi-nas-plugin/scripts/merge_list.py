#!/usr/bin/env python3
"""Safely add or replace one Xiaomi NAS .list entry.

The script only writes when --apply is supplied. It refuses to replace an
existing entry unless --replace is also supplied, and writes atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", dest="list_path", required=True, type=Path)
    parser.add_argument("--entry", required=True, type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    args = parse_args()
    if not args.list_path.is_file():
        raise FileNotFoundError(f"existing .list not found: {args.list_path}")

    current = load_object(args.list_path)
    entry = load_object(args.entry)
    if set(entry) != {"homeassistant"}:
        raise ValueError("entry file must contain exactly the homeassistant key")
    if "homeassistant" in current and not args.replace:
        raise ValueError(
            "homeassistant already exists; rerun with --replace after reviewing it"
        )

    updated = dict(current)
    updated["homeassistant"] = entry["homeassistant"]
    rendered = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"

    if not args.apply:
        print(f"dry-run: would update {args.list_path}")
        print(f"dry-run: keys before={len(current)} after={len(updated)}")
        return 0

    if args.backup is not None:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.list_path, args.backup)
        print(f"backup: {args.backup}")

    mode = args.list_path.stat().st_mode
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{args.list_path.name}.",
        suffix=".tmp",
        dir=args.list_path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, args.list_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    print(f"updated: {args.list_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
