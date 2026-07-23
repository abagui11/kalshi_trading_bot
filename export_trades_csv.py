"""Export paper trades + kalshi_decisions audit rows to CSV."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
OUT_TRADES = ROOT / "exports" / "trades_export.csv"
OUT_DECISIONS = ROOT / "exports" / "decisions_export.csv"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.LEDGER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def export_trades(conn: sqlite3.Connection) -> int:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
    has_chart = "chart_path" in cols
    has_bot = "bot_id" in cols
    chart_sel = "chart_path" if has_chart else "NULL AS chart_path"
    bot_sel = "bot_id" if has_bot else "'control' AS bot_id"
    rows = conn.execute(
        f"""
        SELECT
            id, {bot_sel}, opened_at, closed_at, series, market_ticker, product_id, side,
            contracts, entry_cents, expiry_ts, status, result,
            payout_usd, pnl_usd, rationale, {chart_sel}
        FROM paper_positions
        ORDER BY id
        """
    ).fetchall()

    # Latest decision features per ticker (if table exists).
    decisions_by_ticker: dict[str, sqlite3.Row] = {}
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "kalshi_decisions" in tables:
        for d in conn.execute(
            """
            SELECT * FROM kalshi_decisions
            ORDER BY id ASC
            """
        ):
            if d["market_ticker"]:
                decisions_by_ticker[str(d["market_ticker"])] = d

    fieldnames = [
        "id",
        "bot_id",
        "opened_at",
        "closed_at",
        "series",
        "market_ticker",
        "product_id",
        "side",
        "contracts",
        "entry_cents",
        "entry_usd",
        "cost_usd",
        "yes_mid_cents",
        "model_fair_yes_cents",
        "edge_cents",
        "expiry_ts",
        "status",
        "result",
        "won",
        "payout_usd",
        "pnl_usd",
        "spot",
        "strike",
        "spot_vs_strike_pct",
        "gate_outcome",
        "trigger_type",
        "would_skip_reasons",
        "rationale",
        "chart_path",
        "chart_found",
    ]
    OUT_TRADES.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TRADES.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            entry = float(r["entry_cents"] or 0)
            contracts = int(r["contracts"] or 0)
            result = (r["result"] or "").lower()
            side = (r["side"] or "").upper()
            won = ""
            if r["status"] == "closed" and result:
                won = (
                    "1"
                    if (
                        (side == "YES" and result == "yes")
                        or (side == "NO" and result == "no")
                    )
                    else "0"
                )
            d = decisions_by_ticker.get(str(r["market_ticker"]))
            chart = (r["chart_path"] or "") if has_chart else ""
            if not chart and d is not None:
                chart = d["chart_path"] or ""
            w.writerow(
                {
                    "id": r["id"],
                    "bot_id": r["bot_id"] if "bot_id" in r.keys() else "control",
                    "opened_at": r["opened_at"],
                    "closed_at": r["closed_at"] or "",
                    "series": r["series"],
                    "market_ticker": r["market_ticker"],
                    "product_id": r["product_id"],
                    "side": r["side"],
                    "contracts": contracts,
                    "entry_cents": entry,
                    "entry_usd": round(entry / 100.0, 4),
                    "cost_usd": round((entry / 100.0) * contracts, 4),
                    "yes_mid_cents": d["yes_mid_cents"] if d else "",
                    "model_fair_yes_cents": d["model_fair_yes_cents"] if d else "",
                    "edge_cents": d["edge_cents"] if d else "",
                    "expiry_ts": r["expiry_ts"] or "",
                    "status": r["status"],
                    "result": r["result"] or "",
                    "won": won,
                    "payout_usd": r["payout_usd"] if r["payout_usd"] is not None else "",
                    "pnl_usd": r["pnl_usd"] if r["pnl_usd"] is not None else "",
                    "spot": d["spot"] if d else "",
                    "strike": d["strike"] if d else "",
                    "spot_vs_strike_pct": d["spot_vs_strike_pct"] if d else "",
                    "gate_outcome": d["gate_outcome"] if d else "",
                    "trigger_type": d["trigger_type"] if d else "",
                    "would_skip_reasons": d["would_skip_reasons"] if d else "",
                    "rationale": (r["rationale"] or "")
                    .replace("\r\n", " ")
                    .replace("\n", " "),
                    "chart_path": chart,
                    "chart_found": "1" if chart else "0",
                }
            )
    print(f"Wrote {len(rows)} trades -> {OUT_TRADES}")
    return len(rows)


def export_decisions(conn: sqlite3.Connection) -> int:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "kalshi_decisions" not in tables:
        print("No kalshi_decisions table yet")
        return 0
    rows = conn.execute(
        "SELECT * FROM kalshi_decisions ORDER BY id"
    ).fetchall()
    if not rows:
        OUT_DECISIONS.parent.mkdir(parents=True, exist_ok=True)
        with OUT_DECISIONS.open("w", newline="", encoding="utf-8-sig") as f:
            f.write("")
        print(f"Wrote 0 decisions -> {OUT_DECISIONS}")
        return 0
    fieldnames = list(rows[0].keys())
    OUT_DECISIONS.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DECISIONS.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] if r[k] is not None else "" for k in fieldnames})
    print(f"Wrote {len(rows)} decisions -> {OUT_DECISIONS}")
    return len(rows)


def main() -> None:
    conn = _connect()
    try:
        export_trades(conn)
        export_decisions(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
