"""M5 loading and 15m window construction for backtests."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import research

logger = logging.getLogger(__name__)

PRODUCT_TO_COINBASE = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}
PRODUCT_TO_SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
}


def _parse_ts(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    s = str(ts).strip()
    if s.endswith("Z"):
        if "." in s:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class WindowSpec:
    """One 15m binary window."""

    product_id: str
    series: str
    coinbase: str
    market_ticker: str
    open_ts: datetime
    expiry_ts: datetime
    strike: float


def load_m5(
    product_id: str,
    *,
    days: float = 7.0,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch Coinbase M5 bars covering the last `days` ending at `end`."""
    end = end or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=float(days))
    coinbase = PRODUCT_TO_COINBASE.get(product_id.upper(), f"{product_id.upper()}-USD")
    bars = research.fetch_coinbase_candles_range(
        "FIVE_MINUTE",
        int(start.timestamp()),
        int(end.timestamp()),
        product_id=coinbase,
    )
    logger.info(
        "Loaded %s M5 bars for %s (%s -> %s)",
        len(bars),
        coinbase,
        _iso(start),
        _iso(end),
    )
    return list(bars)


def bars_as_of(
    bars: Sequence[dict[str, Any]],
    when: datetime,
) -> list[dict[str, Any]]:
    """Bars with ts <= when (inclusive)."""
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    out: list[dict[str, Any]] = []
    for b in bars:
        try:
            ts = _parse_ts(str(b["ts"]))
        except (KeyError, ValueError, TypeError):
            continue
        if ts <= when:
            out.append(dict(b))
    return out


def spot_at(bars: Sequence[dict[str, Any]], when: datetime) -> float | None:
    asof = bars_as_of(bars, when)
    if not asof:
        return None
    try:
        return float(asof[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return None


def build_windows(
    bars: Sequence[dict[str, Any]],
    *,
    product_id: str,
) -> list[WindowSpec]:
    """Split M5 span into 15m windows; strike = spot at window open."""
    if len(bars) < 4:
        return []
    product = product_id.upper()
    series = PRODUCT_TO_SERIES.get(product, f"KX{product}15M")
    coinbase = PRODUCT_TO_COINBASE.get(product, f"{product}-USD")

    first = _parse_ts(str(bars[0]["ts"]))
    last = _parse_ts(str(bars[-1]["ts"]))
    # Align open to 15m boundary.
    open_min = (first.minute // 15) * 15
    cursor = first.replace(minute=open_min, second=0, microsecond=0)
    if cursor < first:
        cursor += timedelta(minutes=15)

    windows: list[WindowSpec] = []
    while cursor + timedelta(minutes=15) <= last + timedelta(minutes=5):
        expiry = cursor + timedelta(minutes=15)
        strike = spot_at(bars, cursor)
        if strike is not None and strike > 0:
            tag = cursor.strftime("%y%b%d%H%M").upper()
            ticker = f"{series}-{tag}"
            windows.append(
                WindowSpec(
                    product_id=product,
                    series=series,
                    coinbase=coinbase,
                    market_ticker=ticker,
                    open_ts=cursor,
                    expiry_ts=expiry,
                    strike=float(strike),
                )
            )
        cursor += timedelta(minutes=15)
    return windows


def settle_result_spot_vs_strike(
    spot: float | None,
    strike: float | None,
) -> str | None:
    """Proxy Kalshi result: yes if spot > strike at expiry."""
    if spot is None or strike is None or strike <= 0:
        return None
    return "yes" if float(spot) > float(strike) else "no"
