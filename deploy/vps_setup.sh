#!/usr/bin/env bash
# One-shot VPS setup for the kalshi eva_wick bot (run on the hub box as root).
set -e
APP=/opt/kalshi-15m-bot

# Hub is colocated: talk to the Intelligence API over localhost, not Caddy.
sed -i "s|^INTELLIGENCE_API_URL=.*|INTELLIGENCE_API_URL=http://localhost:8080|" "$APP/.env"

# Normalize CRLF from the Windows-authored .env.
sed -i "s/\r$//" "$APP/.env"

cd "$APP"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

cat > /etc/systemd/system/kalshi-bot.service <<'EOF'
[Unit]
Description=Kalshi 15m eva_wick bot - Telegram broadcasts + paper fills
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/kalshi-15m-bot
EnvironmentFile=/opt/kalshi-15m-bot/.env
ExecStart=/opt/kalshi-15m-bot/.venv/bin/python main.py
Restart=always
RestartSec=30

StandardOutput=journal
StandardError=journal
SyslogIdentifier=kalshi-bot

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable kalshi-bot
echo "SETUP_DONE (service installed, not started yet)"
