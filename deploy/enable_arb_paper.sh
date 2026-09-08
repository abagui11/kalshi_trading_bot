#!/usr/bin/env bash
# 2026-09-08: enable eva_arb (last-2-min favorite-dip) as a paper sleeve in
# the current experiment epoch. Run ON the VPS after code sync:
#   bash deploy/enable_arb_paper.sh
set -eu
cd /opt/kalshi-15m-bot

echo "== 1) env: add eva_arb to ENABLED_BOTS (stays paper via KALSHI_LIVE_BOTS) =="
sed -i 's/^ENABLED_BOTS=.*/ENABLED_BOTS=eva_wick,eva_streak,eva_arb/' .env
grep -E 'ENABLED_BOTS|KALSHI_LIVE_BOTS|KALSHI_PAPER_ONLY' .env

echo "== 2) seed eva_arb book at the shared experiment baseline =="
.venv/bin/python - <<'PY'
import paper

BASELINE = 246.7509  # same start line as eva_streak / eva_wick (2026-09-08 flip)
paper.sync_live_cash(BASELINE, starting_usd=BASELINE, bot_id="eva_arb")
s = paper.get_stats(bot_id="eva_arb")
print(f"eva_arb: start ${s['starting_usd']:.2f} equity ${s['equity_usd']:.2f} realized ${s['realized_pnl_usd']:+.2f}")
PY

echo "== 3) restart bot =="
systemctl restart kalshi-bot
sleep 3
systemctl is-active kalshi-bot
