"""Kalshi 15m decision cycle — settle due, ICT bias → YES/NO, paper fill."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import bot_config
import config
import kalshi_charts
import kalshi_client
import kalshi_ict
import notify
import paper
from models import KalshiSuggestion

logger = logging.getLogger(__name__)


def _near_decision_time(now: datetime | None = None) -> bool:
    """True when within KALSHI_DECISION_WINDOW_SEC of (window_open + offset)."""
    now = now or datetime.now(timezone.utc)
    offset = int(bot_config.KALSHI_CYCLE_OFFSET_SEC)
    window = int(bot_config.KALSHI_DECISION_WINDOW_SEC)
    # 15m windows at :00/:15/:30/:45
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
    """Approximate fill price in cents for YES or NO given YES mid."""
    mid = float(yes_mid_cents)
    if side.upper() == "YES":
        return max(1.0, min(99.0, mid))
    return max(1.0, min(99.0, 100.0 - mid))


def _bankroll_usd() -> float:
    """Bankroll for sizing: live Kalshi balance when enabled, else configured $77."""
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
    if contracts < 1:
        logger.info(
            "Size 0: bankroll=$%.2f budget=$%.2f price=$%.2f (need >= 1 contract)",
            bankroll,
            budget,
            price,
        )
    else:
        logger.info(
            "Size %s x%s @ %.1f¢ (bankroll=$%.2f budget=$%.2f cost~$%.2f)",
            side,
            contracts,
            entry_cents,
            bankroll,
            budget,
            contracts * price,
        )
    return contracts, entry_cents, budget


def _mid_too_extreme(side: str, mid: float) -> str | None:
    """Skip lottery-ticket mids even when ICT has a bias."""
    extreme = float(getattr(bot_config, "KALSHI_EXTREME_MID_CENTS", 5.0))
    min_edge = float(bot_config.KALSHI_MIN_EDGE_CENTS)
    if mid < extreme or mid > (100.0 - extreme):
        return f"mid {mid:.1f}¢ too extreme (<{extreme} or >{100 - extreme})"
    if side == "YES" and mid > (100.0 - min_edge):
        return f"YES mid {mid:.1f}¢ too rich (need ≤{100 - min_edge:.0f})"
    if side == "NO" and mid < min_edge:
        return f"NO via mid {mid:.1f}¢ too rich (YES mid need ≥{min_edge:.0f})"
    return None


def _decide_for_market(series: str, market: dict[str, Any]) -> KalshiSuggestion:
    product_id = bot_config.series_product(series)
    coinbase = bot_config.PRODUCT_TO_COINBASE.get(product_id, f"{product_id}-USD")
    ticker = str(market.get("ticker") or "")
    expiry = (
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    mid = kalshi_client.mid_cents_from_market(market)
    if mid is None:
        mid = kalshi_client.get_orderbook_mid(ticker)
    if mid is None:
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale="no mid available",
            expiry_ts=str(expiry) if expiry else None,
        )

    if paper.has_open_for_market(ticker):
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale="already have open paper position",
            mid_cents=mid,
            expiry_ts=str(expiry) if expiry else None,
        )

    try:
        ict, snapshot = kalshi_ict.propose_ict_bias(coinbase)
    except Exception as exc:
        logger.exception("ICT bias failed for %s", coinbase)
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale=f"ICT analysis failed: {exc}",
            mid_cents=mid,
            expiry_ts=str(expiry) if expiry else None,
        )

    bias = kalshi_ict.ict_bias_label(ict.action)
    side = kalshi_ict.ict_action_to_side(ict.action)
    rationale = (ict.rationale or "").strip() or "no ICT rationale"
    ctx = snapshot.get("market_context")
    spot = getattr(ctx, "spot", None)
    if spot is not None:
        rationale = f"ICT {bias} @ spot {float(spot):,.2f}. {rationale}"

    if side is None:
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale=f"skipped (ICT no_trade): {rationale}",
            mid_cents=mid,
            expiry_ts=str(expiry) if expiry else None,
            ict_action=ict.action,
            ict_bias=bias,
        )

    extreme_reason = _mid_too_extreme(side, float(mid))
    if extreme_reason:
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale=f"skipped: {extreme_reason}. ICT was {bias}. {rationale}",
            mid_cents=mid,
            expiry_ts=str(expiry) if expiry else None,
            ict_action=ict.action,
            ict_bias=bias,
        )

    contracts, entry_cents, budget = size_contracts(side, mid)
    if contracts < 1:
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale=(
                f"skipped: bankroll too small for 1 contract at {entry_cents:.1f}¢ "
                f"(budget ${budget:.2f}). {rationale}"
            ),
            mid_cents=mid,
            expiry_ts=str(expiry) if expiry else None,
            ict_action=ict.action,
            ict_bias=bias,
        )

    min_edge = float(bot_config.KALSHI_MIN_EDGE_CENTS)
    if side == "YES":
        fair = float(mid) + min_edge
        edge = min_edge
    else:
        fair = float(mid) - min_edge
        edge = min_edge

    return KalshiSuggestion(
        series=series,
        market_ticker=ticker,
        side=side,
        contracts=contracts,
        entry_cents=float(entry_cents),
        expiry_ts=str(expiry) if expiry else None,
        rationale=rationale,
        product_id=product_id,
        fair_yes_cents=fair,
        mid_cents=mid,
        edge_cents=float(edge),
        ict_action=ict.action,
        ict_bias=bias,
    )


def _strike_from_market(market: dict[str, Any]) -> float | None:
    raw = market.get("floor_strike")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _notify_decision(
    suggestion: KalshiSuggestion,
    *,
    market: dict[str, Any] | None = None,
    opened: bool = False,
) -> None:
    if (
        bot_config.BROADCAST_ONLY_TRADES
        and not suggestion.is_trade()
        and not opened
    ):
        logger.info("Skip broadcast (BROADCAST_ONLY_TRADES): %s", suggestion.rationale[:120])
        return
    strike = _strike_from_market(market or {})
    chart_path = None
    try:
        chart_path = kalshi_charts.build_decision_chart(suggestion, strike=strike)
    except Exception:
        logger.exception("Chart build failed for %s", suggestion.product_id)
    try:
        notify.broadcast_decision(
            suggestion, chart_path=chart_path, opened=opened
        )
    except Exception:
        logger.exception("Decision notify failed for %s", suggestion.market_ticker)


def run_decision_cycle() -> list[KalshiSuggestion]:
    """For each configured series, ICT-decide and notify (trade or skip)."""
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
            _notify_decision(skip)
            continue
        market = markets[0]
        suggestion = _decide_for_market(series, market)
        results.append(suggestion)
        if suggestion.is_trade():
            kalshi_client.place_order(
                suggestion.market_ticker,
                suggestion.side,
                suggestion.contracts,
                yes_price_cents=int(round(suggestion.entry_cents or 0)),
            )
            opened = paper.open_trade(suggestion)
            if opened:
                _notify_decision(suggestion, market=market, opened=True)
                logger.info(
                    "Paper opened %s %s x%s @ %.1f¢ (ICT %s)",
                    suggestion.product_id,
                    suggestion.side,
                    suggestion.contracts,
                    suggestion.entry_cents or 0,
                    suggestion.ict_bias,
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
                    expiry_ts=suggestion.expiry_ts,
                    ict_action=suggestion.ict_action,
                    ict_bias=suggestion.ict_bias,
                )
                results[-1] = suggestion
                _notify_decision(suggestion, market=market, opened=False)
        else:
            logger.info("%s %s", series, suggestion.rationale)
            _notify_decision(suggestion, market=market, opened=False)
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
