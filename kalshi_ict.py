"""ICT H4/H1/M5 bias for Kalshi 15m — soft OB gate + critic refine loop."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import analyze
import bot_config
import charts
import critic
import kalshi_critic
import research
import validate
from models import Suggestion
from patterns.htf_structure import detect_htf_zones
from patterns.key_levels import compute_key_levels
from patterns.market_context import MarketContext, build_market_context
from patterns.order_block import entry_valid_at_price, fib_zone_bounds

logger = logging.getLogger(__name__)

KALSHI_GUIDE_PREFIX = """
# RUNTIME MODE: Kalshi 15-minute binary (read this first)

You are producing an ICT + Custom KalshiRules direction for a Kalshi 15-minute BTC/ETH binary.
- spot_buy / deriv_buy → long → YES
- spot_sell / deriv_sell → short → NO
- no_trade → skip this window

Apply the Trading Guide **Kalshi 15-minute binary mode** and **Custom KalshiRules** in full:
sessions (US/Asia/weekend), never chase, M5 ≥0.25% likely retraces next 5m, NEVER BUY >55¢,
prefer ≤50¢ with ~3¢ favorable limit, block last 3 minutes (except lottery/strong-signal),
index ≠ Coinbase chart / freeze near expiry.

Require M5 OB fib (0.25–0.50) or fresh matching M5 SFP reclaim in the narrative.
Ignore multi-day hold planning and spot sizing.
Do NOT invent mid-richness ¢ edges — the engine prices fair value separately.
Do NOT recommend a trade whose rationale is a coin flip or a sub-0.05% required move.

