#!/usr/bin/env bash
# Backfill live Kalshi settlements that never made the paper book
# (IOC filled, then paper_open_failed). Does NOT touch cash — paper_state
# was already synced to shard-2 balance, which includes these P&Ls.
set -eu
cd /opt/kalshi-15m-bot
.venv/bin/python - <<'PY'
import sqlite3
from datetime import datetime, timezone
import kalshi_client as k
import paper

CRYPTO = 2
BOT = "eva_wick"

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

sett = k.request("GET", "/portfolio/settlements", params={"exchange_index": CRYPTO, "limit": 50}, auth=True)
EPOCH = "2026-09-04T16:37:00Z"
fills = k.request("GET", "/portfolio/fills", params={"exchange_index": CRYPTO, "limit": 80}, auth=True)
fills_by_ticker: dict[str, list] = {}
for f in fills.get("fills") or []:
    fills_by_ticker.setdefault(f.get("ticker"), []).append(f)

conn = sqlite3.connect("ledger.db")
conn.row_factory = sqlite3.Row

inserted = []
for s in sett.get("settlements") or []:
    ticker = s.get("ticker") or ""
    if not ticker:
        continue
    settled = str(s.get("settled_time") or "")
    if settled and settled < EPOCH:
        continue
    exists = conn.execute(
        "SELECT id FROM paper_positions WHERE market_ticker=? AND bot_id=?",
        (ticker, BOT),
    ).fetchone()
    if exists:
        continue
    yes_n = float(s.get("yes_count_fp") or 0)
    no_n = float(s.get("no_count_fp") or 0)
    if yes_n < 0.5 and no_n < 0.5:
        continue
    if yes_n >= no_n:
        side, contracts = "YES", int(round(yes_n))
        cost = float(s.get("yes_total_cost_dollars") or 0)
    else:
        side, contracts = "NO", int(round(no_n))
        cost = float(s.get("no_total_cost_dollars") or 0)
    entry = (cost / contracts * 100.0) if contracts else 0.0
    result = str(s.get("market_result") or "").lower()
    won = (side.lower() == result)
    payout = float(contracts) if won else 0.0
    pnl = payout - cost
    opened = None
    for f in fills_by_ticker.get(ticker, []):
        opened = f.get("created_time") or opened
    opened = (opened or s.get("settled_time") or now())[:19] + "Z"
    closed = (s.get("settled_time") or now())[:19] + "Z"
    product = "ETH" if "ETH" in ticker else "BTC"
    series = "KXETH15M" if product == "ETH" else "KXBTC15M"
    rationale = (
        f"backfill: live Kalshi fill was rejected by the soak paper book "
        f"(paper_open_failed). {side} x{contracts} @ {entry:.2f}¢ settled {result}. "
        f"Cash already includes this via shard-2 sync; this row is display-only."
    )
    conn.execute(
        """
        INSERT INTO paper_positions (
            bot_id, opened_at, closed_at, series, market_ticker, product_id,
            side, contracts, entry_cents, expiry_ts, rationale, status,
            result, payout_usd, pnl_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?)
        """,
        (BOT, opened, closed, series, ticker, product, side, contracts,
         entry, closed, rationale, result, payout, pnl),
    )
    inserted.append((ticker, side, contracts, entry, result, pnl))

conn.commit()
print("inserted", len(inserted), "ghost settlements:")
for row in inserted:
    print(" ", row)

# Keep cash = live shard (do not add pnl again).
bal = k.request("GET", "/portfolio/balance", auth=True)
shards = {int(b["exchange_index"]): float(b["balance"]) for b in bal["balance_breakdown"]}
cash = shards.get(CRYPTO, 0.0)
paper.sync_live_cash(cash, starting_usd=450.0, bot_id=BOT)
st = paper.get_stats(bot_id=BOT)
print(f"cash kept at shard-2 ${cash:.2f}; realized ${st['realized_pnl_usd']:+.2f}")
print("closed since epoch:")
for r in conn.execute(
    "SELECT id, opened_at, market_ticker, side, contracts, entry_cents, result, round(pnl_usd,2) "
    "FROM paper_positions WHERE bot_id=? AND opened_at>='2026-09-04T16:37' ORDER BY id",
    (BOT,),
):
    print(dict(r) if False else tuple(r))
conn.close()
PY
