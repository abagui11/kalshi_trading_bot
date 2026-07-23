"""Shared market + optional HTF bias for all strategy bots in a cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharedHtfBias:
    """One Claude/ICT bias snapshot reused by control + adverse."""

    ict_action: str
    ict_bias: str  # bull | bear | mixed | unknown
    ict_rationale: str
    gate_outcome: str | None
    htf_bias: str
    setup_tags: list[str] = field(default_factory=list)
    critic_downgraded: bool = False
    critic_passes: int = 0
    critic_findings: list[dict[str, Any]] = field(default_factory=list)
    chart_read_score: float | None = None
    ob_low: float | None = None
    ob_high: float | None = None
    structure_chart_path: str | None = None
    entry_chart_path: str | None = None
    side: str | None = None  # YES | NO | None if no_trade


@dataclass
class SharedCycleContext:
    """Everything a strategy needs without re-fetching markets/LLM."""

    series: str
    market: dict[str, Any]
    market_ticker: str
    product_id: str
    coinbase: str
    cycle_id: str
    expiry_ts: str | None
    yes_mid_cents: float | None
    spot: float | None
    strike: float | None
    sigma: float | None
    tau_sec: float | None
    spot_vs_strike_pct: float | None
    prior_5m_ret: float | None
    prior_15m_ret: float | None
    prior_1h_ret: float | None
    fair_yes_cents: float | None
    edge_cents: float | None
    m5_bars: list[dict[str, Any]] = field(default_factory=list)
    htf: SharedHtfBias | None = None
    near_decision: bool = False
    base_kwargs: dict[str, Any] = field(default_factory=dict)

    def with_bot(self, bot_id: str) -> dict[str, Any]:
        """Base kwargs for KalshiSuggestion / finalize, tagged with bot_id."""
        out = dict(self.base_kwargs)
        out["bot_id"] = bot_id
        return out
