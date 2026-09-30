#!/usr/bin/env python3
"""What the altcoin books actually paid, against what the book was quoting.

Joins each shadow-book position to the decision row that opened it, so the
simulated fill (count, VWAP, slippage vs the touch) can be checked against the
touch ask and the qualifying mid. An entry outside the 67-82c range, or a VWAP
below the touch, means the ladder walk is wrong — not that we got a good fill.

    /opt/kalshi-15m-bot/.venv/bin/python deploy/_alt_fill_audit.py
"""
from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, "/opt/kalshi-15m-bot")

import config  # noqa: E402

ALT = ("eva_wick_xrp", "eva_wick_sol", "eva_wick_hype")

conn = sqlite3.connect(f"file:{config.LEDGER_DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

ph = ",".join("?" * len(ALT))
rows = conn.execute(
    f"SELECT * FROM paper_positions WHERE bot_id IN ({ph})"
    " ORDER BY opened_at DESC LIMIT 40",
    ALT,
).fetchall()

def _arm_meta(bot_id: str, ticker: str) -> dict:
    """size_for_fill writes its context into the window arm, not the decision."""
    row = conn.execute(
        "SELECT meta_json FROM bot_window_state WHERE bot_id=? AND market_ticker=?",
        (bot_id, ticker),
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return {}

for r in rows:
    print(f"\n{r['bot_id']}  {r['market_ticker']}  {r['side']} x{r['contracts']} "
          f"@ {float(r['entry_cents']):.2f}c   status={r['status']} "
          f"result={r['result']} pnl={r['pnl_usd']}")
    print(f"  opened_at {r['opened_at']}")
    d = conn.execute(
        "SELECT yes_mid_cents, entry_cents, fill_vs_mid_cents, seconds_to_expiry,"
        " rationale FROM kalshi_decisions WHERE bot_id=? AND market_ticker=?"
        " ORDER BY id DESC LIMIT 1",
        (r["bot_id"], r["market_ticker"]),
    ).fetchone()
    if d:
        print(f"    yes_mid_cents        {d['yes_mid_cents']}")
        print(f"    decision entry       {d['entry_cents']}")
        print(f"    fill_vs_mid_cents    {d['fill_vs_mid_cents']}")
        print(f"    seconds_to_expiry    {d['seconds_to_expiry']}")
        print(f"    rationale            {str(d['rationale'])[:160]}")
    meta = _arm_meta(r["bot_id"], r["market_ticker"])
    if not meta:
        print("  (no window-arm meta)")
        continue
    keys = (
        "side_mid_cents", "ask_cents", "touch_ask", "fill_limit_cents",
        "fill_model", "ladder_levels", "requested_ct", "filled_ct",
        "fill_vwap_cents", "slippage_vs_touch", "touch_depth_ct",
        "spread_cents", "ask_vs_mid", "seconds_left",
    )
    for k in keys:
        if k in meta:
            print(f"    {k:<20} {meta[k]}")
    extra = {k: v for k, v in meta.items() if k not in keys}
    if extra:
        print(f"    (other) {extra}")

conn.close()
