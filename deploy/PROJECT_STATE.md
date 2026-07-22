# Kalshi 15m paper bot — project state

Updated: 2026-07-22

## What this is

Local Windows bot that papers Kalshi **KXBTC15M** / **KXETH15M** 15-minute up/down markets.

- Claude picks YES/NO from Coinbase M5 candles + Kalshi YES mid.
- Trades only when edge vs mid ≥ `KALSHI_MIN_EDGE_CENTS`.
- Paper fill at mid; settle from Kalshi `result` (YES→$1 / NO→$0 per contract).
- Telegram: trade + why only; `/stats` and `/positions`.
- Dashboard: equity + open/closed paper trades on `DASHBOARD_PORT` (default 8081).

## Run locally

```powershell
cd C:\Users\abagu\OneDrive\Documents\Republic\kalshi_15m_bot
.\.venv\Scripts\Activate.ps1
python main.py
```

Dashboard (separate terminal):

```powershell
python -m dashboard
```

One-shot cycle (forces a decision even off the window offset):

```powershell
python -c "from kalshi_cycle import run_once; import json; print(json.dumps(run_once(force_decision=True), indent=2, default=str))"
```

## Key modules

| File | Role |
|------|------|
| `kalshi_client.py` | RSA-PSS auth, markets, mid, result, paper-only order stub |
| `kalshi_cycle.py` | settle + decision cycle |
| `paper.py` | binary SQLite paper book |
| `bot.py` / `main.py` | Telegram + 60s job |
| `notify.py` | trade+why DM |

## Safety

- `KALSHI_PAPER_ONLY=true` until Gate 3 paper soak passes.
- Secrets live in `secrets/` and `.env` (gitignored).
- Do not reuse the spot bot Telegram token or `ledger.db`.
