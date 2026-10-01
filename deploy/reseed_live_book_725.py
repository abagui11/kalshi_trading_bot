#!/usr/bin/env python3
"""Mirror the 2026-10-01 $500 deposit into the live eva_wick book's ledger.

Seed change, not a reset — the exact operation `reseed_shadow_books.py` ran
on 2026-09-30, applied to the live book for the capital that actually
arrived: `starting_usd` and `cash_usd` both move by +$500.00, so realized
P&L, every position row, and the ledger invariant
``cash = seed + realized - cost(open)`` are untouched.

Why the book does not read the shard balance instead: the ledger is pre-fee
on purpose (documented since 09-04), so book equity runs hot vs the account
by accumulated taker fees (~$170 since the 09-17 epoch) plus the ~$43 gap
between the shared $246.75 seed and the shard's actual $203.90 at the
09-22 flip. Re-basing cash to the account would silently book that gap as
a P&L event mid-epoch. The gap is a known display property, not an error.

Run with kalshi-bot STOPPED (open_trade reads cash then writes it back, and
the live book holds open positions most windows). Idempotent: refuses to
run twice.

  systemctl stop kalshi-bot
  cp ledger.db "ledger.db.bak_beforereseed_$(date -u +%Y%m%dT%H%M%SZ)"
  ./.venv/bin/python deploy/reseed_live_book_725.py
  systemctl start kalshi-bot
"""
import sqlite3
import sys

BOT = "eva_wick"
DELTA = 500.00
OLD_SEED = 246.7509
EPS = 0.01

conn = sqlite3.connect("/opt/kalshi-15m-bot/ledger.db")
conn.row_factory = sqlite3.Row

row = conn.execute(
    "SELECT starting_usd, cash_usd, realized_pnl_usd FROM paper_state"
    " WHERE bot_id = ?", (BOT,),
).fetchone()
if row is None:
    sys.exit(f"ABORT: no paper_state row for {BOT}")
seed, cash, realized = (float(row["starting_usd"]), float(row["cash_usd"]),
                        float(row["realized_pnl_usd"]))
if abs(seed - OLD_SEED) > EPS:
    sys.exit(f"ABORT: {BOT} seed is {seed:.4f}, expected {OLD_SEED:.4f} — "
             "already reseeded or in an unexpected state; doing nothing.")

open_cost = float(conn.execute(
    "SELECT COALESCE(SUM(contracts * entry_cents / 100.0), 0) c"
    " FROM paper_positions WHERE bot_id = ? AND status = 'open'", (BOT,),
).fetchone()["c"])

drift = (cash + open_cost) - (seed + realized)
if abs(drift) > EPS:
    sys.exit(f"ABORT: invariant broken BEFORE reseed (drift ${drift:.4f}) — "
             "fix the ledger first, a reseed would bake the error in.")

conn.execute(
    "UPDATE paper_state SET starting_usd = starting_usd + ?,"
    " cash_usd = cash_usd + ? WHERE bot_id = ?", (DELTA, DELTA, BOT),
)
conn.commit()

row = conn.execute(
    "SELECT starting_usd, cash_usd, realized_pnl_usd FROM paper_state"
    " WHERE bot_id = ?", (BOT,),
).fetchone()
seed2, cash2, realized2 = (float(row["starting_usd"]), float(row["cash_usd"]),
                           float(row["realized_pnl_usd"]))
drift2 = (cash2 + open_cost) - (seed2 + realized2)
assert abs(drift2) <= EPS, f"invariant broken AFTER reseed: {drift2:.4f}"
assert abs(realized2 - realized) <= 1e-9, "realized P&L must not move"
print(f"{BOT}: seed {seed:.4f} -> {seed2:.4f}, cash {cash:.4f} -> {cash2:.4f}")
print(f"equity now ${cash2 + open_cost:,.2f} (open cost ${open_cost:,.2f}); "
      f"realized unchanged at ${realized2:,.2f} — invariant OK")
