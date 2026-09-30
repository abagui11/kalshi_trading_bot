"""Re-seed the wick-derivative shadow books to their configured seed.

Operator call 2026-09-30: the two hourly piggyback books and the six
cross-asset books run on a $1,000 ledger instead of the $225 baseline the
rest of this ledger uses. Only those eight ids are touched — the live wick
book's state mirrors the real shard balance, and the 09-08 sleeves are
mid-epoch, so re-basing either would restate a record that is being scored.

This is a seed change, not a reset. Every position, trade and the realized
P&L stay exactly as they are; only ``starting_usd`` and the matching cash
move, via the ledger's own invariant:

    equity = cash + cost(open positions) = starting_usd + realized

so ``cash = seed + realized - open_cost``. A book that has lost $0.70 still
shows -$0.70 afterwards, against a $1,000 seed instead of a $225 one.

Idempotent — running it twice is a no-op. Honest note on what it does cost:
the percent-of-seed column on the Eva Lab funnel is P&L over this seed, so
the hourly books' percent figures (6 closed rows each at the time of the
change) are restated by the ratio. Their trade record and epoch are
untouched; nothing is rewritten and nothing is archived.

Usage (on the VPS, from /opt/kalshi-15m-bot):

    .venv/bin/python deploy/reseed_shadow_books.py --dry-run
    .venv/bin/python deploy/reseed_shadow_books.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot_config  # noqa: E402
import paper  # noqa: E402


def _open_cost(conn, bot_id: str) -> float:
    rows = conn.execute(
        "SELECT entry_cents, contracts FROM paper_positions"
        " WHERE bot_id = ? AND status = 'open'",
        (bot_id,),
    ).fetchall()
    return sum(
        (float(r["entry_cents"]) / 100.0) * int(r["contracts"]) for r in rows
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = sorted(bot_config.SHADOW_SEED_BOTS)
    paper.init_db()
    changed = 0
    with paper._connect() as conn:
        for bot_id in targets:
            seed = float(bot_config.book_seed_usd(bot_id))
            row = conn.execute(
                "SELECT starting_usd, cash_usd, realized_pnl_usd"
                " FROM paper_state WHERE bot_id = ?",
                (bot_id,),
            ).fetchone()
            if row is None:
                print(f"{bot_id:22s} (no paper_state row — skipped)")
                continue
            old_start = float(row["starting_usd"] or 0)
            old_cash = float(row["cash_usd"] or 0)
            realized = float(row["realized_pnl_usd"] or 0)
            open_cost = _open_cost(conn, bot_id)
            new_cash = seed + realized - open_cost
            if abs(old_start - seed) < 0.005 and abs(old_cash - new_cash) < 0.005:
                print(f"{bot_id:22s} already at ${seed:,.2f} — no change")
                continue
            print(
                f"{bot_id:22s} seed ${old_start:,.2f} -> ${seed:,.2f} | "
                f"cash ${old_cash:,.2f} -> ${new_cash:,.2f} | "
                f"realized ${realized:+,.2f} kept | open cost ${open_cost:,.2f}"
            )
            if not args.dry_run:
                conn.execute(
                    "UPDATE paper_state SET starting_usd = ?, cash_usd = ?,"
                    " updated_at = ? WHERE bot_id = ?",
                    (seed, new_cash, paper._now(), bot_id),
                )
                changed += 1
        if not args.dry_run:
            conn.commit()

        # Post-check on every touched book: the ledger invariant must hold.
        if not args.dry_run:
            print()
            for bot_id in targets:
                row = conn.execute(
                    "SELECT starting_usd, cash_usd, realized_pnl_usd"
                    " FROM paper_state WHERE bot_id = ?",
                    (bot_id,),
                ).fetchone()
                if row is None:
                    continue
                equity = float(row["cash_usd"]) + _open_cost(conn, bot_id)
                expect = float(row["starting_usd"]) + float(
                    row["realized_pnl_usd"] or 0
                )
                ok = abs(equity - expect) < 0.005
                print(
                    f"{bot_id:22s} equity ${equity:,.2f} vs seed+realized "
                    f"${expect:,.2f} {'OK' if ok else 'MISMATCH'}"
                )
                if not ok:
                    return 1

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {changed if not args.dry_run else len(targets)} book(s).")
    if args.dry_run:
        print("Dry run — nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
