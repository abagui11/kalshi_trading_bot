"""Last-2-minute quote logger — evidence collector for Dan's Eva #3 arb idea.

Premise under test: near window close, outsized last-2-minute swings against
the settled side are noise (Kalshi stops taking prices ~45s early), so a
favorite that touched 90% and dips to 75–85% is a discounted near-certain
winner. This script only *records*; it never places orders.

Every ~5s inside the final ~4.5 minutes of each KXBTC15M/KXETH15M window it
snapshots yes bid/ask, then after settlement records the result. Analyze with:

    sqlite3 lastmin.db "SELECT ..."

Run on the VPS as the kalshi-lastmin systemd service (deploy/kalshi-lastmin.service).
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kalshi_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lastmin")

DB_PATH = Path(__file__).resolve().parent.parent / "lastmin.db"
SERIES = ("KXBTC15M", "KXETH15M")
POLL_WINDOW_SEC = 270  # start polling at T-4.5m
POLL_EVERY_SEC = 5.0
IDLE_EVERY_SEC = 20.0


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            series TEXT NOT NULL,
            ticker TEXT NOT NULL,
            sec_to_expiry REAL,
            yes_bid REAL,
            yes_ask REAL,
            yes_mid REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            ticker TEXT PRIMARY KEY,
            series TEXT,
            expiry_ts TEXT,
            result TEXT,
            settled_ts TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quotes_ticker ON quotes(ticker)")
    conn.commit()
    return conn


def _parse_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dollars_to_cents(v) -> float | None:
    try:
        return float(v) * 100.0
    except (TypeError, ValueError):
        return None


def _active_market(series: str) -> tuple[str, datetime] | None:
    """(ticker, expiry) of the soonest-expiring open market."""
    try:
        markets = kalshi_client.get_markets(series, status="open", limit=5)
    except Exception:
        logger.exception("get_markets failed for %s", series)
        return None
    best = None
    now = datetime.now(timezone.utc)
    for m in markets:
        expiry = _parse_ts(
            m.get("close_time")
            or m.get("expected_expiration_time")
            or m.get("expiration_time")
        )
        if expiry is None or expiry <= now:
            continue
        if best is None or expiry < best[1]:
            best = (str(m.get("ticker") or ""), expiry)
    return best


def _snapshot(conn: sqlite3.Connection, series: str, ticker: str, expiry: datetime) -> None:
    try:
        market = kalshi_client.get_market(ticker)
    except Exception:
        logger.exception("get_market failed for %s", ticker)
        return
    bid = _dollars_to_cents(market.get("yes_bid_dollars"))
    ask = _dollars_to_cents(market.get("yes_ask_dollars"))
    mid = None
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
    elif bid is not None or ask is not None:
        mid = bid if bid is not None else ask
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO quotes (ts, series, ticker, sec_to_expiry, yes_bid, yes_ask, yes_mid) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            now.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            series,
            ticker,
            (expiry - now).total_seconds(),
            bid,
            ask,
            mid,
        ),
    )
    conn.commit()


def _record_result(conn: sqlite3.Connection, series: str, ticker: str, expiry: datetime) -> bool:
    try:
        result = kalshi_client.get_market_result(ticker)
    except Exception:
        logger.exception("result fetch failed for %s", ticker)
        return False
    if result not in ("yes", "no"):
        return False
    conn.execute(
        "INSERT OR REPLACE INTO results (ticker, series, expiry_ts, result, settled_ts) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            ticker,
            series,
            expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            result,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    conn.commit()
    logger.info("settled %s -> %s", ticker, result)
    return True


def main() -> None:
    conn = _db()
    active: dict[str, tuple[str, datetime]] = {}
    pending_results: dict[str, tuple[str, datetime, int]] = {}  # ticker -> (series, expiry, tries)

    logger.info("lastmin logger up, db=%s", DB_PATH)
    while True:
        now = datetime.now(timezone.utc)

        # Settle results for expired windows (retry a few times).
        for ticker in list(pending_results):
            series, expiry, tries = pending_results[ticker]
            if now < expiry:
                continue
            if _record_result(conn, series, ticker, expiry) or tries >= 12:
                del pending_results[ticker]
            else:
                pending_results[ticker] = (series, expiry, tries + 1)

        polled = False
        for series in SERIES:
            entry = active.get(series)
            if entry is None or entry[1] <= now:
                if entry is not None:
                    pending_results.setdefault(entry[0], (series, entry[1], 0))
                found = _active_market(series)
                if found is None:
                    continue
                active[series] = found
                entry = found
            ticker, expiry = entry
            sec_left = (expiry - now).total_seconds()
            if 0 < sec_left <= POLL_WINDOW_SEC:
                _snapshot(conn, series, ticker, expiry)
                polled = True

        time.sleep(POLL_EVERY_SEC if polled else IDLE_EVERY_SEC)


if __name__ == "__main__":
    main()
