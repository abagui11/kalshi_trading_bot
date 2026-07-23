"""Kalshi binary paper books — one independent book per bot_id."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import config
from models import KalshiSuggestion

logger = logging.getLogger(__name__)

DEFAULT_BOT_ID = "control"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.LEDGER_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _bot_ids() -> tuple[str, ...]:
    try:
        import bot_config

        bots = tuple(bot_config.ENABLED_BOTS)
        if bots:
            return bots
    except Exception:
        pass
    return (DEFAULT_BOT_ID,)


def _starting_usd() -> float:
    try:
        import bot_config

        return float(bot_config.KALSHI_BANKROLL_USD)
    except Exception:
        return float(config.PAPER_PORTFOLIO_VALUE)


_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_state (
    bot_id TEXT PRIMARY KEY,
    starting_usd REAL NOT NULL,
    cash_usd REAL NOT NULL,
    realized_pnl_usd REAL NOT NULL DEFAULT 0,
    updated_at TEXT
);
"""

_POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL DEFAULT 'control',
    opened_at TEXT NOT NULL,
    series TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
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
    closed_at TEXT,
    chart_path TEXT
);
"""

_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL DEFAULT 'control',
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

_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS kalshi_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL DEFAULT 'control',
    ts TEXT NOT NULL,
    cycle_id TEXT,
    series TEXT,
    market_ticker TEXT,
    product_id TEXT,
    side TEXT,
    opened INTEGER NOT NULL DEFAULT 0,
    position_id INTEGER,
    rationale TEXT,
    yes_mid_cents REAL,
    entry_cents REAL,
    model_fair_yes_cents REAL,
    edge_cents REAL,
    fill_vs_mid_cents REAL,
    spot REAL,
    strike REAL,
    spot_vs_strike_pct REAL,
    tau_sec REAL,
    sigma REAL,
    prior_5m_ret REAL,
    prior_15m_ret REAL,
    prior_1h_ret REAL,
    ict_action TEXT,
    ict_bias TEXT,
    gate_outcome TEXT,
    trigger_type TEXT,
    ob_low REAL,
    ob_high REAL,
    h1_bias_tag TEXT,
    critic_passes INTEGER,
    critic_findings_json TEXT,
    critic_downgraded INTEGER NOT NULL DEFAULT 0,
    would_skip_reasons TEXT,
    chart_path TEXT
);
"""

_ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    series TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    product_id TEXT NOT NULL,
    side TEXT NOT NULL,
    contracts INTEGER NOT NULL,
    limit_cents REAL NOT NULL,
    cancel_at TEXT,
    expiry_ts TEXT,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    filled_at TEXT,
    cancelled_at TEXT,
    position_id INTEGER,
    cycle_id TEXT,
    subtype TEXT
);
"""

_WINDOW_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_window_state (
    bot_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    armed_side TEXT,
    arm_yes_mid REAL,
    arm_side_mid REAL,
    arm_spot REAL,
    arm_strike REAL,
    ict_bias TEXT,
    htf_bias TEXT,
    armed_at TEXT,
    meta_json TEXT,
    PRIMARY KEY (bot_id, market_ticker)
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _migrate_paper_state(conn: sqlite3.Connection) -> None:
    """Upgrade legacy id=1 paper_state to bot_id rows."""
    if not _table_exists(conn, "paper_state"):
        conn.execute(_STATE_SCHEMA)
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_state)").fetchall()}
    if "bot_id" in cols:
        return
    # Legacy schema: recreate with bot_id, migrate id=1 → control.
    rows = conn.execute("SELECT * FROM paper_state").fetchall()
    conn.execute("ALTER TABLE paper_state RENAME TO paper_state_legacy")
    conn.execute(_STATE_SCHEMA)
    start = _starting_usd()
    if rows:
        legacy = dict(rows[0])
        conn.execute(
            """
            INSERT INTO paper_state (bot_id, starting_usd, cash_usd, realized_pnl_usd, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_BOT_ID,
                float(legacy.get("starting_usd") or start),
                float(legacy.get("cash_usd") or start),
                float(legacy.get("realized_pnl_usd") or 0),
                legacy.get("updated_at") or _now(),
            ),
        )
    conn.execute("DROP TABLE IF EXISTS paper_state_legacy")


