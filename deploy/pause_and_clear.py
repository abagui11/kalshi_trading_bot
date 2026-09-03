#!/usr/bin/env python3
"""Archive trade history, clear live books, write PAUSED note.

Run on the server from /opt/kalshi-15m-bot:
  PYTHONPATH=. .venv/bin/python deploy/pause_and_clear.py
"""

from __future__ import annotations

import csv
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

import config
import paper


NOTE = (
    "PAUSED — 2026-08-04\n"
    "\n"
    "Bot stopped after conviction-path soak. Vision/Claude was failing\n"
    "(Anthropic credit balance too low), so fills fell back to sticky HTF\n"
    "bull→YES at low conviction and lost money.\n"
    "\n"
    "Trade history from this soak archived under archives/.\n"
    "Do not restart live trading until Anthropic credits are restored\n"
    "and vision is verified healthy.\n"
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _export_table(conn: sqlite3.Connection, table: str, path: Path) -> int:
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in cols})
    return len(rows)


def main() -> None:
    paper.init_db()
    db_path = Path(config.LEDGER_DB)
    stamp = _stamp()
    arch = ROOT / "archives" / f"paper_paused_{stamp}"
    arch.mkdir(parents=True, exist_ok=True)

    # Snapshot DB + CSVs
    if db_path.is_file():
        shutil.copy2(db_path, arch / "ledger.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    counts = {}
    for table in (
        "paper_positions",
        "paper_trades",
        "kalshi_decisions",
        "paper_orders",
        "paper_state",
        "bot_window_state",
    ):
        try:
            counts[table] = _export_table(conn, table, arch / f"{table}.csv")
        except sqlite3.Error as exc:
            counts[table] = f"err:{exc}"
    conn.close()

    (arch / "NOTE.txt").write_text(NOTE, encoding="utf-8")
    (ROOT / "PAUSED").write_text(NOTE, encoding="utf-8")

    # Clear live history
    bankroll = float(getattr(__import__("bot_config"), "KALSHI_BANKROLL_USD", 66.10))
    with sqlite3.connect(db_path) as conn:
        for table in (
            "paper_positions",
            "paper_trades",
            "kalshi_decisions",
            "paper_orders",
            "bot_window_state",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    paper.reset_book(bankroll)

    print("archived_to", arch)
    print("counts", counts)
    print("books_reset_to", bankroll)
    print("PAUSED note written at", ROOT / "PAUSED")
    for s in paper.get_all_bot_stats():
        print(
            f"  {s['bot_id']}: equity={s['equity_usd']} "
            f"closed={s['closed_count']} decisions={paper.count_decisions(bot_id=s['bot_id'])}"
        )


if __name__ == "__main__":
    main()
