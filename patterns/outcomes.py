"""Outcome scoring for SFP events (A, B, C)."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from patterns import config
from patterns.swing import Pivot, find_pivots

OutcomeA = Literal["reversal", "invalidation", "pending", "neutral"]


def score_outcome_a(
    df: pd.DataFrame,
    event_idx: int,
    direction: Literal["bullish", "bearish"],
    swept_level: float,
    n_bars: int,
) -> OutcomeA:
    """
    Outcome A: invalidation if close past swept level first;
    reversal if close in SFP direction without prior invalidation.
    """
    start = event_idx + 1
    end = min(event_idx + 1 + n_bars, len(df))
    if start >= len(df):
        return "pending"

    for i in range(start, end):
        close = float(df.iloc[i]["close"])
        if direction == "bullish":
            if close < swept_level:
                return "invalidation"
            if close > swept_level:
                return "reversal"
        else:
            if close > swept_level:
                return "invalidation"
            if close < swept_level:
                return "reversal"

    return "neutral"


def score_outcome_b(
    df: pd.DataFrame,
    event_idx: int,
    direction: Literal["bullish", "bearish"],
    n_bars: int,
    move_pct: float | None = None,
) -> bool:
    """Did price move >= move_pct in SFP direction within N bars?"""
    threshold = move_pct if move_pct is not None else config.MOVE_PCT_B
    start = event_idx + 1
    end = min(event_idx + 1 + n_bars, len(df))
    if start >= len(df):
        return False

    ref_close = float(df.iloc[event_idx]["close"])
    window = df.iloc[start:end]
    if window.empty:
        return False

    if direction == "bullish":
        max_high = float(window["high"].max())
        move = (max_high - ref_close) / ref_close
    else:
        min_low = float(window["low"].min())
        move = (ref_close - min_low) / ref_close

    return move >= threshold


def _last_swing_before(pivots: list[Pivot], event_idx: int, kind: Literal["high", "low"]) -> Pivot | None:
    candidates = [p for p in pivots if p.kind == kind and p.idx < event_idx]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.idx)


def score_outcome_c(
    df: pd.DataFrame,
    event_idx: int,
    direction: Literal["bullish", "bearish"],
    n_bars: int,
    pivots: list[Pivot] | None = None,
) -> bool:
    """Structure break in SFP direction within N bars."""
    if pivots is None:
        pivots = find_pivots(df)

    start = event_idx + 1
    end = min(event_idx + 1 + n_bars, len(df))
    if start >= len(df):
        return False

    window = df.iloc[start:end]
    if window.empty:
        return False

    if direction == "bullish":
        prior = _last_swing_before(pivots, event_idx, "high")
        if prior is None:
            return False
        return float(window["high"].max()) > prior.price
    else:
        prior = _last_swing_before(pivots, event_idx, "low")
        if prior is None:
            return False
        return float(window["low"].min()) < prior.price