**Rationale MUST name KalshiRules** (session + entry rule + block/execute + ICT) or return no_trade.
"""

_KALSHI_PREAMBLE = (
    "Kalshi 15m binary decision for {product_id}. "
    "Apply ICT H4/H1/M5 + Custom KalshiRules (sessions, never >55¢, ≤50¢ preferred with "
    "~3¢ favorable limit, never chase, last-3m block, M5≥0.25% retrace). "
    "Return JSON with spot_buy/spot_sell/no_trade — engine maps long→YES, short→NO. "
    "Cite only structures in programmatic context. "
    "Rationale must explicitly reference KalshiRules that justify the action "
    "(or return no_trade)."
)


def _recent_m5_sfp_matches(ctx: MarketContext, direction: str) -> bool:
    """True if a live M5 SFP matches the trade direction."""
    want = "bullish" if direction == "bullish" else "bearish"
    for event in ctx.m5_sfps or []:
        if event.direction != want:
            continue
        if event in (ctx.live_invalidated_sfps or []):
            continue
        return True
    tags = " ".join(ctx.setup_tags or []).lower()
    if want == "bullish" and "bullish" in tags and "sfp" in tags:
        return True
    if want == "bearish" and "bearish" in tags and "sfp" in tags:
        return True
    return False


def evaluate_gate_outcome(
    suggestion: Suggestion,
    market_context: MarketContext | None,
) -> str:
    """Return pass_fib | pass_sfp | fail | skipped_llm_no_trade (soft — no raise)."""
    action = suggestion.action
    if action == "no_trade":
        return "skipped_llm_no_trade"
    if action not in validate.TRADE_ACTIONS:
        return "fail"
    if suggestion.order_block is None:
        return "fail"
    ob = suggestion.order_block
    for key in ("low", "high", "start_ts", "end_ts"):
        if key not in ob:
            return "fail"

    direction = analyze._trade_direction(action)
    low = float(ob["low"])
    high = float(ob["high"])
    spot = float(market_context.spot) if market_context is not None else None
    if spot is None and suggestion.entry is not None:
        spot = float(suggestion.entry)
    if spot is None:
        return "fail"

    in_fib = entry_valid_at_price(spot, direction, low, high)
    if in_fib:
        return "pass_fib"
    sfp_ok = False
    if market_context is not None:
        sfp_ok = _recent_m5_sfp_matches(market_context, direction) and (
            low <= spot <= high
        )
    if sfp_ok:
        return "pass_sfp"
    return "fail"


def validate_kalshi_ict_suggestion(
    suggestion: Suggestion,
    market_context: MarketContext | None,
    *,
    soft: bool = True,
) -> Suggestion:
    """OB/fib (or SFP reclaim) check. Soft mode logs via evaluate_gate_outcome — keeps action."""
    action = suggestion.action
    if action == "no_trade":
        return suggestion
    if action not in validate.TRADE_ACTIONS:
        if soft:
            return Suggestion.no_trade(
                f"Invalid action for Kalshi ICT: {action}",
                product_id=suggestion.product_id,
            )
        raise ValueError(f"Invalid action for Kalshi ICT: {action}")

    if suggestion.order_block is None:
        if soft:
            return suggestion
        raise ValueError("order_block required for Kalshi ICT trade")
    ob = suggestion.order_block
    for key in ("low", "high", "start_ts", "end_ts"):
        if key not in ob:
            if soft:
                return suggestion
            raise ValueError(f"order_block missing {key}")

    try:
        analyze._validate_order_block_entry(suggestion, market_context)
    except (ValueError, KeyError, TypeError):
        if not soft:
            raise
        # Soft: keep suggestion for logging; gate_outcome will be fail.
        pass

    direction = analyze._trade_direction(action)
    low = float(ob["low"])
    high = float(ob["high"])
    spot = float(market_context.spot) if market_context is not None else None
    if spot is None and suggestion.entry is not None:
        spot = float(suggestion.entry)
    if spot is None:
        if soft:
            return suggestion
        raise ValueError("spot unavailable for Kalshi fib gate")

    in_fib = entry_valid_at_price(spot, direction, low, high)
    sfp_ok = False
    if market_context is not None and not in_fib:
        sfp_ok = _recent_m5_sfp_matches(market_context, direction) and (
            low <= spot <= high
        )

    if not in_fib and not sfp_ok:
        z_low, z_high = fib_zone_bounds(direction, low, high)
        msg = (
            f"live spot {spot:,.2f} outside M5 fib band {z_low:,.2f}-{z_high:,.2f} "
            "and no matching M5 SFP reclaim"
        )
        if soft:
            logger.info("Kalshi ICT soft gate (logged only): %s", msg)
            if suggestion.entry is None:
                suggestion.entry = round(spot, 2)
            return suggestion
        raise ValueError(msg + " — no_trade for this 15m window")

    suggestion.entry = round(spot, 2)
    if not suggestion.decision_charts:
        suggestion.decision_charts = ["H4", "H1", "M5"]
    suggestion.entry_chart = suggestion.entry_chart or "M5"
    suggestion.structure_chart = suggestion.structure_chart or "H4"
    return suggestion


def build_ict_snapshot(product_id: str, cycle_id: str | None = None) -> dict[str, Any]:
    """Fetch OHLC, market context, and marked H4/H1/M5 charts for one product."""
    cycle_id = cycle_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data = research.get_all_timeframes(product_id=product_id)
    daily_bars = research.get_daily_bars_for_levels(product_id=product_id)
    key_levels = compute_key_levels(daily_bars)
    htf_zones = detect_htf_zones(data["H4"], product_id=product_id)
    market_context = build_market_context(
        data["H4"],
        data["H1"],
        data["M5"],
        daily_bars=daily_bars,
        product_id=product_id,
    )
    marked_paths = charts.render_marked_charts(
        data,
        key_levels,
        htf_zones,
        cycle_id=cycle_id,
        market_context=market_context,
        product_id=product_id,
    )
    return {
        "cycle_id": cycle_id,
        "product_id": product_id,
        "data": data,
        "market_context": market_context,
        "marked_paths": marked_paths,
        "key_levels": key_levels,
        "htf_zones": htf_zones,
    }


def propose_ict_bias(
    product_id: str,
    *,
    cycle_id: str | None = None,
    model_fair_yes_cents: float | None = None,
    yes_mid_cents: float | None = None,
) -> tuple[Suggestion, dict[str, Any], critic.RefineResult | None]:
    """Run ICT vision + critic refine; return suggestion, snapshot, refine result."""
    snapshot = build_ict_snapshot(product_id, cycle_id=cycle_id)
    guide = KALSHI_GUIDE_PREFIX + "\n\n" + analyze.load_trading_guide()
    preamble = _KALSHI_PREAMBLE.format(product_id=product_id)
    ctx: MarketContext = snapshot["market_context"]

    def _validate(suggestion: Suggestion, mctx: MarketContext | None) -> Suggestion:
        return validate_kalshi_ict_suggestion(suggestion, mctx, soft=True)

    def _extra(llm_body: str, suggestion: Suggestion):
        return kalshi_critic.check_kalshi_rationale(
            llm_body,
            suggestion,
            model_fair_yes_cents=model_fair_yes_cents,
            yes_mid_cents=yes_mid_cents,
        )

    suggestion = analyze.propose_trade(
        snapshot["marked_paths"],
        trading_guide=guide,
        market_context=ctx,
        product_id=product_id,
        validate_fn=_validate,
        user_preamble=preamble,
    )

    run_llm = bool(getattr(bot_config, "KALSHI_RUN_LLM_CRITIC", True))
    refine = critic.refine_suggestion(
        suggestion,
        ctx,
        snapshot["marked_paths"],
        guide,
        run_llm_critic=run_llm,
        validate_fn=_validate,
        user_preamble=preamble,
        extra_findings_fn=_extra,
    )
    suggestion = refine.suggestion
    suggestion.rationale = refine.llm_body or suggestion.rationale
    snapshot["gate_outcome"] = evaluate_gate_outcome(suggestion, ctx)
    snapshot["refine"] = refine
    return suggestion, snapshot, refine


def ict_action_to_side(action: str) -> str | None:
    """Map ICT action to Kalshi YES/NO, or None for skip."""
    if action in validate.LONG_ACTIONS:
        return "YES"
    if action in validate.SHORT_ACTIONS:
        return "NO"
    return None


def ict_bias_label(action: str) -> str:
    if action in validate.LONG_ACTIONS:
        return "long"
    if action in validate.SHORT_ACTIONS:
        return "short"
    return "none"
