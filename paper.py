"""Kalshi binary paper book — fill at mid, settle YES=$1 / NO=$0 per contract."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import config
from models import KalshiSuggestion

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.LEDGER_DB)
    conn.row_factory = sqlite3.Row
    return conn


_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_usd REAL NOT NULL,
    cash_usd REAL NOT NULL,
    realized_pnl_usd REAL NOT NULL DEFAULT 0,
    updated_at TEXT
);
"""

_POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    series TEXT NOT NULL,
    market_ticker TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL,
    side TEXT NOT NULL,
    contracts INTEGER NOT NULL,
    entry_cents REAL NOT NULL,
    expiry_ts TEXT,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    result TEXT,
    payout_usd REAL,
    pnl_usd REAL,
    closed_at TEXT
);
"""

_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    series TEXT,
    market_ticker TEXT,
    product_id TEXT,
    side TEXT,
    contracts INTEGER,
    entry_cents REAL,
    result TEXT,
    payout_usd REAL,
    pnl_usd REAL,
    rationale TEXT
);
"""


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_STATE_SCHEMA)
        conn.execute(_POSITIONS_SCHEMA)
        conn.execute(_TRADES_SCHEMA)
        row = conn.execute("SELECT id FROM paper_state WHERE id = 1").fetchone()
        if row is None:
            start = float(config.PAPER_PORTFOLIO_VALUE)
            conn.execute(
                """
                INSERT INTO paper_state (id, starting_usd, cash_usd, realized_pnl_usd, updated_at)
                VALUES (1, ?, ?, 0, ?)
                """,
                (start, start, _now()),
            )
        conn.commit()


def reset_book(starting_usd: float | None = None) -> None:
    start = float(starting_usd if starting_usd is not None else config.PAPER_PORTFOLIO_VALUE)
    with _connect() as conn:
        conn.execute("DELETE FROM paper_positions")
        conn.execute("DELETE FROM paper_trades")
        conn.execute("DELETE FROM paper_state")
        conn.execute(
            """
            INSERT INTO paper_state (id, starting_usd, cash_usd, realized_pnl_usd, updated_at)
            VALUES (1, ?, ?, 0, ?)
            """,
            (start, start, _now()),
        )
        conn.commit()


def _cost_usd(entry_cents: float, contracts: int) -> float:
    return (float(entry_cents) / 100.0) * int(contracts)


def open_trade(suggestion: KalshiSuggestion) -> dict[str, Any] | None:
    """Paper-fill a YES/NO suggestion at entry_cents. Debits cash by cost."""
    init_db()
    if not suggestion.is_trade():
        return None
    assert suggestion.entry_cents is not None
    cost = _cost_usd(suggestion.entry_cents, suggestion.contracts)
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM paper_positions WHERE market_ticker = ?",
            (suggestion.market_ticker,),
        ).fetchone()
        if existing:
            logger.info("Already have position on %s — skip open", suggestion.market_ticker)
            return None
        state = conn.execute("SELECT cash_usd FROM paper_state WHERE id = 1").fetchone()
        cash = float(state["cash_usd"]) if state else 0.0
        if cost > cash + 1e-9:
            logger.warning(
                "Insufficient paper cash (have %.2f need %.2f) for %s",
                cash,
                cost,
                suggestion.market_ticker,
            )
            return None
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO paper_positions (
                opened_at, series, market_ticker, product_id, side, contracts,
                entry_cents, expiry_ts, rationale, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                now,
                suggestion.series,
                suggestion.market_ticker,
                suggestion.product_id,
                suggestion.side,
                int(suggestion.contracts),
                float(suggestion.entry_cents),
                suggestion.expiry_ts,
                suggestion.rationale,
            ),
        )
        pos_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE paper_state SET cash_usd = ?, updated_at = ? WHERE id = 1",
            (cash - cost, now),
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                ts, event, series, market_ticker, product_id, side, contracts,
                entry_cents, rationale
            ) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                suggestion.series,
                suggestion.market_ticker,
                suggestion.product_id,
                suggestion.side,
                int(suggestion.contracts),
                float(suggestion.entry_cents),
                suggestion.rationale,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM paper_positions WHERE id = ?", (pos_id,)
        ).fetchone()
        return dict(row) if row else None


def has_open_for_market(market_ticker: str) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM paper_positions WHERE market_ticker = ? AND status = 'open'",
            (market_ticker,),
        ).fetchone()
        return row is not None


