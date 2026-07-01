"""ICT-style order block detection from OHLC structure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from patterns.swing import Pivot, find_pivots

Direction = Literal["bullish", "bearish"]


@dataclass
class OrderBlock:
    direction: Direction
    low: float
    high: float
    start_ts: str
    end_ts: str
    displacement_ts: str


def _last_swing_before(pivots: list[Pivot], idx: int, kind: Literal["high", "low"]) -> Pivot | None:
    candidates = [p for p in pivots if p.kind == kind and p.idx < idx]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.idx)


def _candle_direction(row: pd.Series) -> Direction:
    return "bullish" if float(row["close"]) >= float(row["open"]) else "bearish"


def ob_from_displacement(
    df: pd.DataFrame,
    displacement_idx: int,
    direction: Direction,
) -> OrderBlock | None:
    """Last opposite candle before a displacement bar that breaks structure."""
    return _ob_from_displacement(df, displacement_idx, direction)


def _ob_from_displacement(
    df: pd.DataFrame,
    displacement_idx: int,
    direction: Direction,
) -> OrderBlock | None:
    """Last opposite candle before a displacement bar that breaks structure."""
    opposite: Direction = "bearish" if direction == "bullish" else "bullish"
    for j in range(displacement_idx - 1, max(displacement_idx - 30, -1), -1):
        row = df.iloc[j]
        if _candle_direction(row) != opposite:
            continue
        ts = df.index[j].strftime("%Y-%m-%dT%H:%M:%SZ")
        disp_ts = df.index[displacement_idx].strftime("%Y-%m-%dT%H:%M:%SZ")
        return OrderBlock(
            direction=direction,
            low=round(float(row["low"]), 2),
            high=round(float(row["high"]), 2),
            start_ts=ts,
            end_ts=ts,
            displacement_ts=disp_ts,
        )
    return None


def find_order_blocks(bars: list[dict], lookback: int = 60) -> list[OrderBlock]:
    """
    Scan recent bars for displacement through swing structure.
    Bullish OB: last down candle before close breaks above prior swing high.
    Bearish OB: last up candle before close breaks below prior swing low.
    """
    if len(bars) < lookback:
        return []

    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )

    pivots = find_pivots(df)
    blocks: list[OrderBlock] = []
    start_idx = max(0, len(df) - lookback)

    for i in range(start_idx, len(df)):
        close = float(df.iloc[i]["close"])
        swing_high = _last_swing_before(pivots, i, "high")
        swing_low = _last_swing_before(pivots, i, "low")

        if swing_high and close > swing_high.price:
            ob = ob_from_displacement(df, i, "bullish")
            if ob:
                blocks.append(ob)
        if swing_low and close < swing_low.price:
            ob = ob_from_displacement(df, i, "bearish")
            if ob:
                blocks.append(ob)

    # Keep most recent unique zones (by displacement time).
    seen: set[str] = set()
    unique: list[OrderBlock] = []
    for ob in reversed(blocks):
        key = f"{ob.direction}:{ob.displacement_ts}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(ob)
    unique.reverse()
    return unique[-5:]


def fib_zone_bounds(
    direction: Direction,
    low: float,
    high: float,
    fib_low: float = 0.618,
    fib_high: float = 0.786,
) -> tuple[float, float]:
    """0.618–0.786 entry sweet spot inside an OB (bullish: from bottom; bearish: from top)."""
    span = high - low
    if span <= 0:
        return low, high
    if direction == "bearish":
        z0 = low + span * (1 - fib_high)
        z1 = low + span * (1 - fib_low)
    else:
        z0 = low + span * fib_low
        z1 = low + span * fib_high
    return round(min(z0, z1), 2), round(max(z0, z1), 2)


def fib_level(direction: Direction, low: float, high: float, fib: float) -> float:
    """Single fib mark inside an OB (e.g. 0.618 entry level for bullish demand)."""
    span = high - low
    if span <= 0:
        return low
    if direction == "bearish":
        return round(low + span * (1 - fib), 2)
    return round(low + span * fib, 2)


def price_in_fib_zone(
    price: float,
    direction: Direction,
    low: float,
    high: float,
    fib_low: float = 0.618,
    fib_high: float = 0.786,
    tolerance_pct: float = 0.003,
) -> bool:
    """True when price is inside the fib sweet spot (with small tolerance for rounding)."""
    zone_low, zone_high = fib_zone_bounds(direction, low, high, fib_low, fib_high)
    pad = max(zone_high - zone_low, low * tolerance_pct, 1.0) * tolerance_pct
    return (zone_low - pad) <= price <= (zone_high + pad)


def price_in_ob(price: float, ob: OrderBlock, fib_low: float = 0.618, fib_high: float = 0.786) -> bool:
    """True when price sits in the OB discount/premium zone (fib slice of the block)."""
    return price_in_fib_zone(price, ob.direction, ob.low, ob.high, fib_low, fib_high)


def zones_overlap(
    low_a: float,
    high_a: float,
    low_b: float,
    high_b: float,
    min_overlap_ratio: float = 0.5,
) -> bool:
    """True when two price zones share enough horizontal overlap."""
    span_a = high_a - low_a
    span_b = high_b - low_b
    if span_a <= 0 or span_b <= 0:
        return False
    overlap = max(0.0, min(high_a, high_b) - max(low_a, low_b))
    ref = min(span_a, span_b)
    return overlap / ref >= min_overlap_ratio


def bounds_close(
    low_a: float,
    high_a: float,
    low_b: float,
    high_b: float,
    tol_pct: float = 0.01,
) -> bool:
    """True when two OB bounds are effectively the same zone."""
    ref = max(high_b - low_b, 1.0)
    return abs(low_a - low_b) <= ref * tol_pct and abs(high_a - high_b) <= ref * tol_pct


def order_block_dict_matches(
    ob_dict: dict,
    candidate: OrderBlock,
    *,
    min_overlap_ratio: float = 0.5,
) -> bool:
    """True when JSON order_block aligns with a detected H1 OrderBlock."""
    del min_overlap_ratio
    try:
        low = float(ob_dict["low"])
        high = float(ob_dict["high"])
    except (KeyError, TypeError, ValueError):
        return False
    if ob_dict.get("start_ts") and ob_dict["start_ts"] == candidate.start_ts:
        return True
    return bounds_close(low, high, candidate.low, candidate.high)


def find_matching_h1_ob(
    ob_dict: dict,
    order_blocks: list[OrderBlock],
    direction: Direction,
) -> OrderBlock | None:
    """Best matching detected H1 OB for a trade's order_block field."""
    matches = [
        ob
        for ob in order_blocks
        if ob.direction == direction and order_block_dict_matches(ob_dict, ob)
    ]
    if not matches:
        return None
    return max(matches, key=lambda ob: ob.displacement_ts)


def format_ob_with_fib(ob: OrderBlock) -> str:
    """Single-line summary with fib sweet spot for prompts."""
    z_low, z_high = fib_zone_bounds(ob.direction, ob.low, ob.high)
    return (
        f"{ob.direction} H1 OB {ob.low:,.2f}-{ob.high:,.2f} "
        f"(candle {ob.start_ts[:16]}, displacement {ob.displacement_ts[:16]}) "
        f"| fib 0.618-0.786: {z_low:,.2f}-{z_high:,.2f}"
    )
