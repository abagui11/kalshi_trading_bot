"""Kalshi LTF watchdog — deterministic M5 entries between 15m vision cycles.

No LLM on the hot path. Maps spot ICT triggers to YES/NO via direction + HTF bias,
then shared edge/KalshiRules finalize. Shadow mode (WATCHDOG_EXECUTE_ENABLED=false)
logs + alerts without filling.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import bot_config
import charts
import critic
import kalshi_client
import kalshi_fair
import kalshi_finalize
import kalshi_triggers
import paper
import research
import watchdog as spot_watchdog
from models import KalshiSuggestion
from patterns import market_structure_state as mss
from patterns.key_levels import compute_key_levels
from patterns.htf_structure import detect_htf_zones

logger = logging.getLogger(__name__)


def _cycle_id() -> str:
    return datetime.now(timezone.utc).strftime("KWD%Y%m%dT%H%M%SZ")


def _series_for_coinbase(coinbase: str) -> str | None:
    product = bot_config.product_label(coinbase)
    for series, label in bot_config.SERIES_TO_PRODUCT.items():
        if label == product:
            return series
    return None


def _template_rationale(trigger: spot_watchdog.WatchdogTrigger, htf_bias: str, side: str) -> str:
    align = mss.alignment_tag(side, htf_bias)
    para = mss.htf_paragraph(htf_bias, side, align)
    body = (
        f"Watchdog {trigger.name}: {trigger.reason}. "
        f"Mapped {trigger.direction} → {side}. "
        "KalshiRules: LTF entry between M15 marks; no vision LLM on this path."
    )
    return f"{para} {body}"


def _render_marked(
    coinbase: str,
    cycle_id: str,
    ctx: Any,
    data: dict[str, list[dict]],
    daily_bars: list[dict],
) -> dict[str, str]:
    key_levels = compute_key_levels(daily_bars)
    htf_zones = detect_htf_zones(data["H4"], product_id=coinbase)
    return charts.render_marked_charts(
        data,
        key_levels,
        htf_zones,
        cycle_id=cycle_id,
        market_context=ctx,
        product_id=coinbase,
    )


def _build_base_for_market(
    series: str,
    market: dict[str, Any],
    coinbase: str,
    cycle_id: str,
) -> tuple[dict[str, Any], float | None, float | None, float | None, str | None]:
    product_id = bot_config.series_product(series)
    ticker = str(market.get("ticker") or "")
    expiry = (
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    expiry_s = str(expiry) if expiry else None
    mid = kalshi_client.mid_cents_from_market(market)
    if mid is None:
        mid = kalshi_client.get_orderbook_mid(ticker)

    strike = market.get("floor_strike")
    try:
        strike_f = float(strike) if strike is not None else None
    except (TypeError, ValueError):
        strike_f = None
    try:
        spot = float(research.get_live_spot_price(product_id=coinbase))
    except Exception:
        spot = None

    m5 = research.get_ohlc("M5", limit=20, product_id=coinbase)
    sigma = kalshi_fair.m5_log_return_sigma(m5, lookback=12)
    tau = kalshi_fair.tau_seconds(expiry_s)
    fair_res = None
    fair_cents = None
    edge = None
    if spot is not None and strike_f is not None and strike_f > 0 and mid is not None:
        try:
            fair_res = kalshi_fair.fair_yes_cents(spot, strike_f, tau, sigma)
            fair_cents = fair_res.fair_yes_cents
            edge = fair_res.edge_cents(float(mid))
        except ValueError:
            logger.exception("Watchdog fair value failed")

    base = {
        "series": series,
        "market_ticker": ticker,
        "product_id": product_id,
        "mid_cents": mid,
        "fair_yes_cents": fair_cents,
        "edge_cents": edge,
        "expiry_ts": expiry_s,
        "spot": spot,
        "strike": strike_f,
        "spot_vs_strike_pct": (
            fair_res.spot_vs_strike_pct if fair_res else None
        ),
        "tau_sec": tau,
        "sigma": sigma,
        "prior_5m_ret": kalshi_fair.prior_return_pct(m5, 1),
        "prior_15m_ret": kalshi_fair.prior_return_pct(m5, 3),
        "prior_1h_ret": kalshi_fair.prior_return_pct(m5, 12),
        "cycle_id": cycle_id,
    }
    return base, mid, fair_cents, edge, expiry_s


def run_kalshi_watchdog() -> list[KalshiSuggestion]:
    """One LTF scan across configured series; at most one fire per series."""
    if not bot_config.WATCHDOG_ENABLED:
        return []

    from kalshi_cycle import apply_and_log

    execute = bot_config.watchdog_execute_enabled()
    results: list[KalshiSuggestion] = []

    for series in bot_config.KALSHI_SERIES:
        product_id = bot_config.series_product(series)
        coinbase = bot_config.PRODUCT_TO_COINBASE.get(product_id, f"{product_id}-USD")
        cycle_id = _cycle_id()

        try:
            markets = kalshi_client.get_open_markets(series)
        except Exception:
            logger.exception("Kalshi watchdog: list markets failed for %s", series)
            continue
        if not markets:
            continue
        market = markets[0]
        ticker = str(market.get("ticker") or "")
        if paper.has_open_for_market(ticker, bot_id="control"):
            continue

        try:
            ctx, data, _live, daily_bars = spot_watchdog._prepare_context(coinbase)
        except Exception:
            logger.exception("Kalshi watchdog: context failed for %s", coinbase)
            continue

        htf_bias = kalshi_triggers.htf_bias_from_context(ctx)
        triggers = spot_watchdog.evaluate_triggers(ctx, data["M5"], positions=[])
        if not triggers:
            continue

        base, mid, fair_cents, edge, expiry_s = _build_base_for_market(
            series, market, coinbase, cycle_id
        )
        if mid is None:
            sug = kalshi_finalize.make_skip(
                rationale="watchdog: no mid available",
                base=base,
                htf_bias=htf_bias,
                setup_tags=list(ctx.setup_tags or []),
                skip_codes=["no_mid"],
                trigger_type="watchdog",
            )
            results.append(apply_and_log(sug, market=market))
            continue

        # Soft-refresh structure (charts optional — render on fire)
        mss.refresh_from_context(
            ctx,
            product_id=coinbase,
            market_ticker=ticker,
            htf_bias=htf_bias,
        )

        for trigger in triggers:
            if spot_watchdog._is_on_cooldown(coinbase, trigger):
                sug = kalshi_finalize.make_skip(
                    rationale=(
                        f"watchdog cooldown: {trigger.name} for {coinbase}. "
                        f"{trigger.reason}"
                    ),
                    base=base,
                    htf_bias=htf_bias,
                    setup_tags=list(ctx.setup_tags or []) + ["watchdog_cooldown"],
                    skip_codes=["watchdog_cooldown"],
                    trigger_type="watchdog",
                    trigger_name=trigger.name,
                )
                results.append(apply_and_log(sug, market=market))
                spot_watchdog._record_fire(coinbase, trigger, cycle_id)
                break

            skip_tags: list[str] = []
            shadow = not execute
            if trigger.direction == "bearish" and not bot_config.WATCHDOG_ALLOW_SHORTS:
                skip_tags.append("watchdog_shorts_disabled")
                shadow = True

            # Macro soft gate (never silent)
            try:
                from macro.context import active_posture

                posture = active_posture()
                if posture and getattr(posture, "blocks_direction", None):
                    blocked = posture.blocks_direction(trigger.direction)
                    if blocked:
                        skip_tags.append(f"macro_gate_{trigger.direction}")
                        sug = kalshi_finalize.make_skip(
                            rationale=(
                                f"watchdog macro gate blocked {trigger.direction}: "
                                f"{trigger.reason}"
                            ),
                            base=base,
                            htf_bias=htf_bias,
                            setup_tags=list(ctx.setup_tags or []) + skip_tags,
                            skip_codes=skip_tags,
                            trigger_type="watchdog",
                            trigger_name=trigger.name,
                        )
                        results.append(apply_and_log(sug, market=market))
                        spot_watchdog._record_fire(coinbase, trigger, cycle_id)
                        break
            except Exception:
                pass

            if bot_config.RELATIVE_STRENGTH_ENABLED:
                try:
                    from patterns import relative_strength

                    rs_bias = relative_strength.build_relative_strength_context().bias
                    side_rs = "long" if trigger.direction == "bullish" else "short"
                    if not relative_strength.soft_gate_allows(
                        rs_bias, coinbase, side_rs
                    ):
                        skip_tags.append("relative_strength_gate")
                        sug = kalshi_finalize.make_skip(
                            rationale=(
                                f"watchdog relative_strength_gate ({rs_bias}): "
                                f"{trigger.reason}"
                            ),
                            base=base,
                            htf_bias=htf_bias,
                            setup_tags=list(ctx.setup_tags or []) + skip_tags,
                            skip_codes=skip_tags,
                            trigger_type="watchdog",
                            trigger_name=trigger.name,
                        )
                        results.append(apply_and_log(sug, market=market))
                        spot_watchdog._record_fire(coinbase, trigger, cycle_id)
                        break
                except Exception:
                    logger.exception("RS gate failed")

            side = kalshi_triggers.direction_to_side(trigger.direction)
            if side is None:
                continue

            marked: dict[str, str] = {}
            try:
                marked = _render_marked(coinbase, cycle_id, ctx, data, daily_bars)
            except Exception:
                logger.exception("Watchdog chart render failed")

            mss.refresh_from_context(
                ctx,
                product_id=coinbase,
                market_ticker=ticker,
                marked_paths=marked,
                htf_bias=htf_bias,
            )

            thesis = _template_rationale(trigger, htf_bias, side)
            context_block = critic.build_market_context_block(list(ctx.alerts or []))
            if not context_block and ctx.summary_text:
                context_block = f"=== Market context (programmatic) ===\n{ctx.summary_text}"
            full_rationale = critic.compose_rationale(thesis, context_block)

            setup_tags = list(ctx.setup_tags or []) + skip_tags + [trigger.name]
            sug = kalshi_finalize.finalize_directional(
                side=side,
                trigger_reason=full_rationale,
                trigger_type="watchdog",
                base=base,
                mid=float(mid),
                fair_cents=fair_cents,
                edge=edge,
                expiry_s=expiry_s,
                htf_bias=htf_bias,
                ict_action=None,
                ict_bias=htf_bias,
                ict_rationale=full_rationale,
                gate_outcome="pass_fib" if "fib" in trigger.name else "pass_sfp",
                setup_tags=setup_tags,
                audit={
                    "ob_low": float(trigger.ob.low),
                    "ob_high": float(trigger.ob.high),
                    "gate_outcome": "watchdog",
                },
                structure_chart_path=marked.get("H4"),
                entry_chart_path=marked.get("M5"),
                trigger_name=trigger.name,
                shadow_only=shadow,
                skip_codes=skip_tags,
            )
            # Ensure template rationale survives finalize prepend
            if sug.rationale and full_rationale not in sug.rationale:
                sug.rationale = f"{sug.rationale} {full_rationale}"
            sug.bot_id = "control"

            results.append(apply_and_log(sug, market=market))
            spot_watchdog._record_fire(coinbase, trigger, cycle_id)
            break  # one fire per series per scan

    return results
