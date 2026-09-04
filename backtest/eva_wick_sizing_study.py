"""Do eva_wick's size multipliers earn their keep?

Evidence behind the 2026-09-04 sizing change. Each multiplier in
``strategies/eva_wick.py`` claims a cohort is better or worse than average; this
replays the recorded book and checks whether that claim holds.

ROI (pnl / cost) is the unit, not cents-per-contract: sizing decides how much
capital rides a setup, and a 2x take-profit pays roughly +100% on any entry
price, so per-contract cents mostly re-measures the entry price.

Usage:  python backtest/eva_wick_sizing_study.py [path/to/ledger.db]

Caveat that belongs in any conclusion drawn from this: the book is tens of
positions over a single regime. Cohort splits here are directional evidence,
not significance.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict

PENALTY_TAGS = ("soft_rich", "soft_mid_hour", "soft_btc_move")
BOOST_TAGS = ("last15_priority", "m15_conviction")


def load(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.contracts, p.entry_cents, p.result, p.pnl_usd,
                   d.setup_tags
            FROM paper_positions p
            LEFT JOIN kalshi_decisions d ON d.position_id = p.id
            WHERE p.bot_id = 'eva_wick' AND p.status != 'open'
            GROUP BY p.id ORDER BY p.id
            """
        ).fetchall()
    finally:
        conn.close()

    recs = []
    for r in rows:
        ct = int(r["contracts"] or 0)
        cost = float(r["entry_cents"]) / 100.0 * ct
        pnl = float(r["pnl_usd"] or 0)
        recs.append(
            {
                "id": r["id"],
                "tags": set(json.loads(r["setup_tags"] or "[]")),
                "entry": float(r["entry_cents"]),
                "cost": cost,
                "pnl": pnl,
                "win": pnl > 0,
            }
        )
    return recs


def report(label: str, sel: list[dict]) -> None:
    if not sel:
        return
    n = len(sel)
    cost = sum(x["cost"] for x in sel)
    pnl = sum(x["pnl"] for x in sel)
    wins = sum(1 for x in sel if x["win"])
    roi = (pnl / cost * 100) if cost else 0.0
    print(
        f"{label:34} n={n:>3}  win {wins / n * 100:>3.0f}%  "
        f"cost ${cost:>6.2f}  pnl ${pnl:>+7.2f}  ROI {roi:>+6.1f}%"
    )


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else "ledger.db"
    recs = load(db)
    if not recs:
        print("no closed eva_wick positions in", db)
        return
    print(f"closed eva_wick positions: {len(recs)}\n")

    print("=== does each multiplier select the cohort it claims? ===")
    for tag in PENALTY_TAGS + BOOST_TAGS + ("wide_day_range",):
        has = [x for x in recs if tag in x["tags"]]
        if not has:
            continue
        hasnt = [x for x in recs if tag not in x["tags"]]
        kind = "penalty" if tag in PENALTY_TAGS else (
            "boost" if tag in BOOST_TAGS else "penalty"
        )
        print(f"\n-- {tag} ({kind}, fires {len(has)}/{len(recs)}) --")
        report("   WITH", has)
        report("   WITHOUT", hasnt)

    print("\n=== stacked penalties vs outcome ===")
    by_n: dict[int, list[dict]] = defaultdict(list)
    for x in recs:
        by_n[len(x["tags"] & set(PENALTY_TAGS))].append(x)
    for k in sorted(by_n):
        report(f"   {k} soft penalt{'y' if k == 1 else 'ies'}", by_n[k])

    print("\n=== entry price bucket ===")
    for lo, hi in ((0, 20), (20, 27), (27, 33), (33, 100)):
        report(f"   entry {lo}-{hi}c", [x for x in recs if lo <= x["entry"] < hi])

    # P&L is linear in size, so a multiplier change scales its cohort directly.
    print("\n=== counterfactual sizing (same trades, scaled) ===")
    base = sum(x["pnl"] for x in recs)
    print(f"   baseline                                  ${base:+.2f}")
    always_on = [t for t in ("wide_day_range",) if
                 sum(1 for x in recs if t in x["tags"]) == len(recs)]
    if always_on:
        print(f"   drop always-on wide_day_range (x1.33)     ${base / 0.75:+.2f}")
    for tag in ("soft_rich", "soft_mid_hour"):
        cohort = sum(x["pnl"] for x in recs if tag in x["tags"])
        print(
            f"   ...and un-halve {tag:<14} ${base / 0.75 + cohort / 0.75:+.2f}"
            f"   [cohort alone: ${cohort:+.2f}]"
        )


if __name__ == "__main__":
    main()
