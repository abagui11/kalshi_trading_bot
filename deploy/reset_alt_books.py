#!/usr/bin/env python3
"""Restart the altcoin shadow-book epoch after the fill model was withdrawn.

The first three entries of the 2026-09-30 epoch were priced by the ladder walk
that has since been turned off (`EVA_FAV_ALT_TRIM_TO_DEPTH = False`): one of
them, SOL at 61.0c against a quoted 69.0c ask, is a fill the exchange could
not have given. The other two were walked up rather than down and are only
*probably* wrong, but they were produced by the same model, so none of the
three can be compared against a book that now fills at the ask.

Nothing is deleted. The rows are copied into
``paper_positions_altbadfill_20260930`` first and stay queryable there; what
is removed is their claim on the forward record.

Run with kalshi-bot.service STOPPED, then restart it and re-pin
``_ALT_EPOCH_DEFAULT`` in trading_bot_MVP/kalshi_bridge.py to the new start.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/kalshi-15m-bot")

import bot_config  # noqa: E402
import paper  # noqa: E402

ALT = tuple(bot_config.ALT_WICK_VARIANTS)
ARCHIVE = "paper_positions_altbadfill_20260930"
ph = ",".join("?" * len(ALT))
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with paper._connect() as conn:
    before = conn.execute(
        f"SELECT bot_id, COUNT(*) n FROM paper_positions WHERE bot_id IN ({ph})"
        " GROUP BY bot_id",
        ALT,
    ).fetchall()
    print("positions before:", {r["bot_id"]: r["n"] for r in before} or "none")
    for r in conn.execute(
        f"SELECT bot_id, market_ticker, side, contracts, entry_cents, status"
        f" FROM paper_positions WHERE bot_id IN ({ph}) ORDER BY id", ALT
    ):
        print(f"  {r['bot_id']:<14} {r['market_ticker']:<28} {r['side']} "
              f"x{r['contracts']} @ {float(r['entry_cents']):.2f}c "
              f"({r['status']})")

    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {ARCHIVE} AS"
        f" SELECT * FROM paper_positions WHERE 0"
    )
    moved = conn.execute(
        f"INSERT INTO {ARCHIVE} SELECT * FROM paper_positions"
        f" WHERE bot_id IN ({ph})",
        ALT,
    ).rowcount
    print(f"\narchived {moved} row(s) to {ARCHIVE}")

    for table in ("paper_positions", "paper_trades", "paper_orders",
                  "bot_window_state"):
        try:
            n = conn.execute(
                f"DELETE FROM {table} WHERE bot_id IN ({ph})", ALT
            ).rowcount
        except Exception as exc:  # table may not exist in older ledgers
            print(f"  {table}: skipped ({exc})")
            continue
        print(f"  {table}: cleared {n}")

    start = float(bot_config.KALSHI_BANKROLL_USD)
    for bot_id in ALT:
        conn.execute(
            "INSERT OR REPLACE INTO paper_state"
            " (bot_id, starting_usd, cash_usd, realized_pnl_usd, updated_at)"
            " VALUES (?, ?, ?, 0, ?)",
            (bot_id, start, start, now),
        )
    print(f"\npaper_state reset to ${start:.2f} for: {', '.join(ALT)}")

with paper._connect() as conn:
    for bot_id in ALT:
        s = conn.execute(
            "SELECT * FROM paper_state WHERE bot_id=?", (bot_id,)
        ).fetchone()
        n = conn.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE bot_id=?", (bot_id,)
        ).fetchone()[0]
        print(f"  {bot_id:<14} positions={n} cash=${float(s['cash_usd']):.2f} "
              f"realized=${float(s['realized_pnl_usd']):+.2f}")

print(f"\nnew epoch starts at the next kalshi-bot start (now {now})")
