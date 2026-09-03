#!/usr/bin/env bash
# One-shot: mint a service token for the Kalshi eva_wick bot, append it to the
# hub's SERVICE_API_TOKENS, restart the dashboard, print the token.
set -e
ENV=/opt/eth-trading-agent/.env
TOKEN=$(openssl rand -hex 32)
cp "$ENV" "$ENV.bak_kalshi_token_20260903"
sed -i "s|^SERVICE_API_TOKENS=.*|&,$TOKEN|" "$ENV"
grep -c "$TOKEN" "$ENV" >/dev/null
systemctl restart eth-dashboard
sleep 3
systemctl is-active eth-dashboard
echo "NEWTOKEN=$TOKEN"
