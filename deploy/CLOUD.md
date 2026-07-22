# Kalshi 15m bot — cloud deploy (optional, after paper + demo gates)

Do **not** share the spot ETH bot’s `.env` or `ledger.db`.

## Suggested layout

- App dir: `/opt/kalshi-15m-bot`
- Service: `kalshi-agent.service`
- Dashboard port: `8081`
- Secrets: `/opt/kalshi-15m-bot/secrets/kalshi_prod.key` (or demo key while testing)

## systemd sketch

```ini
[Unit]
Description=Kalshi 15m trading agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/kalshi-15m-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/kalshi-15m-bot/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Dashboard (optional second unit):

```ini
ExecStart=/opt/kalshi-15m-bot/.venv/bin/python -m dashboard
```

## Prod `.env` switches

```env
KALSHI_ENV=prod
KALSHI_API_BASE=https://external-api.kalshi.com/trade-api/v2
KALSHI_API_KEY_ID=<prod-uuid>
KALSHI_PRIVATE_KEY_PATH=secrets/kalshi_prod.key
KALSHI_PAPER_ONLY=false
KALSHI_MAX_CONTRACTS=1
DASHBOARD_PORT=8081
```

Keep caps tiny until you trust settlement + Telegram + Kalshi UI agreement.
