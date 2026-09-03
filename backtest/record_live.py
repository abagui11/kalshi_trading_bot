"""Hook helpers to record live Kalshi mids/results into the backtest archive.

Enable by setting ``bot_config.KALSHI_SNAPSHOT_DB`` (or env) to a SQLite path.
The live cycle calls :func:`maybe_record_from_context` / :func:`maybe_record_result`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.archive import init_archive, mid_as_of, record_snapshot, result_for

__all__ = [
    "init_archive",
    "record_snapshot",
    "mid_as_of",
    "result_for",
    "snapshot_db_path",
    "maybe_record_from_context",
    "maybe_record_result",
]


def snapshot_db_path() -> Path | None:
    try:
        import bot_config

        raw = getattr(bot_config, "KALSHI_SNAPSHOT_DB", None)
    except Exception:
        return None
    if not raw:
        return None
    return Path(str(raw))


def maybe_record_from_context(ctx: Any) -> None:
    """Persist a mid/spot snapshot from SharedCycleContext when archive is enabled."""
    path = snapshot_db_path()
    if path is None:
        return
    now = getattr(ctx, "now", None) or datetime.now(timezone.utc)
    try:
        record_snapshot(
            path,
            ts=now,
            series=str(ctx.series),
            market_ticker=str(ctx.market_ticker),
            product_id=getattr(ctx, "product_id", None),
            yes_mid_cents=getattr(ctx, "yes_mid_cents", None),
            spot=getattr(ctx, "spot", None),
            strike=getattr(ctx, "strike", None),
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Failed to record Kalshi snapshot")


def maybe_record_result(
    *,
    series: str,
    market_ticker: str,
    product_id: str | None,
    result: str,
    yes_mid_cents: float | None = None,
    spot: float | None = None,
    strike: float | None = None,
) -> None:
    path = snapshot_db_path()
    if path is None:
        return
    try:
        record_snapshot(
            path,
            ts=datetime.now(timezone.utc),
            series=series,
            market_ticker=market_ticker,
            product_id=product_id,
            yes_mid_cents=yes_mid_cents,
            spot=spot,
            strike=strike,
            result=result,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Failed to record Kalshi result")
