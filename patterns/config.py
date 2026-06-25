"""Tunable parameters for pattern detection and outcome scoring."""

from __future__ import annotations

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_SWEEP_PCT = 0.001  # 0.1%

OUTCOME_N: dict[str, int] = {
    "W1": 8,
    "D1": 10,
    "H12": 14,
    "H4": 14,
    "H1": 24,
}

MOVE_PCT_B = 0.05  # 5%
VOLUME_SPIKE_MULT = 1.5
VOLUME_AVG_LOOKBACK = 20

# HTF level: pivot within this % of a prior W1 swing counts as HTF alignment
HTF_LEVEL_TOLERANCE_PCT = 0.005  # 0.5%
