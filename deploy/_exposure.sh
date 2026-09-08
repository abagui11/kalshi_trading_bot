#!/usr/bin/env bash
set -eu
cd /opt/kalshi-15m-bot
.venv/bin/python - <<'PY'
import json
import kalshi_client as k

CRYPTO = 2
print("=== raw positions payload (shard-scoped) ===")
pos = k.request("GET", "/portfolio/positions",
                params={"exchange_index": CRYPTO}, auth=True)
print(json.dumps(pos, indent=1)[:2000])
PY
