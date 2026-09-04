#!/usr/bin/env bash
# Reconcile the eva_wick ledger against the real Kalshi account (run on the VPS).
# Crypto lives on exchange shard 2 — portfolio reads MUST be scoped to it, or
# positions come back empty and balance comes back as a cross-shard aggregate.
set -eu
cd /opt/kalshi-15m-bot

echo "=== ledger: recent positions ==="
sqlite3 -header -column ledger.db "SELECT id, opened_at, closed_at, market_ticker, side, contracts, entry_cents, status, result, round(pnl_usd,3) pnl FROM paper_positions ORDER BY id DESC LIMIT 12;"
echo
echo "=== ledger: book state ==="
sqlite3 -header -column ledger.db "SELECT bot_id, round(cash_usd,2) cash, round(realized_pnl_usd,2) realized FROM paper_state;"
echo
.venv/bin/python - <<'PY'
import json
import kalshi_client as k

CRYPTO_SHARD = 2

bal = k.request("GET", "/portfolio/balance", auth=True)
shards = {int(b["exchange_index"]): float(b["balance"]) for b in bal.get("balance_breakdown", [])}
print("=== balance by shard ===")
print(json.dumps(shards, indent=1))
print(f"crypto shard {CRYPTO_SHARD}: ${shards.get(CRYPTO_SHARD, 0.0):.4f}")

print("\n=== open positions (shard-scoped) ===")
pos = k.request(
    "GET", "/portfolio/positions",
    params={"exchange_index": CRYPTO_SHARD}, auth=True,
)
rows = [p for p in pos.get("market_positions", []) if p.get("position")]
print(json.dumps(rows, indent=1) if rows else "(none)")

print("\n=== recent fills ===")
fills = k.request(
    "GET", "/portfolio/fills",
    params={"exchange_index": CRYPTO_SHARD, "limit": 10}, auth=True,
)
for f in fills.get("fills", [])[:10]:
    print(
        f"{f.get('created_time')} {f.get('ticker')} {f.get('side')} "
        f"{f.get('action')} @ {f.get('yes_price_dollars')} taker={f.get('is_taker')}"
    )
PY