def _migrate_positions_unique(conn: sqlite3.Connection) -> None:
    """Ensure UNIQUE(bot_id, market_ticker) instead of ticker-only."""
    if not _table_exists(conn, "paper_positions"):
        conn.execute(_POSITIONS_SCHEMA)
        return
    _ensure_column(conn, "paper_positions", "bot_id", "TEXT NOT NULL DEFAULT 'control'")
    # Detect old UNIQUE(market_ticker) via index list / recreate when needed.
    idx_rows = conn.execute("PRAGMA index_list(paper_positions)").fetchall()
    needs_rebuild = False
    for idx in idx_rows:
        name = idx[1]
        unique = idx[2]
        if not unique:
            continue
        cols = [
            r[2]
            for r in conn.execute(f"PRAGMA index_info({name})").fetchall()
        ]
        if cols == ["market_ticker"]:
            needs_rebuild = True
            break
    # Also rebuild if no composite unique yet.
    has_composite = False
    for idx in idx_rows:
        if not idx[2]:
            continue
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{idx[1]}')").fetchall()]
        if set(cols) == {"bot_id", "market_ticker"}:
            has_composite = True
    if needs_rebuild or not has_composite:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_positions_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL DEFAULT 'control',
                opened_at TEXT NOT NULL,
                series TEXT NOT NULL,
                market_ticker TEXT NOT NULL,
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
                closed_at TEXT,
                chart_path TEXT,
                UNIQUE (bot_id, market_ticker)
            )
            """
        )
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions_v2'"
        ).fetchone()
        count_v2 = conn.execute("SELECT COUNT(*) FROM paper_positions_v2").fetchone()[0]
        if count_v2 == 0:
            cols = {
                r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()
            }
            select_bot = "bot_id" if "bot_id" in cols else "'control'"
            conn.execute(
                f"""
                INSERT INTO paper_positions_v2 (
                    id, bot_id, opened_at, series, market_ticker, product_id, side,
                    contracts, entry_cents, expiry_ts, rationale, status, result,
                    payout_usd, pnl_usd, closed_at, chart_path
                )
                SELECT id, {select_bot}, opened_at, series, market_ticker, product_id, side,
                    contracts, entry_cents, expiry_ts, rationale, status, result,
                    payout_usd, pnl_usd, closed_at, chart_path
                FROM paper_positions
                """
            )
        conn.execute("DROP TABLE paper_positions")
        conn.execute("ALTER TABLE paper_positions_v2 RENAME TO paper_positions")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_positions_bot_ticker
        ON paper_positions (bot_id, market_ticker)
        """
    )


