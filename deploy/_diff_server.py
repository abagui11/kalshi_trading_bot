#!/usr/bin/env python3
"""Compare local tree against md5sums captured from the VPS app dir.

Usage: python deploy/_diff_server.py <md5sum-file>

The VPS copy of /opt/kalshi-15m-bot is rsync-deployed, not a git checkout, so
"what is actually running" can only be established by hashing it. Anything this
prints as CHANGED is a file a deploy would overwrite on a live trading box.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    src = Path(sys.argv[1])
    server: dict[str, str] = {}
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            server[parts[1].lstrip("./")] = parts[0]

    same, changed, only_local, only_server = [], [], [], []
    local: dict[str, str] = {}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "_boxdeploy/")) or "__pycache__" in rel:
            continue
        local[rel] = md5(p)

    for rel, h in sorted(local.items()):
        if rel not in server:
            only_local.append(rel)
        elif server[rel] != h:
            changed.append(rel)
        else:
            same.append(rel)
    for rel in sorted(server):
        if rel not in local:
            only_server.append(rel)

    print(f"identical: {len(same)}")
    print(f"\nCHANGED on server vs local ({len(changed)}):")
    for rel in changed:
        print(f"  M {rel}")
    print(f"\nlocal only ({len(only_local)}):")
    for rel in only_local:
        print(f"  + {rel}")
    print(f"\nserver only ({len(only_server)}):")
    for rel in only_server:
        print(f"  - {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
