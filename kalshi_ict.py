"""ICT H4/H1/M5 bias for Kalshi 15m YES/NO decisions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import analyze
import bot_config
import charts
import research
import validate
from models import Suggestion
from patterns.htf_structure import detect_htf_zones
from patterns.key_levels import compute_key_levels
from patterns.market_context import MarketContext, build_market_context
from patterns.order_block import entry_valid_at_price, fib_zone_bounds

logger = logging.getLogger(__name__)

KALSHI_GUIDE_PREFIX = """
# RUNTIME MODE: Kalshi 15m binary (read this first)

You are producing an ICT direction for a Kalshi 15-minute BTC/ETH up/down market.
- spot_buy / deriv_buy → long → YES (price up vs window)
- spot_sell / deriv_sell → short → NO (price down vs window)
- no_trade → skip this window
Apply the Trading Guide Kalshi 15-minute binary section. Require M5 OB fib (0.25–0.50)
or a fresh matching M5 SFP reclaim. Ignore multi-day hold planning and ignore spot sizing.
"""


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


def validate_kalshi_ict_suggestion(
    suggestion: Suggestion,
    market_context: MarketContext | None,
) -> Suggestion:
    """OB/fib (or SFP reclaim) gate for Kalshi — skips spot R/R + sizing."""
    action = suggestion.action
    if action == "no_trade":
        return suggestion
    if action not in validate.TRADE_ACTIONS:
        raise ValueError(f"Invalid action for Kalshi ICT: {action}")

    if suggestion.order_block is None:
        raise ValueError("order_block required for Kalshi ICT trade")
    ob = suggestion.order_block
    for key in ("low", "high", "start_ts", "end_ts"):
        if key not in ob:
            raise ValueError(f"order_block missing {key}")

    # Reuse M5 OB identity + fib band check on suggested entry.
    analyze._validate_order_block_entry(suggestion, market_context)

    direction = analyze._trade_direction(action)
    low = float(ob["low"])
    high = float(ob["high"])
    spot = float(market_context.spot) if market_context is not None else None
    if spot is None and suggestion.entry is not None:
        spot = float(suggestion.entry)
    if spot is None:
        raise ValueError("spot unavailable for Kalshi fib gate")

    in_fib = entry_valid_at_price(spot, direction, low, high)
    sfp_ok = False
    if market_context is not None and not in_fib:
        # SFP reclaim exception: matching M5 SFP and spot back inside the M5 OB.
        sfp_ok = _recent_m5_sfp_matches(market_context, direction) and (
            low <= spot <= high
        )

    if not in_fib and not sfp_ok:
        z_low, z_high = fib_zone_bounds(direction, low, high)
        raise ValueError(
            f"live spot {spot:,.2f} outside M5 fib band {z_low:,.2f}-{z_high:,.2f} "
            "and no matching M5 SFP reclaim — no_trade for this 15m window"
        )

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


def propose_ict_bias(product_id: str, *, cycle_id: str | None = None) -> tuple[Suggestion, dict[str, Any]]:
    """Run ICT vision analysis for one Coinbase product; return suggestion + snapshot."""
    snapshot = build_ict_snapshot(product_id, cycle_id=cycle_id)
    guide = KALSHI_GUIDE_PREFIX + "\n\n" + analyze.load_trading_guide()

    def _validate(suggestion: Suggestion, ctx: MarketContext | None) -> Suggestion:
        try:
            return validate_kalshi_ict_suggestion(suggestion, ctx)
        except (ValueError, KeyError, TypeError) as exc:
            logger.info("Kalshi ICT gate rejected %s: %s", product_id, exc)
            return Suggestion.no_trade(f"ICT gate: {exc}", product_id=product_id)

    suggestion = analyze.propose_trade(
        snapshot["marked_paths"],
        trading_guide=guide,
        market_context=snapshot["market_context"],
        product_id=product_id,
        validate_fn=_validate,
        user_preamble=(
            f"Kalshi 15m binary decision for {product_id}. "
            "Apply ICT H4/H1/M5 + M5 OB fib / SFP rules. "
            "Return JSON with spot_buy/spot_sell/no_trade — the engine maps long→YES, short→NO."
        ),
    )
    return suggestion, snapshot


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