def _ensure_bot_state(conn: sqlite3.Connection, bot_id: str) -> None:
    row = conn.execute(
        "SELECT bot_id FROM paper_state WHERE bot_id = ?", (bot_id,)
    ).fetchone()
    if row is None:
        start = _starting_usd()
        conn.execute(
            """
            INSERT INTO paper_state (bot_id, starting_usd, cash_usd, realized_pnl_usd, updated_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (bot_id, start, start, _now()),
        )


def init_db() -> None:
    with _connect() as conn:
        _migrate_paper_state(conn)
        conn.execute(_STATE_SCHEMA)
        conn.execute(_POSITIONS_SCHEMA)
        _migrate_positions_unique(conn)
        conn.execute(_TRADES_SCHEMA)
        conn.execute(_DECISIONS_SCHEMA)
        conn.execute(_ORDERS_SCHEMA)
        conn.execute(_WINDOW_STATE_SCHEMA)
        _ensure_column(conn, "paper_positions", "chart_path", "TEXT")
        _ensure_column(conn, "paper_positions", "bot_id", "TEXT NOT NULL DEFAULT 'control'")
        _ensure_column(conn, "paper_trades", "bot_id", "TEXT NOT NULL DEFAULT 'control'")
        _ensure_column(conn, "kalshi_decisions", "bot_id", "TEXT NOT NULL DEFAULT 'control'")
        for col, decl in (
            ("structure_chart_path", "TEXT"),
            ("entry_chart_path", "TEXT"),
            ("setup_tags", "TEXT"),
            ("skip_codes", "TEXT"),
            ("chart_read_score", "REAL"),
            ("seconds_to_expiry", "REAL"),
            ("trigger_name", "TEXT"),
        ):
            _ensure_column(conn, "kalshi_decisions", col, decl)
        for bot_id in _bot_ids():
            _ensure_bot_state(conn, bot_id)
        # Keep legacy single-book row available as control.
        _ensure_bot_state(conn, DEFAULT_BOT_ID)
        conn.commit()


def reset_book(
    starting_usd: float | None = None,
    *,
    bot_id: str | None = None,
) -> None:
    start = float(starting_usd if starting_usd is not None else _starting_usd())
    bots = (bot_id,) if bot_id else _bot_ids()
    with _connect() as conn:
        if bot_id:
            conn.execute("DELETE FROM paper_positions WHERE bot_id = ?", (bot_id,))
            conn.execute("DELETE FROM paper_trades WHERE bot_id = ?", (bot_id,))
            conn.execute("DELETE FROM paper_orders WHERE bot_id = ?", (bot_id,))
            conn.execute("DELETE FROM bot_window_state WHERE bot_id = ?", (bot_id,))
            conn.execute("DELETE FROM paper_state WHERE bot_id = ?", (bot_id,))
        else:
            conn.execute("DELETE FROM paper_positions")
            conn.execute("DELETE FROM paper_trades")
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM bot_window_state")
            conn.execute("DELETE FROM paper_state")
        for bid in bots:
            conn.execute(
                """
                INSERT INTO paper_state (bot_id, starting_usd, cash_usd, realized_pnl_usd, updated_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (bid, start, start, _now()),
            )
        conn.commit()


def _cost_usd(entry_cents: float, contracts: int) -> float:
    return (float(entry_cents) / 100.0) * int(contracts)


def open_trade(suggestion: KalshiSuggestion) -> dict[str, Any] | None:
    """Paper-fill a YES/NO suggestion at entry_cents. Debits that bot's cash."""
    init_db()
    if not suggestion.is_trade():
        return None
    assert suggestion.entry_cents is not None
    bot_id = suggestion.bot_id or DEFAULT_BOT_ID
    cost = _cost_usd(suggestion.entry_cents, suggestion.contracts)
    with _connect() as conn:
        _ensure_bot_state(conn, bot_id)
        existing = conn.execute(
            """
            SELECT id FROM paper_positions
            WHERE bot_id = ? AND market_ticker = ? AND status = 'open'
            """,
            (bot_id, suggestion.market_ticker),
        ).fetchone()
        if existing:
            logger.info(
                "Already have %s position on %s — skip open",
                bot_id,
                suggestion.market_ticker,
            )
            return None
        state = conn.execute(
            "SELECT cash_usd FROM paper_state WHERE bot_id = ?", (bot_id,)
        ).fetchone()
        cash = float(state["cash_usd"]) if state else 0.0
        if cost > cash + 1e-9:
            logger.warning(
                "Insufficient paper cash for %s (have %.2f need %.2f) on %s",
                bot_id,
                cash,
                cost,
                suggestion.market_ticker,
            )
            return None
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO paper_positions (
                bot_id, opened_at, series, market_ticker, product_id, side, contracts,
                entry_cents, expiry_ts, rationale, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                bot_id,
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
            "UPDATE paper_state SET cash_usd = ?, updated_at = ? WHERE bot_id = ?",
            (cash - cost, now, bot_id),
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                bot_id, ts, event, series, market_ticker, product_id, side, contracts,
                entry_cents, rationale
            ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_id,
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


def place_limit_order(
    suggestion: KalshiSuggestion,
    *,
    subtype: str | None = None,
) -> dict[str, Any] | None:
    """Park a working paper limit; filled later when mid reaches limit."""
    init_db()
    if suggestion.side not in ("YES", "NO") or suggestion.entry_cents is None:
        return None
    if suggestion.contracts < 1:
        return None
    bot_id = suggestion.bot_id or DEFAULT_BOT_ID
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM paper_orders
            WHERE bot_id = ? AND market_ticker = ? AND status = 'pending'
            """,
            (bot_id, suggestion.market_ticker),
        ).fetchone()
        if existing:
            return None
        if has_open_for_market(suggestion.market_ticker, bot_id=bot_id):
            return None
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO paper_orders (
                bot_id, created_at, series, market_ticker, product_id, side,
                contracts, limit_cents, cancel_at, expiry_ts, rationale, status,
                cycle_id, subtype
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                bot_id,
                now,
                suggestion.series,
                suggestion.market_ticker,
                suggestion.product_id,
                suggestion.side,
                int(suggestion.contracts),
                float(suggestion.entry_cents),
                suggestion.cancel_at_ts,
                suggestion.expiry_ts,
                suggestion.rationale,
                suggestion.cycle_id,
                subtype,
            ),
        )
        oid = int(cur.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM paper_orders WHERE id = ?", (oid,)
        ).fetchone()
        return dict(row) if row else None


def process_pending_orders(
    *,
    yes_mids: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Fill pending limits when side mid ≤ limit; cancel past cancel_at."""
    init_db()
    yes_mids = yes_mids or {}
    events: list[dict[str, Any]] = []
    now = _now()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_orders WHERE status = 'pending' ORDER BY id ASC"
        ).fetchall()
    for row in rows:
        order = dict(row)
        bot_id = str(order["bot_id"])
        ticker = str(order["market_ticker"])
        cancel_at = order.get("cancel_at")
        if cancel_at and str(cancel_at) <= now:
            with _connect() as conn:
                conn.execute(
                    """
                    UPDATE paper_orders
                    SET status = 'cancelled', cancelled_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, int(order["id"])),
                )
                conn.commit()
            events.append({"event": "cancelled", "order": order})
            continue
        mid = yes_mids.get(ticker)
        if mid is None:
            continue
        side = str(order["side"]).upper()
        side_mid = float(mid) if side == "YES" else 100.0 - float(mid)
        limit = float(order["limit_cents"])
        if side_mid > limit + 1e-9:
            continue
        sug = KalshiSuggestion(
            series=str(order["series"]),
            market_ticker=ticker,
            side=side,
            contracts=int(order["contracts"]),
            entry_cents=limit,
            expiry_ts=order.get("expiry_ts"),
            rationale=str(order.get("rationale") or "limit fill"),
            product_id=str(order["product_id"]),
            mid_cents=float(mid),
            bot_id=bot_id,
            cycle_id=order.get("cycle_id"),
            trigger_type="lottery_ticket",
            trigger_name=str(order.get("subtype") or "limit_fill"),
        )
        opened = open_trade(sug)
        with _connect() as conn:
            if opened:
                conn.execute(
                    """
                    UPDATE paper_orders
                    SET status = 'filled', filled_at = ?, position_id = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, int(opened["id"]), int(order["id"])),
                )
                events.append({"event": "filled", "order": order, "position": opened})
            else:
                conn.execute(
                    """
                    UPDATE paper_orders
                    SET status = 'cancelled', cancelled_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, int(order["id"])),
                )
                events.append({"event": "cancelled_no_cash", "order": order})
            conn.commit()
    return events


def has_open_for_market(
    market_ticker: str,
    *,
    bot_id: str | None = None,
) -> bool:
    init_db()
    with _connect() as conn:
        if bot_id:
            row = conn.execute(
                """
                SELECT 1 FROM paper_positions
                WHERE bot_id = ? AND market_ticker = ? AND status = 'open'
                """,
                (bot_id, market_ticker),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1 FROM paper_positions
                WHERE market_ticker = ? AND status = 'open'
                """,
                (market_ticker,),
            ).fetchone()
        return row is not None


