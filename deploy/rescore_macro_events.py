"""Re-score recent `ignored` macro headlines with the current keyword set.

Keyword changes (e.g. promoting CLARITY Act / legislative catalysts) only affect
headlines ingested *after* the change. Headlines already stored as `ignored`
keep their old score and are skipped on re-poll for 7 days by the URL-hash
dedup, so they never resurface. This one-off backfill recomputes
`relevance_score` for recent ignored events and, for any that now clear
`MACRO_LLM_PROMOTE_THRESHOLD`, runs the classifier and flips them to
`classified` so they appear in active posture and `/research macro`.

Usage (on the server, from the repo root):

    sudo -u ethagent /opt/eth-trading-agent/.venv/bin/python \
        deploy/rescore_macro_events.py --days 5 --dry-run
    sudo -u ethagent /opt/eth-trading-agent/.venv/bin/python \
        deploy/rescore_macro_events.py --days 5 --yes
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot_config  # noqa: E402
import config  # noqa: E402
from macro import classify  # noqa: E402
from macro.keywords import relevance_score  # noqa: E402


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.LEDGER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5, help="Look back window (default 5).")
    parser.add_argument(
        "--yes", action="store_true", help="Apply changes (default is dry-run)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only; make no changes."
    )
    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Update keyword scores only; do not call the classifier or promote.",
    )
    args = parser.parse_args()

    apply_changes = args.yes and not args.dry_run
    threshold = bot_config.MACRO_LLM_PROMOTE_THRESHOLD

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, source, title, summary, keyword_score
            FROM macro_events
            WHERE status = 'ignored'
              AND ingested_at >= datetime('now', ?)
            ORDER BY ingested_at DESC
            """,
            (f"-{max(1, args.days)} days",),
        ).fetchall()

    print(
        f"Scanning {len(rows)} ignored events from the last {args.days} day(s); "
        f"promote threshold = {threshold}; "
        f"mode = {'APPLY' if apply_changes else 'DRY-RUN'}"
    )

    rescored = 0
    promoted = 0
    for row in rows:
        text = f"{row['title']} {row['summary'] or ''}"
        new_score, hits = relevance_score(text, extra_t2=config.MACRO_KEYWORD_EXTRA)
        old_score = int(row["keyword_score"] or 0)
        if new_score == old_score:
            continue
        rescored += 1
        crosses = new_score >= threshold and not args.no_classify
        flag = " -> PROMOTE" if crosses else ""
        print(f"  #{row['id']} {old_score} -> {new_score}{flag}  {row['title'][:70]}")

        if not apply_changes:
            if crosses:
                promoted += 1
            continue

        if not crosses:
            with _connect() as conn:
                conn.execute(
                    "UPDATE macro_events SET keyword_score = ?, keyword_hits = ? WHERE id = ?",
                    (new_score, json.dumps(hits), row["id"]),
                )
                conn.commit()
            continue

        classification = classify.classify_headline(
            title=row["title"], summary=row["summary"], source=row["source"]
        )
        expires_at = classify.expires_at_from_ttl(int(classification["ttl_hours"]))
        with _connect() as conn:
            conn.execute(
                """
                UPDATE macro_events
                SET keyword_score = ?, keyword_hits = ?, severity = ?, eth_bias = ?,
                    category = ?, eth_impact_summary = ?, posture_hints = ?,
                    expires_at = ?, status = 'classified', raw_json = ?
                WHERE id = ?
                """,
                (
                    new_score,
                    json.dumps(hits),
                    int(classification["severity"]),
                    classification["eth_bias"],
                    classification["category"],
                    classification["eth_impact_summary"],
                    json.dumps(classification["posture_hints"]),
                    expires_at,
                    json.dumps(classification),
                    row["id"],
                ),
            )
            conn.commit()
        promoted += 1
        print(
            f"       classified sev={classification['severity']} "
            f"bias={classification['eth_bias']} cat={classification['category']}"
        )

    verb = "would be" if not apply_changes else "were"
    print(f"\nDone. {rescored} event(s) re-scored; {promoted} {verb} promoted/classified.")
    if not apply_changes:
        print("Dry-run only — re-run with --yes to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
