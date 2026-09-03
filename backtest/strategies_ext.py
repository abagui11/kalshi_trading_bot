"""Stub / recorded-bias strategies for backtest Phase C."""

from __future__ import annotations

from typing import Any

from strategies.adverse import AdverseStrategy
from strategies.context import SharedCycleContext, SharedHtfBias
from strategies.control import ControlStrategy
from strategies.lottery import LotteryStrategy


def htf_from_record(rec: dict[str, Any] | None) -> SharedHtfBias | None:
    if not rec:
        return None
    side = rec.get("side")
    if side not in ("YES", "NO", None):
        side = None
    htf_bias = str(rec.get("htf_bias") or rec.get("ict_bias") or "unknown")
    if side is None:
        if htf_bias == "bear":
            side = "NO"
        elif htf_bias == "bull":
            side = "YES"
    return SharedHtfBias(
        ict_action=str(rec.get("ict_action") or ("spot_sell" if side == "NO" else "spot_buy" if side == "YES" else "no_trade")),
        ict_bias=str(rec.get("ict_bias") or htf_bias),
        ict_rationale=str(rec.get("ict_rationale") or "recorded bias"),
        gate_outcome=rec.get("gate_outcome") or "pass_fib",
        htf_bias=htf_bias,
        setup_tags=list(rec.get("setup_tags") or [f"htf_{htf_bias}"]),
        side=side if side in ("YES", "NO") else None,
    )


def rule_htf_from_returns(
    prior_1h_ret: float | None,
    *,
    threshold_pct: float = 0.15,
) -> SharedHtfBias:
    """Simple deterministic HTF stub from 1h return (no Claude)."""
    if prior_1h_ret is None:
        bias, side, action = "unknown", None, "no_trade"
    elif prior_1h_ret <= -threshold_pct:
        bias, side, action = "bear", "NO", "spot_sell"
    elif prior_1h_ret >= threshold_pct:
        bias, side, action = "bull", "YES", "spot_buy"
    else:
        bias, side, action = "mixed", None, "no_trade"
    return SharedHtfBias(
        ict_action=action,
        ict_bias=bias,
        ict_rationale=f"rule HTF from prior_1h_ret={prior_1h_ret}",
        gate_outcome="pass_fib" if side else "skipped_llm_no_trade",
        htf_bias=bias,
        setup_tags=[f"htf_{bias}", "rule_htf"],
        side=side,
    )


class RecordedControlStrategy(ControlStrategy):
    """Control path that only acts when ctx.htf is pre-injected (no Claude)."""

    bot_id = "control"
    display_name = "Control (recorded bias)"
    needs_htf_bias = True

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        # Refuse to invent bias — runner must inject recorded/rule HTF.
        if ctx.htf is None:
            return None
        return super().decide(ctx)


def resolve_strategies(bot_ids: list[str]) -> list[Any]:
    """Map bot ids to strategy instances for the runner."""
    mapping = {
        "lottery": LotteryStrategy(),
        "adverse": AdverseStrategy(),
        "control": RecordedControlStrategy(),
    }
    out = []
    for bid in bot_ids:
        strat = mapping.get(bid)
        if strat is not None:
            out.append(strat)
    return out
