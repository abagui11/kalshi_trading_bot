"""Swing Fail Pattern detection and event assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from patterns import config
from patterns.outcomes import (
    OutcomeA,
    score_outcome_a,
    score_outcome_b,
    score_outcome_c,
)
from patterns.swing import Pivot, find_pivots

Direction = Literal["bullish", "bearish"]


@dataclass
class SFPEvent:
    ts: str
    bar_idx: int
    timeframe: str
    direction: Direction
    swept_level: float
    sweep_depth_pct: float
    is_htf_level: bool
    volume_spike: bool
    into_ob_fvg: bool | None
    outcome_a: OutcomeA
    outcome_b: bool | None
    outcome_c: bool | None


def _volume_spike(df: pd.DataFrame, idx: int) -> bool:
    lookback = config.VOLUME_AVG_LOOKBACK
    start = max(0, idx - lookback)
    window = df.iloc[start:idx]
    if window.empty:
        return False
    avg_vol = float(window["volume"].mean())
    if avg_vol <= 0:
        return False
    return float(df.iloc[idx]["volume"]) >= avg_vol * config.VOLUME_SPIKE_MULT


def _is_htf_level(pivot: Pivot, prior_pivots: list[Pivot]) -> bool:
    """Pivot aligns with an earlier W1 swing within tolerance."""
    tol = config.HTF_LEVEL_TOLERANCE_PCT
    for prior in prior_pivots:
        if prior.idx >= pivot.idx:
            continue
        if prior.kind != pivot.kind:
            continue
        diff = abs(prior.price - pivot.price) / pivot.price
        if diff <= tol:
            return True
    return False


def _sweep_depth_pct(
    direction: Direction,
    bar: pd.Series,
    swept_level: float,
) -> float:
    if direction == "bearish":
        if swept_level <= 0:
            return 0.0
        return (float(bar["high"]) - swept_level) / swept_level
    if swept_level <= 0:
        return 0.0
    return (swept_level - float(bar["low"])) / swept_level


def detect_sfps(
    bars: list[dict],
    timeframe: str = "W1",
) -> list[SFPEvent]:
    """Detect SFP events on OHLC bars and score outcomes A/B/C."""
    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )

    pivots = find_pivots(df)
    n_bars = config.OUTCOME_N.get(timeframe, config.OUTCOME_N["W1"])
    events: list[SFPEvent] = []
    best_by_bar: dict[tuple[int, str], tuple[int, SFPEvent]] = {}

    for i in range(len(df)):
        bar = df.iloc[i]
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        prior_pivots = [p for p in pivots if p.idx < i]

        for pivot in prior_pivots:
            direction: Direction | None = None
            if pivot.kind == "high":
                min_high = pivot.price * (1 + config.MIN_SWEEP_PCT)
                if high > min_high and close < pivot.price:
                    direction = "bearish"
            elif pivot.kind == "low":
                max_low = pivot.price * (1 - config.MIN_SWEEP_PCT)
                if low < max_low and close > pivot.price:
                    direction = "bullish"

            if direction is None:
                continue

            key = (i, direction)
            if key in best_by_bar and pivot.idx <= best_by_bar[key][0]:
                continue

            depth = _sweep_depth_pct(direction, bar, pivot.price)
            outcome_a = score_outcome_a(df, i, direction, pivot.price, n_bars)
            if outcome_a == "neutral" and i + n_bars >= len(df):
                outcome_a = "pending"

            earlier = [p for p in pivots if p.idx < pivot.idx]
            event = SFPEvent(
                ts=df.index[i].strftime("%Y-%m-%dT%H:%M:%SZ"),
                bar_idx=i,
                timeframe=timeframe,
                direction=direction,
                swept_level=pivot.price,
                sweep_depth_pct=round(depth * 100, 3),
                is_htf_level=_is_htf_level(pivot, earlier),
                volume_spike=_volume_spike(df, i),
                into_ob_fvg=None,
                outcome_a=outcome_a,
                outcome_b=score_outcome_b(df, i, direction, n_bars),
                outcome_c=score_outcome_c(df, i, direction, n_bars, pivots=pivots),
            )
            best_by_bar[key] = (pivot.idx, event)

    return [pair[1] for pair in best_by_bar.values()]


def compute_stats(events: list[SFPEvent]) -> dict:
    """Aggregate headline and secondary stats."""
    reversals = sum(1 for e in events if e.outcome_a == "reversal")
    invalidations = sum(1 for e in events if e.outcome_a == "invalidation")
    neutral = sum(1 for e in events if e.outcome_a == "neutral")
    pending = sum(1 for e in events if e.outcome_a == "pending")
    scored = reversals + invalidations
    reversal_pct = (reversals / scored * 100) if scored > 0 else 0.0

    b_true = sum(1 for e in events if e.outcome_b is True)
    c_true = sum(1 for e in events if e.outcome_c is True)
    eligible_b = sum(1 for e in events if e.outcome_a != "pending")

    return {
        "total_sfps": len(events),
        "reversals": reversals,
        "invalidations": invalidations,
        "neutral": neutral,
        "pending": pending,
        "reversal_pct": round(reversal_pct, 1),
        "outcome_b_pct": round(b_true / eligible_b * 100, 1) if eligible_b else 0.0,
        "outcome_c_pct": round(c_true / eligible_b * 100, 1) if eligible_b else 0.0,
        "outcome_b_count": b_true,
        "outcome_c_count": c_true,
    }
