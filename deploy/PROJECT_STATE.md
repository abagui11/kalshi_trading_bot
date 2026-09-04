# Kalshi 15m bot — project state

Updated: 2026-09-04

## What this is

Local Windows bot that papers Kalshi **KXBTC15M** / **KXETH15M** 15-minute up/down markets.

- Shared ICT/HTF Claude bias (gated refresh) + strategy plugins; default **adverse-only**.
- Trades only when strategy + edge gates pass.
- Paper fill at mid; settle from Kalshi `result` (YES→$1 / NO→$0 per contract).
- Telegram: trade + why only; `/stats` and `/positions`.
- Dashboard: equity + open/closed paper trades on `DASHBOARD_PORT` (default 8081).

**Adverse-sensitive deploys:** see [`ADVERSE_CHANGELOG.md`](ADVERSE_CHANGELOG.md).

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
| `kalshi_cycle.py` | settle + decision cycle + HTF refresh gate |
| `paper.py` | binary SQLite paper book |
| `bot.py` / `main.py` | Telegram + 60s job |
| `notify.py` | trade+why DM |

## Safety

- Live risk caps: sizing bankroll = `min(live balance, KALSHI_BANKROLL_USD)`,
  `KALSHI_DEPLOY_PCT` per trade, `KALSHI_MAX_DEPLOY_PCT` hard cap,
  `KALSHI_MAX_NOTIONAL_USD` absolute ceiling (0 = off). Exits (`closing=True`
  orders) bypass entry caps — they are risk-reducing.
- Secrets live in `secrets/` and `.env` (gitignored).
- Do not reuse the spot bot Telegram token or `ledger.db`.
- Production (VPS 45.33.97.27, `/opt/kalshi-15m-bot`, systemd `kalshi-bot`):
  `ENABLED_BOTS=eva_wick`, zero-Claude, direction from hub EVA stances.

## Changelog

| Date | Change |
|------|--------|
| 2026-09-04 | **Telegram cards no longer claim "PAPER TRADE" while trading live.** The filled-trade header and the equity line now derive from `KALSHI_PAPER_ONLY` (`LIVE FILL` / `Book equity` when live), as does `paper.format_stats_text`. Subscriber demo-account wording in `telegram_ui.py` is untouched — those accounts really are paper. Added `deploy/reconcile.sh` to diff the ledger against the account; portfolio reads are **shard-scoped** (`exchange_index=2`), because unscoped `GET /portfolio/positions` returns empty for crypto positions and unscoped balance is a cross-shard aggregate. First two live trades reconcile exactly: BTC YES 39¢→92.4¢ (+$0.534) and ETH YES 39¢→98.2¢ (+$0.592) against a shard-2 balance move of +$1.0863, the ~$0.04 gap being taker fees the ledger does not model. |
| 2026-09-04 | **Collateral moved to Kalshi's crypto shard.** Kalshi sharded its exchange 2026-08-24; crypto markets live on `exchange_index=2` and collateral is per-shard, so the first live orders 404'd with `user_not_found` (all funds sat on shard 0). `deploy/shard_transfer.sh` moved $70 to shard 2 via `POST /portfolio/intra_exchange_instance_transfer`. If the live balance on shard 2 runs low, rerun the script or use the Kalshi UI; unscoped `GET /portfolio/balance` returns a cross-shard aggregate, so sizing's `KALSHI_BANKROLL_USD` clamp is what keeps orders within the shard-2 float. |
| 2026-09-04 | **Live trading enabled** (`KALSHI_PAPER_ONLY=false`). Fixed two paper→live gaps: (1) eva_wick entries now priced at side mid (`entry_at_mid=True`) instead of the legacy mid−3¢ "intended limit", which a live IOC (+`KALSHI_LIVE_TAKE_CENTS`) could never fill; (2) eva_wick take-profit now executes a real exchange exit — buys the opposite side fill-or-kill so Kalshi nets the position — and only flattens the ledger at the actual fill; unfilled exits stay open and retry next tick. Known accounting gap: Kalshi taker fees (~1–2¢/contract/side) are not modeled in the ledger, so ledger P&L runs slightly hot vs the account balance. |
| 2026-09-03 | eva_wick strategy added (zero-Claude wick fade/overshoot off hub EVA stances); deployed to VPS with Telegram broadcasts + charts; paper soak overnight. |
