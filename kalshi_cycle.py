"""Kalshi 15m decision cycle — settle due, propose YES/NO with edge gate, paper fill."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

import bot_config
import config
import kalshi_client
import notify
import paper
import research
from models import KalshiSuggestion

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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
            # Also try after close_time even if result empty (log and wait).
            continue
        closed = paper.settle_position(ticker, result)
        if closed:
            logger.info(
                "Settled %s result=%s pnl=%.2f",
                ticker,
                result,
                float(closed.get("pnl_usd") or 0),
            )
            settled.append(closed)
    return settled


def _candle_summary(product_coinbase: str, limit: int = 12) -> str:
    try:
        bars = research.get_ohlc("M5", limit=limit, product_id=product_coinbase)
    except Exception:
        logger.exception("Failed to fetch candles for %s", product_coinbase)
        return "(candles unavailable)"
    lines = []
    for b in bars[-limit:]:
        lines.append(
            f"{b['ts']} o={b['open']:.2f} h={b['high']:.2f} "
            f"l={b['low']:.2f} c={b['close']:.2f}"
        )
    if not lines:
        return "(no candles)"
    last = bars[-1]
    first = bars[-min(limit, len(bars))]
    try:
        move_pct = (float(last["close"]) - float(first["open"])) / float(first["open"]) * 100
    except Exception:
        move_pct = 0.0
    header = f"Last {len(lines)} M5 bars; window move ~{move_pct:+.3f}%"
    return header + "\n" + "\n".join(lines)


def _ask_claude(
    *,
    series: str,
    product_id: str,
    market: dict[str, Any],
    mid_cents: float,
    candle_text: str,
) -> dict[str, Any]:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    title = market.get("title") or series
    strike = market.get("floor_strike") or market.get("yes_sub_title") or ""
    prompt = f"""You are trading Kalshi 15-minute BTC/ETH up/down binary markets.

Market: {title}
Ticker: {market.get('ticker')}
Series: {series}
Asset: {product_id}
Strike / subtitle: {strike}
Rules: {str(market.get('rules_primary') or '')[:400]}
Kalshi YES mid: {mid_cents:.2f} cents (0–100).

Recent Coinbase {product_id}-USD M5 candles:
{candle_text}

Decide whether YES (price up vs window open reference) or NO is underpriced vs a simple fair value.
Return ONLY JSON:
{{"side":"YES"|"NO"|"SKIP","fair_yes_cents":0-100,"rationale":"one or two short sentences"}}

Rules:
- fair_yes_cents is your estimated probability of YES in cents (50 = coin flip).
- SKIP if edge is unclear or market is extreme (<5 or >95) without conviction.
- Be concise. No markdown.
"""
    msg = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text += block.text
    match = _JSON_RE.search(text)
    if not match:
        return {"side": "SKIP", "fair_yes_cents": mid_cents, "rationale": "parse_failed"}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"side": "SKIP", "fair_yes_cents": mid_cents, "rationale": "json_failed"}
    side = str(data.get("side") or "SKIP").upper()
    if side not in ("YES", "NO", "SKIP"):
        side = "SKIP"
    try:
        fair = float(data.get("fair_yes_cents"))
    except (TypeError, ValueError):
        fair = mid_cents
    fair = max(0.0, min(100.0, fair))
    rationale = str(data.get("rationale") or "").strip() or "no rationale"
    return {"side": side, "fair_yes_cents": fair, "rationale": rationale}


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

    candle_text = _candle_summary(coinbase)
    llm = _ask_claude(
        series=series,
        product_id=product_id,
        market=market,
        mid_cents=mid,
        candle_text=candle_text,
    )
    fair = float(llm["fair_yes_cents"])
    side = str(llm["side"]).upper()
    rationale = str(llm["rationale"])
    min_edge = float(bot_config.KALSHI_MIN_EDGE_CENTS)

    if side == "SKIP":
        return KalshiSuggestion.skip(
            series=series,
            market_ticker=ticker,
            product_id=product_id,
            rationale=f"skipped: {rationale}",
            mid_cents=mid,
            fair_yes_cents=fair,
            expiry_ts=str(expiry) if expiry else None,
        )

    if side == "YES":
        edge = fair - mid
        if edge < min_edge:
            return KalshiSuggestion.skip(
                series=series,
                market_ticker=ticker,
                product_id=product_id,
                rationale=f"skipped: no edge (YES fair {fair:.1f} vs mid {mid:.1f}, need +{min_edge})",
                mid_cents=mid,
                fair_yes_cents=fair,
                expiry_ts=str(expiry) if expiry else None,
            )
    else:
        edge = mid - fair
        if edge < min_edge:
            return KalshiSuggestion.skip(
                series=series,
                market_ticker=ticker,
                product_id=product_id,
                rationale=f"skipped: no edge (NO vs mid {mid:.1f} fair {fair:.1f}, need +{min_edge})",
                mid_cents=mid,
                fair_yes_cents=fair,
                expiry_ts=str(expiry) if expiry else None,
            )

    contracts = max(1, int(bot_config.KALSHI_MAX_CONTRACTS))
    suggestion = KalshiSuggestion(
        series=series,
        market_ticker=ticker,
        side=side,
        contracts=contracts,
        entry_cents=float(mid),
        expiry_ts=str(expiry) if expiry else None,
        rationale=rationale,
        product_id=product_id,
        fair_yes_cents=fair,
        mid_cents=mid,
        edge_cents=float(edge),
    )
    return suggestion


def run_decision_cycle() -> list[KalshiSuggestion]:
    """For each configured series, decide and optionally paper-open + notify."""
    results: list[KalshiSuggestion] = []
    for series in config.KALSHI_SERIES:
        try:
            markets = kalshi_client.get_open_markets(series)
        except Exception:
            logger.exception("Failed to list markets for %s", series)
            continue
        if not markets:
            logger.info("%s: no open markets", series)
            continue
        market = markets[0]
        suggestion = _decide_for_market(series, market)
        results.append(suggestion)
        if suggestion.is_trade():
            # Live stub is no-op in paper mode; paper fill is source of truth.
            kalshi_client.place_order(
                suggestion.market_ticker,
                suggestion.side,
                suggestion.contracts,
                yes_price_cents=int(round(suggestion.entry_cents or 0)),
            )
            opened = paper.open_trade(suggestion)
            if opened:
                notify.broadcast_kalshi_trade(suggestion)
                logger.info(
                    "Paper opened %s %s x%s @ %.1f¢",
                    suggestion.product_id,
                    suggestion.side,
                    suggestion.contracts,
                    suggestion.entry_cents or 0,
                )
            else:
                logger.warning("Paper open failed for %s", suggestion.market_ticker)
        else:
            logger.info("%s %s", series, suggestion.rationale)
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
