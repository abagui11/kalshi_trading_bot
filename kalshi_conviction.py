"""Conviction × market-agree sizing for the always-on 15m product path."""

from __future__ import annotations

from typing import Any, Literal

import bot_config

Conviction = Literal["high", "med", "low"]


def market_lean(yes_mid_cents: float) -> str:
    """Market direction from YES mid: >=50 lean YES, else NO."""
    return "YES" if float(yes_mid_cents) >= 50.0 else "NO"


def market_agree(side: str, yes_mid_cents: float) -> bool:
    return side.upper() == market_lean(yes_mid_cents)


def resolve_side_with_fallback(
    *,
    ict_side: str | None,
    htf_bias: str | None,
    yes_mid_cents: float,
) -> tuple[str, str]:
    """Return (side, source) where source is ict|htf|market.

    Soft no_trade never blocks — fall back to HTF then market lean.
    """
    if ict_side in ("YES", "NO"):
        return str(ict_side), "ict"
    bias = (htf_bias or "").lower()
    if bias == "bull":
        return "YES", "htf"
    if bias == "bear":
        return "NO", "htf"
    return market_lean(float(yes_mid_cents)), "market"


def score_conviction(
    *,
    side_source: str,
    critic_downgraded: bool,
    chart_read_score: float | None,
    ict_side_present: bool,
) -> Conviction:
    """Map quality signals to high|med|low.

    Chart-read score is 0–1 from the Kalshi refine path.
    """
    if side_source != "ict" or critic_downgraded or not ict_side_present:
        return "low"
    score = float(chart_read_score) if chart_read_score is not None else 0.55
    high_floor = float(getattr(bot_config, "CONVICTION_HIGH_SCORE", 0.75))
    med_floor = float(getattr(bot_config, "CONVICTION_MED_SCORE", 0.45))
    if score >= high_floor:
        return "high"
    if score >= med_floor:
        return "med"
    return "low"


def base_deploy_pct(conviction: Conviction, agree: bool) -> float:
    """Matrix cell → fraction of book (before adverse boost / max clamp)."""
    matrix: dict[str, dict[str, float]] = {
        "high": {
            "agree": float(getattr(bot_config, "CONVICTION_HIGH_AGREE_PCT", 0.08)),
            "contra": float(getattr(bot_config, "CONVICTION_HIGH_CONTRA_PCT", 0.12)),
        },
        "med": {
            "agree": float(getattr(bot_config, "CONVICTION_MED_AGREE_PCT", 0.03)),
            "contra": float(getattr(bot_config, "CONVICTION_MED_CONTRA_PCT", 0.06)),
        },
        "low": {
            "agree": float(getattr(bot_config, "CONVICTION_LOW_AGREE_PCT", 0.005)),
            "contra": float(getattr(bot_config, "CONVICTION_LOW_CONTRA_PCT", 0.015)),
        },
    }
    cell = matrix.get(conviction, matrix["low"])
    return float(cell["agree" if agree else "contra"])


def clamp_deploy_pct(deploy_pct: float) -> float:
    cap = float(getattr(bot_config, "KALSHI_MAX_DEPLOY_PCT", 0.15))
    return max(0.0, min(float(deploy_pct), cap))


def compute_sizing(
    *,
    side: str,
    yes_mid_cents: float,
    conviction: Conviction,
    adverse_boost: float = 1.0,
) -> dict[str, Any]:
    """Full sizing plan: agree flag, base%, boost, final deploy% (≤ max)."""
    agree = market_agree(side, yes_mid_cents)
    base = base_deploy_pct(conviction, agree)
    boost = max(1.0, float(adverse_boost))
    max_boost = float(getattr(bot_config, "ADVERSE_SIZE_BOOST_MAX", 1.25))
    boost = min(boost, max_boost)
    raw = base * boost
    final = clamp_deploy_pct(raw)
    return {
        "conviction": conviction,
        "market_agree": agree,
        "market_lean": market_lean(yes_mid_cents),
        "base_deploy_pct": base,
        "adverse_boost": boost,
        "deploy_pct": final,
        "capped": raw > final + 1e-12,
    }
