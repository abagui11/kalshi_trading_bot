"""Kalshi 15m decision cycle — fair value, short-horizon trigger, ICT critic, paper fill."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import bot_config
import config
import kalshi_charts
import kalshi_client
import kalshi_critic
import kalshi_fair
import kalshi_ict
import kalshi_triggers
import notify
import paper
import research
from models import KalshiSuggestion

logger = logging.getLogger(__name__)


def _near_decision_time(now: datetime | None = None) -> bool:
    """True when within KALSHI_DECISION_WINDOW_SEC of (window_open + offset)."""
    now = now or datetime.now(timezone.utc)
    offset = int(bot_config.KALSHI_CYCLE_OFFSET_SEC)
    window = int(bot_config.KALSHI_DECISION_WINDOW_SEC)
    minute = now.minute
    window_start_min = (minute // 15) * 15
    open_dt = now.replace(minute=window_start_min, second=0, microsecond=0)
    target = open_dt.timestamp() + offset
    return abs(now.timestamp() - target) <= window


def settle_due() -> list[dict[str, Any]]:
    """Settle open paper positions whose Kalshi market has a yes/no result."""
    paper.init_db()
    settled: list[dict[str, Any]] = []
    for pos in paper.get_open_positions():
        ticker = str(pos["market_ticker"])
        try:
            result = kalshi_client.get_market_result(ticker)
        except Exception:
            logger.exception("Failed to fetch result for %s", ticker)
            continue
        if not result:
            continue
        closed = paper.settle_position(ticker, result)
        if closed:
            logger.info(
                "Settled %s result=%s pnl=%.2f",
                ticker,
                result,
                float(closed.get("pnl_usd") or 0),
            )
            try:
                notify.broadcast_settle(closed)
            except Exception:
                logger.exception("Settle notify failed for %s", ticker)
            settled.append(closed)
    return settled


def _contract_price_cents(side: str, yes_mid_cents: float) -> float:
    """Side fill in cents given YES mid (YES→mid, NO→100−mid)."""
    mid = float(yes_mid_cents)
    if side.upper() == "YES":
        return max(1.0, min(99.0, mid))
    return max(1.0, min(99.0, 100.0 - mid))


def _bankroll_usd() -> float:
    """Bankroll for sizing: live Kalshi balance when enabled, else configured."""
    configured = float(bot_config.KALSHI_BANKROLL_USD)
    if config.KALSHI_PAPER_ONLY or not bot_config.KALSHI_USE_LIVE_BALANCE:
        return configured
    try:
        bal = kalshi_client.get_balance()
        if bal.get("balance_dollars") is not None:
            return max(0.0, float(bal["balance_dollars"]))
        if bal.get("balance") is not None:
            raw = float(bal["balance"])
            return raw / 100.0 if raw > 1000 else raw
        if bal.get("portfolio_value") is not None:
            raw = float(bal["portfolio_value"])
            return raw / 100.0 if raw > 1000 else raw
    except Exception:
        logger.exception(
            "Live balance fetch failed — using KALSHI_BANKROLL_USD=%.2f", configured
        )
    return configured


def size_contracts(side: str, yes_mid_cents: float) -> tuple[int, float, float]:
    """Return (contracts, entry_cents, budget_usd) scaled to bankroll."""
    entry_cents = _contract_price_cents(side, yes_mid_cents)
    price = entry_cents / 100.0
    bankroll = _bankroll_usd()
    budget = max(0.0, bankroll * float(bot_config.KALSHI_DEPLOY_PCT))
    if price <= 0:
        return 0, entry_cents, budget
    raw = int(budget // price)
    cap = max(1, int(bot_config.KALSHI_MAX_CONTRACTS))
    contracts = max(0, min(cap, raw))
    if contracts < 1 and budget >= price:
        contracts = 1
    return contracts, entry_cents, budget


def _mid_too_extreme(side: str, mid: float) -> str | None:
    """Skip lottery-ticket mids."""
    extreme = float(getattr(bot_config, "KALSHI_EXTREME_MID_CENTS", 5.0))
    if mid < extreme or mid > (100.0 - extreme):
        return f"mid {mid:.1f}¢ too extreme (<{extreme} or >{100 - extreme})"
    return None


def _strike_from_market(market: dict[str, Any]) -> float | None:
    raw = market.get("floor_strike")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _base_kwargs(
    *,
    series: str,
    ticker: str,
    product_id: str,
    mid: float | None,
    fair: float | None,
    edge: float | None,
    expiry: str | None,
    features: dict[str, Any],
) -> dict[str, Any]:
    return {
        "series": series,
        "market_ticker": ticker,
        "product_id": product_id,
        "mid_cents": mid,
        "fair_yes_cents": fair,
        "edge_cents": edge,
        "expiry_ts": expiry,
        "spot": features.get("spot"),
        "strike": features.get("strike"),
        "spot_vs_strike_pct": features.get("spot_vs_strike_pct"),
        "tau_sec": features.get("tau_sec"),
        "sigma": features.get("sigma"),
        "prior_5m_ret": features.get("prior_5m_ret"),
        "prior_15m_ret": features.get("prior_15m_ret"),
        "prior_1h_ret": features.get("prior_1h_ret"),
        "cycle_id": features.get("cycle_id"),
    }


def _build_features(
    coinbase: str,
    market: dict[str, Any],
    expiry: str | None,
    cycle_id: str,
) -> dict[str, Any]:
    strike = _strike_from_market(market)
    try:
        spot = float(research.get_live_spot_price(product_id=coinbase))
    except Exception:
        logger.exception("Live spot failed for %s — trying M5 close", coinbase)
        bars = research.get_ohlc("M5", limit=13, product_id=coinbase)
        spot = float(bars[-1]["close"]) if bars else None

    m5 = research.get_ohlc("M5", limit=20, product_id=coinbase)
    sigma = kalshi_fair.m5_log_return_sigma(m5, lookback=12)
    tau = kalshi_fair.tau_seconds(expiry)
    prior_5m = kalshi_fair.prior_return_pct(m5, 1)
    prior_15m = kalshi_fair.prior_return_pct(m5, 3)
    prior_1h = kalshi_fair.prior_return_pct(m5, 12)

    fair_res = None
    if spot is not None and strike is not None and strike > 0:
        try:
            fair_res = kalshi_fair.fair_yes_cents(spot, strike, tau, sigma)
        except ValueError:
            logger.exception("Fair value failed spot=%s strike=%s", spot, strike)

    return {
        "cycle_id": cycle_id,
        "spot": spot,
        "strike": strike,
        "sigma": sigma,
        "tau_sec": tau,
        "prior_5m_ret": prior_5m,
        "prior_15m_ret": prior_15m,
        "prior_1h_ret": prior_1h,
        "m5_bars": m5,
        "fair": fair_res,
        "spot_vs_strike_pct": (
            fair_res.spot_vs_strike_pct
            if fair_res
            else (
                ((spot / strike) - 1.0) * 100.0
                if spot and strike and strike > 0
                else None
            )
        ),
    }


def _decide_for_market(series: str, market: dict[str, Any]) -> KalshiSuggestion:
    product_id = bot_config.series_product(series)
    coinbase = bot_config.PRODUCT_TO_COINBASE.get(product_id, f"{product_id}-USD")
    ticker = str(market.get("ticker") or "")
    expiry = (
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    expiry_s = str(expiry) if expiry else None
    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    min_edge = float(bot_config.KALSHI_MIN_EDGE_CENTS)

    mid = kalshi_client.mid_cents_from_market(market)
    if mid is None:
        mid = kalshi_client.get_orderbook_mid(ticker)

    features = _build_features(coinbase, market, expiry_s, cycle_id)
    fair_res = features.get("fair")
    fair_cents = fair_res.fair_yes_cents if fair_res else None
    edge = (
        fair_res.edge_cents(float(mid))
        if fair_res is not None and mid is not None
        else None
    )

    base = _base_kwargs(
        series=series,
        ticker=ticker,
        product_id=product_id,
        mid=mid,
        fair=fair_cents,
        edge=edge,
        expiry=expiry_s,
        features=features,
    )

    if mid is None:
        return KalshiSuggestion.skip(
            rationale="no mid available",
            **base,
        )

    if paper.has_open_for_market(ticker):
        return KalshiSuggestion.skip(
            rationale="already have open paper position",
            **base,
        )

    # --- ICT + critic (logged; direction not sole driver) ---
    ict = None
    snapshot: dict[str, Any] = {}
    refine = None
    try:
        ict, snapshot, refine = kalshi_ict.propose_ict_bias(
            coinbase,
            cycle_id=cycle_id,
            model_fair_yes_cents=fair_cents,
            yes_mid_cents=float(mid),
        )
    except Exception as exc:
        logger.exception("ICT bias failed for %s", coinbase)
        return KalshiSuggestion.skip(
            rationale=f"ICT analysis failed: {exc}",
            **base,
        )

    bias = kalshi_ict.ict_bias_label(ict.action)
    gate_outcome = snapshot.get("gate_outcome") or kalshi_ict.evaluate_gate_outcome(
        ict, snapshot.get("market_context")
    )
    ict_rationale = (ict.rationale or "").strip() or "no ICT rationale"
    critic_downgraded = bool(refine.downgraded) if refine else False
    critic_passes = int(refine.passes_used) if refine else 0
    critic_findings = (
        kalshi_critic.findings_to_json(refine.final_findings) if refine else []
    )

    ob = ict.order_block or {}
    ob_low = float(ob["low"]) if ob.get("low") is not None else None
    ob_high = float(ob["high"]) if ob.get("high") is not None else None
    ctx = snapshot.get("market_context")
    h1_bias = kalshi_triggers.htf_bias_from_context(ctx)

    audit = {
        "ict_action": ict.action,
        "ict_bias": bias,
        "gate_outcome": gate_outcome,
        "ob_low": ob_low,
        "ob_high": ob_high,
        "h1_bias_tag": h1_bias,
        "critic_passes": critic_passes,
        "critic_findings": critic_findings,
        "critic_downgraded": critic_downgraded,
    }

    if critic_downgraded:
        sug = KalshiSuggestion.skip(
            rationale=f"skipped (critic downgrade): {ict_rationale}",
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "none"
        sug.would_skip_reasons = ["critic_downgrade"]
        return sug

    # --- Hard fair-value gate ---
    if fair_res is None or edge is None:
        sug = KalshiSuggestion.skip(
            rationale=f"skipped: fair value unavailable. ICT: {ict_rationale}",
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "none"
        return sug

    if not kalshi_fair.has_min_edge(edge, min_edge):
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped: |fair−mid|={abs(edge):.1f}¢ < {min_edge:.0f}¢ "
                f"(fair {fair_cents:.1f}¢ mid {mid:.1f}¢). ICT: {ict_rationale}"
            ),
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "none"
        sug.would_skip_reasons = ["coin_flip_gap"]
        return sug

    # --- Mechanical short-horizon trigger + HTF veto ---
    trigger = kalshi_triggers.short_horizon_trigger(
        spot=float(features["spot"] or 0),
        strike=float(features["strike"] or 0),
        yes_mid_cents=float(mid),
        prior_5m_ret_pct=features.get("prior_5m_ret"),
        prior_15m_ret_pct=features.get("prior_15m_ret"),
        market_context=ctx,
    )

    if trigger.side is None:
        sug = KalshiSuggestion.skip(
            rationale=f"skipped (no short-horizon trigger): {trigger.reason}. ICT: {ict_rationale}",
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "none"
        sug.h1_bias_tag = trigger.htf_bias
        sug.would_skip_reasons = kalshi_triggers.shadow_skip_reasons(
            side=None,
            entry_cents=None,
            through_strike_pct=trigger.through_strike_pct,
            momentum_pct=trigger.momentum_pct,
            gate_outcome=gate_outcome,
            htf_bias=trigger.htf_bias,
            fair_yes_cents=fair_cents,
            yes_mid_cents=float(mid),
            min_edge=min_edge,
        )
        return sug

    side = trigger.side
    if kalshi_triggers.htf_vetoes(side, trigger.htf_bias):
        # Hard HTF veto per plan
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped (HTF veto {trigger.htf_bias} vs {side}): {trigger.reason}. "
                f"ICT: {ict_rationale}"
            ),
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "short_horizon"
        sug.h1_bias_tag = trigger.htf_bias
        sug.would_skip_reasons = ["htf_conflict"]
        return sug

    if not kalshi_fair.side_agrees_with_edge(side, edge):
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped: trigger {side} disagrees with fair edge {edge:+.1f}¢ "
                f"(fair {fair_cents:.1f} vs mid {mid:.1f}). ICT: {ict_rationale}"
            ),
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "short_horizon"
        sug.h1_bias_tag = trigger.htf_bias
        return sug

    extreme_reason = _mid_too_extreme(side, float(mid))
    if extreme_reason:
        sug = KalshiSuggestion.skip(
            rationale=f"skipped: {extreme_reason}. {trigger.reason}. ICT: {ict_rationale}",
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = "short_horizon"
        return sug

    minutes_left = kalshi_triggers.minutes_to_expiry(expiry_s)
    session = kalshi_triggers.session_label_et()
    mid_side = float(mid) if side == "YES" else (100.0 - float(mid))
    lottery = False
    if kalshi_triggers.in_last_minutes(expiry_s):
        # KalshiRules: block last 3m unless lottery ticket ($0.05–$0.10 + liquidity sweep).
        sfp_tags = " ".join((ctx.setup_tags if ctx else []) or []).lower()
        swept = "sfp" in sfp_tags or gate_outcome == "pass_sfp"
        if kalshi_triggers.is_lottery_ticket(mid_side) and swept:
            lottery = True
            trigger_type = "lottery_ticket"
        else:
            sug = KalshiSuggestion.skip(
                rationale=(
                    f"skipped (KalshiRules last-{kalshi_triggers.BLOCK_LAST_MINUTES:.0f}m block): "
                    f"minutes_left={minutes_left}. {trigger.reason}. ICT: {ict_rationale}"
                ),
                ict_action=ict.action,
                ict_bias=bias,
                **base,
            )
            for k, v in audit.items():
                setattr(sug, k, v)
            sug.trigger_type = "none"
            sug.h1_bias_tag = trigger.htf_bias
            sug.would_skip_reasons = ["last_3m_block"]
            return sug
    else:
        trigger_type = "short_horizon"

    # KalshiRules: intended limit ~3¢ more favorable than mid (lottery uses market).
    if lottery:
        entry_cents = mid_side
    else:
        entry_cents = kalshi_triggers.intended_limit_cents(side, float(mid))

    if entry_cents > kalshi_triggers.MAX_ENTRY_CENTS:
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped (KalshiRules NEVER BUY >{kalshi_triggers.MAX_ENTRY_CENTS:.0f}¢): "
                f"limit {entry_cents:.1f}¢. ICT: {ict_rationale}"
            ),
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        return sug

    # Size at the intended limit price (not raw mid).
    bankroll = _bankroll_usd()
    budget = max(0.0, bankroll * float(bot_config.KALSHI_DEPLOY_PCT))
    price = entry_cents / 100.0
    cap = max(1, int(bot_config.KALSHI_MAX_CONTRACTS))
    contracts = max(0, min(cap, int(budget // price))) if price > 0 else 0
    if contracts < 1 and budget >= price:
        contracts = 1
    if contracts < 1:
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped: bankroll too small for 1 contract at {entry_cents:.1f}¢ "
                f"(budget ${budget:.2f}). {trigger.reason}"
            ),
            ict_action=ict.action,
            ict_bias=bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        return sug

    shadow = kalshi_triggers.shadow_skip_reasons(
        side=side,
        entry_cents=entry_cents,
        through_strike_pct=trigger.through_strike_pct,
        momentum_pct=trigger.momentum_pct,
        gate_outcome=gate_outcome,
        htf_bias=trigger.htf_bias,
        fair_yes_cents=fair_cents,
        yes_mid_cents=float(mid),
        min_edge=min_edge,
        minutes_left=minutes_left,
    )

    rationale = kalshi_triggers.compose_kalshi_rules_rationale(
        session=session,
        trigger_reason=trigger.reason,
        side=side,
        yes_mid_cents=float(mid),
        entry_cents=float(entry_cents),
        limit_cents=float(entry_cents),
        fair_cents=float(fair_cents),
        edge_cents=float(edge),
        gate_outcome=gate_outcome,
        ict_bias=bias,
        ict_rationale=ict_rationale,
        minutes_left=minutes_left,
        lottery=lottery,
    )

    return KalshiSuggestion(
        side=side,
        contracts=contracts,
        entry_cents=float(entry_cents),
        rationale=rationale,
        ict_action=ict.action,
        ict_bias=bias,
        gate_outcome=gate_outcome,
        trigger_type=trigger_type,
        ob_low=ob_low,
        ob_high=ob_high,
        h1_bias_tag=trigger.htf_bias,
        critic_passes=critic_passes,
        critic_findings=critic_findings,
        critic_downgraded=False,
        would_skip_reasons=shadow,
        **base,
    )


def _notify_decision(
    suggestion: KalshiSuggestion,
    *,
    market: dict[str, Any] | None = None,
    opened: bool = False,
) -> str | None:
    if (
        bot_config.BROADCAST_ONLY_TRADES
        and not suggestion.is_trade()
        and not opened
    ):
        logger.info(
            "Skip broadcast (BROADCAST_ONLY_TRADES): %s",
            suggestion.rationale[:120],
        )
        return None
    strike = suggestion.strike or _strike_from_market(market or {})
    chart_path = None
    try:
        chart_path = kalshi_charts.build_decision_chart(
            suggestion,
            strike=strike,
            position_id=suggestion.position_id,
        )
    except Exception:
        logger.exception("Chart build failed for %s", suggestion.product_id)
    try:
        notify.broadcast_decision(
            suggestion, chart_path=chart_path, opened=opened
        )
    except Exception:
        logger.exception("Decision notify failed for %s", suggestion.market_ticker)
    return chart_path


def run_decision_cycle() -> list[KalshiSuggestion]:
    """For each configured series, decide and notify (trade or skip); log all."""
    results: list[KalshiSuggestion] = []
    for series in config.KALSHI_SERIES:
        try:
            markets = kalshi_client.get_open_markets(series)
        except Exception:
            logger.exception("Failed to list markets for %s", series)
            skip = KalshiSuggestion.skip(
                series=series,
                market_ticker="",
                product_id=bot_config.series_product(series),
                rationale=f"skipped: failed to list open markets for {series}",
            )
            results.append(skip)
            paper.log_decision(skip)
            _notify_decision(skip)
            continue
        if not markets:
            logger.info("%s: no open markets", series)
            skip = KalshiSuggestion.skip(
                series=series,
                market_ticker="",
                product_id=bot_config.series_product(series),
                rationale=f"skipped: no open markets for {series} right now",
            )
            results.append(skip)
            paper.log_decision(skip)
            _notify_decision(skip)
            continue

        market = markets[0]
        suggestion = _decide_for_market(series, market)
        if suggestion.is_trade():
            kalshi_client.place_order(
                suggestion.market_ticker,
                suggestion.side,
                suggestion.contracts,
                yes_price_cents=int(round(suggestion.entry_cents or 0)),
            )
            opened = paper.open_trade(suggestion)
            if opened:
                suggestion.opened = True
                suggestion.position_id = int(opened["id"])
                chart_path = _notify_decision(
                    suggestion, market=market, opened=True
                )
                if chart_path:
                    suggestion.chart_path = chart_path
                    paper.set_position_chart_path(
                        suggestion.position_id, chart_path
                    )
                paper.log_decision(suggestion)
                logger.info(
                    "Paper opened %s %s x%s @ %.1f¢ fair=%.1f edge=%+.1f gate=%s",
                    suggestion.product_id,
                    suggestion.side,
                    suggestion.contracts,
                    suggestion.entry_cents or 0,
                    suggestion.fair_yes_cents or 0,
                    suggestion.edge_cents or 0,
                    suggestion.gate_outcome,
                )
            else:
                logger.warning("Paper open failed for %s", suggestion.market_ticker)
                suggestion = KalshiSuggestion.skip(
                    series=suggestion.series,
                    market_ticker=suggestion.market_ticker,
                    product_id=suggestion.product_id,
                    rationale=(
                        f"signal was {suggestion.side} but paper open failed "
                        f"(cash or duplicate). Original why: {suggestion.rationale}"
                    ),
                    mid_cents=suggestion.mid_cents,
                    fair_yes_cents=suggestion.fair_yes_cents,
                    edge_cents=suggestion.edge_cents,
                    expiry_ts=suggestion.expiry_ts,
                    ict_action=suggestion.ict_action,
                    ict_bias=suggestion.ict_bias,
                    spot=suggestion.spot,
                    strike=suggestion.strike,
                    gate_outcome=suggestion.gate_outcome,
                    trigger_type=suggestion.trigger_type,
                    critic_downgraded=suggestion.critic_downgraded,
                    would_skip_reasons=suggestion.would_skip_reasons,
                )
                paper.log_decision(suggestion)
                _notify_decision(suggestion, market=market, opened=False)
        else:
            logger.info("%s %s", series, suggestion.rationale[:160])
            paper.log_decision(suggestion)
            _notify_decision(suggestion, market=market, opened=False)
        results.append(suggestion)
    return results


def run_once(*, force_decision: bool = False) -> dict[str, Any]:
    """One job tick: settle due; run decisions if near window offset (or forced)."""
    paper.init_db()
    settled = settle_due()
    decided: list[KalshiSuggestion] = []
    near = force_decision or _near_decision_time()
    if near:
        decided = run_decision_cycle()
    else:
        logger.info("Not near decision window — settle only (%s settled)", len(settled))
    return {
        "settled": settled,
        "decisions": [d.to_dict() for d in decided],
        "near_decision": near,
        "stats": paper.get_stats(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = run_once(force_decision=True)
    print(json.dumps(out, indent=2, default=str))
