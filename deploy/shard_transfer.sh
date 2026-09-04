#!/usr/bin/env bash
# Move collateral to the crypto shard (exchange_index=2).
# Kalshi sharded 2026-08-24: crypto trades on shard 2; collateral is per-shard.
set -eu
cd /opt/kalshi-15m-bot
.venv/bin/python - <<'PY'
import json
import kalshi_client

def shard_balances():
    resp = kalshi_client.request("GET", "/portfolio/balance", auth=True)
    return {
        int(b["exchange_index"]): float(b["balance"])
        for b in resp.get("balance_breakdown", [])
    }

before = shard_balances()
print("before:", json.dumps(before))

amount_usd = 70.00
if before.get(2, 0.0) >= amount_usd:
    print("shard 2 already funded — skipping transfer")
else:
    body = {
        "source": "event_contract",
        "destination": "event_contract",
        "amount": int(round(amount_usd * 10000)),  # centicents
        "source_exchange_shard": 0,
        "destination_exchange_shard": 2,
    }
    resp = kalshi_client.request(
        "POST", "/portfolio/intra_exchange_instance_transfer",
        json_body=body, auth=True,
    )
    print("transfer:", json.dumps(resp))

import time
time.sleep(5)
print("after:", json.dumps(shard_balances()))
PY