def has_pending_order(
    market_ticker: str,
    *,
    bot_id: str,
) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM paper_orders
            WHERE bot_id = ? AND market_ticker = ? AND status = 'pending'
            """,
            (bot_id, market_ticker),
        ).fetchone()
        return row is not None


def get_open_positions(*, bot_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        if bot_id:
            rows = conn.execute(
                """
                SELECT * FROM paper_positions
                WHERE status = 'open' AND bot_id = ?
                ORDER BY id ASC
                """,
                (bot_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE status = 'open' ORDER BY id ASC"
            ).fetchall()
        return [dict(r) for r in rows]


def settle_position(
    market_ticker: str,
    result: str,
    *,
    bot_id: str | None = None,
    position_id: int | None = None,
) -> dict[str, Any] | None:
    """Settle an open position from Kalshi result ('yes'|'no')."""
    init_db()
    result_n = result.strip().lower()
    if result_n not in ("yes", "no"):
        return None
    with _connect() as conn:
        if position_id is not None:
            row = conn.execute(
                "SELECT * FROM paper_positions WHERE id = ? AND status = 'open'",
                (int(position_id),),
            ).fetchone()
        elif bot_id:
            row = conn.execute(
                """
                SELECT * FROM paper_positions
                WHERE bot_id = ? AND market_ticker = ? AND status = 'open'
                """,
                (bot_id, market_ticker),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM paper_positions
                WHERE market_ticker = ? AND status = 'open'
                ORDER BY id ASC LIMIT 1
                """,
                (market_ticker,),
            ).fetchone()
        if row is None:
            return None
        pos_bot = str(row["bot_id"] if "bot_id" in row.keys() else DEFAULT_BOT_ID)
        side = str(row["side"]).upper()
        contracts = int(row["contracts"])
        entry_cents = float(row["entry_cents"])
        won = (side == "YES" and result_n == "yes") or (
            side == "NO" and result_n == "no"
        )
        payout_per = 1.0 if won else 0.0
        payout_usd = payout_per * contracts
        pnl_usd = (payout_per - entry_cents / 100.0) * contracts
        now = _now()
        _ensure_bot_state(conn, pos_bot)
        state = conn.execute(
            "SELECT cash_usd, realized_pnl_usd FROM paper_state WHERE bot_id = ?",
            (pos_bot,),
        ).fetchone()
        cash = float(state["cash_usd"]) if state else 0.0
        realized = float(state["realized_pnl_usd"]) if state else 0.0
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
            WHERE bot_id = ?
            """,
            (new_cash, realized + pnl_usd, now, pos_bot),
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                bot_id, ts, event, series, market_ticker, product_id, side, contracts,
                entry_cents, result, payout_usd, pnl_usd, rationale
            ) VALUES (?, ?, 'settle', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pos_bot,
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


def get_closed_positions(
    limit: int = 50,
    *,
    bot_id: str | None = None,
) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        if bot_id:
            rows = conn.execute(
                """
                SELECT * FROM paper_positions
                WHERE status = 'closed' AND bot_id = ?
                ORDER BY closed_at DESC, id DESC
                LIMIT ?
                """,
                (bot_id, limit),
            ).fetchall()
        else:
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


def get_stats(*, bot_id: str | None = None) -> dict[str, Any]:
    init_db()
    bid = bot_id or DEFAULT_BOT_ID
    with _connect() as conn:
        _ensure_bot_state(conn, bid)
        conn.commit()
        state = conn.execute(
            "SELECT * FROM paper_state WHERE bot_id = ?", (bid,)
        ).fetchone()
        closed = conn.execute(
            """
            SELECT pnl_usd FROM paper_positions
            WHERE status = 'closed' AND bot_id = ?
            """,
            (bid,),
        ).fetchall()
        open_rows = conn.execute(
            """
            SELECT entry_cents, contracts FROM paper_positions
            WHERE status = 'open' AND bot_id = ?
            """,
            (bid,),
        ).fetchall()

    starting = float(state["starting_usd"]) if state else _starting_usd()
    cash = float(state["cash_usd"]) if state else starting
    realized = float(state["realized_pnl_usd"]) if state else 0.0
    open_cost = sum(
        _cost_usd(float(r["entry_cents"]), int(r["contracts"])) for r in open_rows
    )
    equity = cash + open_cost
    wins = sum(1 for r in closed if float(r["pnl_usd"] or 0) > 0)
    losses = sum(1 for r in closed if float(r["pnl_usd"] or 0) <= 0)
    n = wins + losses
    win_rate = (wins / n) if n else 0.0
    last10 = get_closed_positions(limit=10, bot_id=bid)
    return {
        "bot_id": bid,
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


def get_all_bot_stats() -> list[dict[str, Any]]:
    init_db()
    try:
        import bot_config

        names = dict(bot_config.BOT_DISPLAY_NAMES)
    except Exception:
        names = {}
    out = []
    for bid in _bot_ids():
        s = get_stats(bot_id=bid)
        s["display_name"] = names.get(bid, bid)
        out.append(s)
    # Rank by realized PnL desc
    out.sort(key=lambda x: float(x.get("realized_pnl_usd") or 0), reverse=True)
    return out


def get_sizing_basis(
    spot_price: float | None = None,
    spots: dict[str, float] | None = None,
) -> tuple[float, float]:
    """Compatibility shim for validate.py — returns (equity, cash) from control book."""
    del spot_price, spots
    stats = get_stats(bot_id=DEFAULT_BOT_ID)
    return float(stats["equity_usd"]), float(stats["cash_usd"])


def format_stats_text(*, bot_id: str | None = None) -> str:
    if bot_id:
        bots = [get_stats(bot_id=bot_id)]
    else:
        bots = get_all_bot_stats()
    lines = ["Kalshi 15m paper stats (multi-bot)"]
    for s in bots:
        lines.append(
            f"[{s.get('bot_id')}] Equity ${s['equity_usd']:.2f} · "
            f"PnL ${s['realized_pnl_usd']:+.2f} · "
            f"{s['win_rate']*100:.0f}% ({s['wins']}W/{s['losses']}L) · "
            f"open {s['open_count']}"
        )
    return "\n".join(lines)


def format_positions_text(*, bot_id: str | None = None) -> str:
    opens = get_open_positions(bot_id=bot_id)
    if not opens:
        return "No open Kalshi paper positions."
    lines = ["Open positions:"]
    for p in opens:
        lines.append(
            f"  [{p.get('bot_id', DEFAULT_BOT_ID)}] {p['product_id']} {p['side']} "
            f"x{p['contracts']} @{p['entry_cents']:.1f}¢  {p['market_ticker']}  "
            f"exp {p.get('expiry_ts') or '?'}"
        )
    return "\n".join(lines)


def set_position_chart_path(position_id: int, chart_path: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE paper_positions SET chart_path = ? WHERE id = ?",
            (chart_path, int(position_id)),
        )
        conn.commit()


def log_decision(suggestion: KalshiSuggestion) -> int:
    """Persist every Kalshi decision (trade or skip) for audit/export."""
    init_db()
    bot_id = suggestion.bot_id or DEFAULT_BOT_ID
    fill_vs_mid = None
    if suggestion.entry_cents is not None and suggestion.mid_cents is not None:
        if suggestion.side == "YES":
            fill_vs_mid = float(suggestion.entry_cents) - float(suggestion.mid_cents)
        elif suggestion.side == "NO":
            fill_vs_mid = float(suggestion.entry_cents) - (
                100.0 - float(suggestion.mid_cents)
            )

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO kalshi_decisions (
                bot_id, ts, cycle_id, series, market_ticker, product_id, side, opened,
                position_id, rationale, yes_mid_cents, entry_cents,
                model_fair_yes_cents, edge_cents, fill_vs_mid_cents,
                spot, strike, spot_vs_strike_pct, tau_sec, sigma,
                prior_5m_ret, prior_15m_ret, prior_1h_ret,
                ict_action, ict_bias, gate_outcome, trigger_type,
                ob_low, ob_high, h1_bias_tag,
                critic_passes, critic_findings_json, critic_downgraded,
                would_skip_reasons, chart_path,
                structure_chart_path, entry_chart_path, setup_tags, skip_codes,
                chart_read_score, seconds_to_expiry, trigger_name
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                bot_id,
                _now(),
                suggestion.cycle_id,
                suggestion.series,
                suggestion.market_ticker,
                suggestion.product_id,
                suggestion.side,
                1 if suggestion.opened else 0,
                suggestion.position_id,
                suggestion.rationale,
                suggestion.mid_cents,
                suggestion.entry_cents,
                suggestion.fair_yes_cents,
                suggestion.edge_cents,
                fill_vs_mid,
                suggestion.spot,
                suggestion.strike,
                suggestion.spot_vs_strike_pct,
                suggestion.tau_sec,
                suggestion.sigma,
                suggestion.prior_5m_ret,
                suggestion.prior_15m_ret,
                suggestion.prior_1h_ret,
                suggestion.ict_action,
                suggestion.ict_bias,
                suggestion.gate_outcome,
                suggestion.trigger_type,
                suggestion.ob_low,
                suggestion.ob_high,
                suggestion.h1_bias_tag,
                int(suggestion.critic_passes),
                json.dumps(suggestion.critic_findings or []),
                1 if suggestion.critic_downgraded else 0,
                json.dumps(list(suggestion.would_skip_reasons or [])),
                suggestion.chart_path,
                suggestion.structure_chart_path,
                suggestion.entry_chart_path,
                json.dumps(list(suggestion.setup_tags or [])),
                json.dumps(list(suggestion.skip_codes or [])),
                suggestion.chart_read_score,
                suggestion.seconds_to_expiry,
                suggestion.trigger_name,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_decisions(
    limit: int = 200,
    *,
    bot_id: str | None = None,
) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        if bot_id:
            rows = conn.execute(
                """
                SELECT * FROM kalshi_decisions
                WHERE bot_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (bot_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM kalshi_decisions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# --- Window arm state (adverse bot) ---


def set_window_arm(
    *,
    bot_id: str,
    market_ticker: str,
    armed_side: str,
    arm_yes_mid: float | None,
    arm_side_mid: float | None,
    arm_spot: float | None,
    arm_strike: float | None,
    ict_bias: str | None,
    htf_bias: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_window_state (
                bot_id, market_ticker, armed_side, arm_yes_mid, arm_side_mid,
                arm_spot, arm_strike, ict_bias, htf_bias, armed_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bot_id, market_ticker) DO UPDATE SET
                armed_side=excluded.armed_side,
                arm_yes_mid=excluded.arm_yes_mid,
                arm_side_mid=excluded.arm_side_mid,
                arm_spot=excluded.arm_spot,
                arm_strike=excluded.arm_strike,
                ict_bias=excluded.ict_bias,
                htf_bias=excluded.htf_bias,
                armed_at=excluded.armed_at,
                meta_json=excluded.meta_json
            """,
            (
                bot_id,
                market_ticker,
                armed_side,
                arm_yes_mid,
                arm_side_mid,
                arm_spot,
                arm_strike,
                ict_bias,
                htf_bias,
                _now(),
                json.dumps(meta or {}),
            ),
        )
        conn.commit()


def get_window_arm(bot_id: str, market_ticker: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM bot_window_state
            WHERE bot_id = ? AND market_ticker = ?
            """,
            (bot_id, market_ticker),
        ).fetchone()
        return dict(row) if row else None


def clear_window_arm(bot_id: str, market_ticker: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM bot_window_state WHERE bot_id = ? AND market_ticker = ?",
            (bot_id, market_ticker),
        )
        conn.commit()


def set_shared_htf_bias(
    market_ticker: str,
    payload: dict[str, Any],
) -> None:
    """Store shared HTF bias for the window under bot_id='_shared'."""
    set_window_arm(
        bot_id="_shared",
        market_ticker=market_ticker,
        armed_side=str(payload.get("side") or ""),
        arm_yes_mid=payload.get("yes_mid"),
        arm_side_mid=None,
        arm_spot=payload.get("spot"),
        arm_strike=payload.get("strike"),
        ict_bias=payload.get("ict_bias"),
        htf_bias=payload.get("htf_bias"),
        meta=payload,
    )


def get_shared_htf_bias(market_ticker: str) -> dict[str, Any] | None:
    row = get_window_arm("_shared", market_ticker)
    if not row:
        return None
    meta_raw = row.get("meta_json")
    try:
        meta = json.loads(meta_raw) if meta_raw else {}
    except (TypeError, json.JSONDecodeError):
        meta = {}
    return meta or dict(row)
