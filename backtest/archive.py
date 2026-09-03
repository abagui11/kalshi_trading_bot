"""Kalshi mid/result archive for higher-fidelity backtests.

Live agent can record snapshots; the runner prefers archive mids/results when present.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kalshi_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    series TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    product_id TEXT,
    yes_mid_cents REAL,
    spot REAL,
    strike REAL,
    result TEXT,
    UNIQUE (ts, market_ticker)
);
CREATE INDEX IF NOT EXISTS idx_kalshi_snap_ticker_ts
    ON kalshi_snapshots (market_ticker, ts);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_archive(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def record_snapshot(
    path: Path,
    *,
    ts: str | datetime,
    series: str,
    market_ticker: str,
    product_id: str | None = None,
    yes_mid_cents: float | None = None,
    spot: float | None = None,
    strike: float | None = None,
    result: str | None = None,
) -> None:
    """Upsert a live or replay snapshot."""
    init_archive(path)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_s = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts_s = str(ts)
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO kalshi_snapshots (
                ts, series, market_ticker, product_id, yes_mid_cents, spot, strike, result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ts, market_ticker) DO UPDATE SET
                yes_mid_cents=COALESCE(excluded.yes_mid_cents, yes_mid_cents),
                spot=COALESCE(excluded.spot, spot),
                strike=COALESCE(excluded.strike, strike),
                result=COALESCE(excluded.result, result),
                product_id=COALESCE(excluded.product_id, product_id)
            """,
            (
                ts_s,
                series,
                market_ticker,
                product_id,
                yes_mid_cents,
                spot,
                strike,
                result,
            ),
        )
        conn.commit()


def mid_as_of(
    path: Path,
    market_ticker: str,
    when: datetime,
) -> float | None:
    """Latest archived yes mid at or before `when`."""
    if not path.exists():
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    ts_s = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT yes_mid_cents FROM kalshi_snapshots
            WHERE market_ticker = ? AND ts <= ? AND yes_mid_cents IS NOT NULL
            ORDER BY ts DESC LIMIT 1
            """,
            (market_ticker, ts_s),
        ).fetchone()
    if row is None:
        return None
    try:
        return float(row["yes_mid_cents"])
    except (TypeError, ValueError):
        return None


def result_for(
    path: Path,
    market_ticker: str,
) -> str | None:
    if not path.exists():
        return None
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT result FROM kalshi_snapshots
            WHERE market_ticker = ? AND result IS NOT NULL AND result != ''
            ORDER BY ts DESC LIMIT 1
            """,
            (market_ticker,),
        ).fetchone()
    if row is None:
        return None
    r = str(row["result"]).strip().lower()
    return r if r in ("yes", "no") else None


def load_recorded_biases(path: Path) -> dict[str, dict[str, Any]]:
    """Optional sidecar: JSON/CSV of recorded ICT biases keyed by market_ticker.

    This helper reads a simple JSON mapping written by export tooling:
    { "TICKER": {"side": "NO", "htf_bias": "bear", "ict_bias": "bear", ...}, ... }
    """
    import json

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
