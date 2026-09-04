#!/usr/bin/env bash
# Point the eva_wick paper book at shard-2 cash so the ledger can open
# and the hub tab matches the account. Does not wipe trade history.
set -eu
cd /opt/kalshi-15m-bot
START="${LIVE_STARTING_USD:-450}"
.venv/bin/python - "$START" <<'PY'
import sys
import kalshi_client as k
import paper

start = float(sys.argv[1])
CRYPTO = 2
bal = k.request("GET", "/portfolio/balance", auth=True)
shards = {int(b["exchange_index"]): float(b["balance"]) for b in bal["balance_breakdown"]}
cash = shards.get(CRYPTO, 0.0)
print(f"shard {CRYPTO} cash ${cash:.4f}; setting eva_wick start=${start:.2f} realized=${cash-start:+.2f}")
paper.sync_live_cash(cash, starting_usd=start, bot_id="eva_wick")
s = paper.get_stats(bot_id="eva_wick")
print(f"book now: start ${s['starting_usd']:.2f} cash ${s.get('cash_usd', s.get('equity_usd')):.2f} "
      f"equity ${s['equity_usd']:.2f} realized ${s['realized_pnl_usd']:+.2f}")
PY
