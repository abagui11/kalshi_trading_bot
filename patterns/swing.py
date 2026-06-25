"""Swing pivot detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from patterns import config


@dataclass
class Pivot:
    idx: int
    ts: str
    price: float
    kind: Literal["high", "low"]


def find_pivots(df: pd.DataFrame, left: int | None = None, right: int | None = None) -> list[Pivot]:
    """Find swing highs/lows with L bars on each side."""
    l = left if left is not None else config.PIVOT_LEFT
    r = right if right is not None else config.PIVOT_RIGHT
    pivots: list[Pivot] = []
    n = len(df)
    if n < l + r + 1:
        return pivots

    highs = df["high"].values
    lows = df["low"].values
    index = df.index

    for i in range(l, n - r):
        window_high = highs[i - l : i + r + 1]
        window_low = lows[i - l : i + r + 1]
        if highs[i] == window_high.max() and highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            ts = index[i].strftime("%Y-%m-%dT%H:%M:%SZ")
            pivots.append(Pivot(idx=i, ts=ts, price=float(highs[i]), kind="high"))
        if lows[i] == window_low.min() and lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            ts = index[i].strftime("%Y-%m-%dT%H:%M:%SZ")
            pivots.append(Pivot(idx=i, ts=ts, price=float(lows[i]), kind="low"))

    return pivots