def get_open_positions() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'open' ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def settle_position(market_ticker: str, result: str) -> dict[str, Any] | None:
    """Settle an open position from Kalshi result ('yes'|'no').

    PnL = (payout - entry_cents/100) * contracts
    payout = 1.0 if side matches result else 0.0
    """
    init_db()
    result_n = result.strip().lower()
    if result_n not in ("yes", "no"):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM paper_positions WHERE market_ticker = ? AND status = 'open'",
            (market_ticker,),
        ).fetchone()
        if row is None:
            return None
        side = str(row["side"]).upper()
        contracts = int(row["contracts"])
        entry_cents = float(row["entry_cents"])
        won = (side == "YES" and result_n == "yes") or (
            side == "NO" and result_n == "no"
        )
        payout_per = 1.0 if won else 0.0
        payout_usd = payout_per * contracts
        pnl_usd = (payout_per - entry_cents / 100.0) * contracts
        cost = _cost_usd(entry_cents, contracts)
        now = _now()
        state = conn.execute(
            "SELECT cash_usd, realized_pnl_usd FROM paper_state WHERE id = 1"
        ).fetchone()
        cash = float(state["cash_usd"]) if state else 0.0
        realized = float(state["realized_pnl_usd"]) if state else 0.0
        # Return cost basis into cash, then add payout (winner gets $1/contract).
        new_cash = cash + cost + pnl_usd
        # Equivalent: cash + payout_usd (since cost was already deducted at open).
        new_cash = cash + payout_usd
        conn.execute(
            """
            UPDATE paper_positions
            SET status = 'closed', result = ?, payout_usd = ?, pnl_usd = ?, closed_at = ?
            WHERE id = ?
            """,
            (result_n, payout_usd, pnl_usd, now, int(row["id"])),
        )
        conn.execute(
            """
            UPDATE paper_state
            SET cash_usd = ?, realized_pnl_usd = ?, updated_at = ?
            WHERE id = 1
            """,
            (new_cash, realized + pnl_usd, now),
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                ts, event, series, market_ticker, product_id, side, contracts,
                entry_cents, result, payout_usd, pnl_usd, rationale
            ) VALUES (?, 'settle', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                row["series"],
                market_ticker,
                row["product_id"],
                side,
                contracts,
                entry_cents,
                result_n,
                payout_usd,
                pnl_usd,
                row["rationale"],
            ),
        )
        conn.commit()
        closed = conn.execute(
            "SELECT * FROM paper_positions WHERE id = ?", (int(row["id"]),)
        ).fetchone()
        return dict(closed) if closed else None


def get_closed_positions(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM paper_positions
            WHERE status = 'closed'
            ORDER BY closed_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> dict[str, Any]:
    init_db()
    with _connect() as conn:
        state = conn.execute("SELECT * FROM paper_state WHERE id = 1").fetchone()
        closed = conn.execute(
            "SELECT pnl_usd FROM paper_positions WHERE status = 'closed'"
        ).fetchall()
        open_rows = conn.execute(
            "SELECT entry_cents, contracts FROM paper_positions WHERE status = 'open'"
        ).fetchall()

    starting = float(state["starting_usd"]) if state else float(config.PAPER_PORTFOLIO_VALUE)
    cash = float(state["cash_usd"]) if state else starting
    realized = float(state["realized_pnl_usd"]) if state else 0.0
    # Mark open cost as reserved capital (not MTM).
    open_cost = sum(_cost_usd(float(r["entry_cents"]), int(r["contracts"])) for r in open_rows)
    equity = cash + open_cost
    wins = sum(1 for r in closed if float(r["pnl_usd"] or 0) > 0)
    losses = sum(1 for r in closed if float(r["pnl_usd"] or 0) <= 0)
    n = wins + losses
    win_rate = (wins / n) if n else 0.0
    last10 = get_closed_positions(limit=10)
    return {
        "starting_usd": starting,
        "cash_usd": cash,
        "equity_usd": equity,
        "open_cost_usd": open_cost,
        "realized_pnl_usd": realized,
        "open_count": len(open_rows),
        "closed_count": n,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "last10": last10,
    }


def get_sizing_basis(
    spot_price: float | None = None,
    spots: dict[str, float] | None = None,
) -> tuple[float, float]:
    """Compatibility shim for validate.py — returns (equity, cash) from Kalshi paper book."""
    del spot_price, spots
    stats = get_stats()
    return float(stats["equity_usd"]), float(stats["cash_usd"])


def format_stats_text() -> str:
    s = get_stats()
    lines = [
        "Kalshi 15m paper stats",
        f"Equity: ${s['equity_usd']:.2f} (cash ${s['cash_usd']:.2f} + open cost ${s['open_cost_usd']:.2f})",
        f"Realized PnL: ${s['realized_pnl_usd']:+.2f}",
        f"Win rate: {s['win_rate']*100:.0f}% ({s['wins']}W / {s['losses']}L of {s['closed_count']})",
        f"Open: {s['open_count']}",
        "",
        "Last 10 settled:",
    ]
    if not s["last10"]:
        lines.append("  (none yet)")
    for t in s["last10"]:
        lines.append(
            f"  {t.get('product_id')} {t.get('side')} x{t.get('contracts')} "
            f"@{t.get('entry_cents'):.1f}¢ → {t.get('result')} "
            f"PnL ${float(t.get('pnl_usd') or 0):+.2f}"
        )
    return "\n".join(lines)


def format_positions_text() -> str:
    opens = get_open_positions()
    if not opens:
        return "No open Kalshi paper positions."
    lines = ["Open positions:"]
    for p in opens:
        lines.append(
            f"  {p['product_id']} {p['side']} x{p['contracts']} "
            f"@{p['entry_cents']:.1f}¢  {p['market_ticker']}  "
            f"exp {p.get('expiry_ts') or '?'}"
        )
    return "\n".join(lines)
