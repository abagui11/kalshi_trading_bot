#!/usr/bin/env bash
# 2026-09-08 flip: eva_streak = live main book (mid entry), eva_wick = paper
# control. Run ON the VPS after the new code is synced:
#   bash deploy/flip_streak_live.sh
set -eu
cd /opt/kalshi-15m-bot

echo "== 1) env: KALSHI_LIVE_BOTS=eva_streak =="
grep -q '^KALSHI_LIVE_BOTS=' .env \
  && sed -i 's/^KALSHI_LIVE_BOTS=.*/KALSHI_LIVE_BOTS=eva_streak/' .env \
  || echo 'KALSHI_LIVE_BOTS=eva_streak' >> .env
grep -E 'ENABLED_BOTS|KALSHI_PAPER_ONLY|KALSHI_LIVE_BOTS|KALSHI_MAX_CONTRACTS' .env

echo "== 2) cancel leftover eva_streak paper limit orders =="
sqlite3 ledger.db "UPDATE paper_orders SET status='cancelled' WHERE status='pending' AND bot_id='eva_streak';"
sqlite3 ledger.db "SELECT COUNT(1) || ' pending orders remain' FROM paper_orders WHERE status='pending';"

echo "== 3) books: streak -> shard-2 cash (fresh start), wick -> clean paper baseline =="
.venv/bin/python - <<'PY'
import kalshi_client as k
import paper

CRYPTO = 2
bal = k.request("GET", "/portfolio/balance", auth=True)
shards = {int(b["exchange_index"]): float(b["balance"]) for b in bal["balance_breakdown"]}
cash = shards.get(CRYPTO, 0.0)
print(f"shard {CRYPTO} cash ${cash:.4f}")
# Live book: start == cash so experiment P&L begins at zero.
paper.sync_live_cash(cash, starting_usd=cash, bot_id="eva_streak")
# Paper control: same clean baseline, same notional, apples-to-apples.
paper.sync_live_cash(cash, starting_usd=cash, bot_id="eva_wick")
for b in ("eva_streak", "eva_wick"):
    s = paper.get_stats(bot_id=b)
    print(f"{b}: start ${s['starting_usd']:.2f} equity ${s['equity_usd']:.2f} realized ${s['realized_pnl_usd']:+.2f}")
PY

echo "== 4) restart bot =="
systemctl restart kalshi-bot
sleep 3
systemctl is-active kalshi-bot
journalctl -u kalshi-bot -n 5 --no-pager -o cat
