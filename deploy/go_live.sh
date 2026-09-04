#!/usr/bin/env bash
# Flip the kalshi eva_wick bot to live trading (run on the VPS as root).
set -eu
ENV=/opt/kalshi-15m-bot/.env

sed -i 's/^KALSHI_PAPER_ONLY=.*/KALSHI_PAPER_ONLY=false/' "$ENV"
# Belt-and-braces absolute per-trade notional ceiling (sizing caps at
# KALSHI_MAX_DEPLOY_PCT of the $66 bankroll ~= $9.90 anyway).
if grep -q '^KALSHI_MAX_NOTIONAL_USD=' "$ENV"; then
  sed -i 's/^KALSHI_MAX_NOTIONAL_USD=.*/KALSHI_MAX_NOTIONAL_USD=10/' "$ENV"
else
  echo 'KALSHI_MAX_NOTIONAL_USD=10' >> "$ENV"
fi

echo "=== flags now ==="
grep -E 'PAPER_ONLY|MAX_NOTIONAL|BANKROLL|DEPLOY_PCT|ENABLED_BOTS|LIVE_TAKE' "$ENV" | grep -v '^#'

systemctl restart kalshi-bot
sleep 8
systemctl is-active kalshi-bot
echo "=== startup log ==="
journalctl -u kalshi-bot -n 20 --no-pager
