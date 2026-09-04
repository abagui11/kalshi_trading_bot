#!/usr/bin/env bash
# Scale the eva_wick bankroll and move matching collateral to the crypto shard.
# Usage: BANKROLL=450 bash deploy/set_bankroll.sh
#
# Three settings must move together or the smallest one silently becomes the
# real limit: the bankroll (drives deploy %), the notional ceiling (must clear
# KALSHI_MAX_DEPLOY_PCT x bankroll), and the contract cap.
set -eu
BANKROLL="${BANKROLL:-450}"
ENV=/opt/kalshi-15m-bot/.env
cd /opt/kalshi-15m-bot

python3 - "$BANKROLL" <<'PY'
import sys
bankroll = float(sys.argv[1])
env = "/opt/kalshi-15m-bot/.env"
# Notional ceiling clears max-deploy so deploy% stays the real limiter;
# contract cap sits inside observed book depth near mid.
updates = {
    "KALSHI_BANKROLL_USD": f"{bankroll:.2f}",
    "KALSHI_MAX_NOTIONAL_USD": f"{bankroll * 0.155:.2f}",
    "KALSHI_MAX_CONTRACTS": "300",
}
lines = open(env).read().splitlines()
seen = set()
for i, ln in enumerate(lines):
    key = ln.split("=", 1)[0].strip()
    if key in updates:
        lines[i] = f"{key}={updates[key]}"
        seen.add(key)
for key, val in updates.items():
    if key not in seen:
        lines.append(f"{key}={val}")
open(env, "w").write("\n".join(lines) + "\n")
print("set:", updates)
PY

echo "=== moving collateral to the crypto shard ==="
.venv/bin/python - "$BANKROLL" <<'PY'
import sys, time, json
import kalshi_client as k

target = float(sys.argv[1])
CRYPTO = 2

def shards():
    b = k.request("GET", "/portfolio/balance", auth=True)
    return {int(x["exchange_index"]): float(x["balance"]) for x in b["balance_breakdown"]}

before = shards()
print("before:", json.dumps(before))
have = before.get(CRYPTO, 0.0)
need = target - have
if need <= 1.0:
    print(f"shard {CRYPTO} already holds ${have:.2f} — no transfer")
else:
    move = min(need, before.get(0, 0.0))
    resp = k.request(
        "POST", "/portfolio/intra_exchange_instance_transfer",
        json_body={
            "source": "event_contract", "destination": "event_contract",
            "amount": int(round(move * 10000)),  # centicents
            "source_exchange_shard": 0, "destination_exchange_shard": CRYPTO,
        }, auth=True,
    )
    print(f"transferred ${move:.2f} -> shard {CRYPTO}:", resp)
    time.sleep(5)
    print("after:", json.dumps(shards()))
PY

systemctl restart kalshi-bot
sleep 6
systemctl is-active kalshi-bot
grep -E 'BANKROLL|MAX_NOTIONAL|MAX_CONTRACTS|DEPLOY_PCT' "$ENV" | grep -v '^#'
