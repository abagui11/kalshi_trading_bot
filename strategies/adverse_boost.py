"""Adverse cheapness → size multiplier only (never a trade/no-trade gate)."""

from __future__ import annotations

from typing import Any

import bot_config
import kalshi_fair
import kalshi_triggers


def adverse_size_boost(
    *,
    side: str,
    yes_mid_cents: float,
    fair_yes_cents: float | None = None,
    edge_cents: float | None = None,
) -> tuple[float, dict[str, Any]]:
    """Return (multiplier, audit) in [1.0, ADVERSE_SIZE_BOOST_MAX].

    Boosts when the bought side is cheap vs 50¢ and/or fair favors the side.
    """
    max_boost = float(getattr(bot_config, "ADVERSE_SIZE_BOOST_MAX", 1.25))
    side_mid = kalshi_triggers.side_mid_cents(side, float(yes_mid_cents))
    audit: dict[str, Any] = {
        "side_mid_cents": side_mid,
        "cheap_vs_50": max(0.0, 50.0 - side_mid),
    }

    # Linear scale: side at 50¢ → 1.0; side at 25¢ → max_boost.
    cheap_span = float(getattr(bot_config, "ADVERSE_BOOST_CHEAP_SPAN_CENTS", 25.0))
    cheap_frac = 0.0
    if cheap_span > 0 and side_mid < 50.0:
        cheap_frac = min(1.0, (50.0 - side_mid) / cheap_span)

    edge_frac = 0.0
    edge = edge_cents
    if edge is None and fair_yes_cents is not None:
        edge = float(fair_yes_cents) - float(yes_mid_cents)
    if edge is not None and kalshi_fair.side_agrees_with_edge(side, float(edge)):
        min_edge = float(getattr(bot_config, "KALSHI_MIN_EDGE_CENTS", 8.0))
        # Full edge contribution once |edge| >= 2× min edge.
        edge_frac = min(1.0, abs(float(edge)) / max(min_edge * 2.0, 1.0))
        audit["edge_cents"] = float(edge)
        audit["edge_favors_side"] = True
    else:
        audit["edge_favors_side"] = False

    # Weight cheapness more than edge (product DNA: disproportion entry).
    strength = max(cheap_frac, 0.65 * cheap_frac + 0.35 * edge_frac)
    boost = 1.0 + (max_boost - 1.0) * strength
    boost = max(1.0, min(max_boost, boost))
    audit["boost"] = boost
    audit["cheap_frac"] = cheap_frac
    audit["edge_frac"] = edge_frac
    return boost, audit
