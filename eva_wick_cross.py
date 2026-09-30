"""Cross-asset wick books — BTC/ETH wick fires, EVA-aligned, traded on alts.

Spec'd by the operator 2026-09-30. Six paper books, one per (signal, target)
pair: when the live eva_wick rule fires on BTC or ETH *and* the EVA vision
board agrees with the fire's direction, buy the same side of the XRP, SOL
and HYPE 15m markets settling on the same quarter-hour clock:

    eva_wick_btc_xrp / _sol / _hype   keyed to BTC wick fires
    eva_wick_eth_xrp / _sol / _hype   keyed to ETH wick fires

What each book tests, and how it differs from the existing shadow books:
the altcoin wick clones (eva_wick_xrp etc.) ask whether the alt's *own*
mid-window favourite is mispriced; these ask whether a BTC/ETH favourite
confirmation **carries across assets** — the alt side is bought at whatever
the alt book quotes, with no 67-80¢ band of its own, because the signal
lives on the majors, not in the alt's pricing.

The EVA alignment gate is the one filter, per the spec ("constrained only
when it lines up with the EVA vision board"): the board's M15 stance for the
*signal* product must point with the fire (YES needs bullish, NO needs
bearish) at ≥ EVA_CROSS_MIN_CONF confidence. Fail-closed like every stance
read in this repo — a stale or unreachable board trades nothing and logs
why. Worth stating plainly: the two stance gates shipped on the wick book
itself both failed out of sample and were removed; this gate is *pre-
registered as part of the rule under test*, not a tuned improvement to a
measured one, and these books exist to find out whether it earns anything.

Accounting matches the other paper books: entry at the real ask off the
market the exchange is quoting (no ask, or the empty-book ~100¢ placeholder,
is a logged skip, never an invented fill), sized through the same
kalshi_sizing path as the wick family so the rows are comparable, settled by
the shared settle_due() poller, no taker fee (the ledger-wide known
overstatement). Paper by construction via bot_config.PAPER_ONLY_BOTS.

Like eva_wick_hourly, not a registry strategy: the cycle's decide(ctx) shape
is one market per series, and this is a cross-series reaction to another
bot's entry. run_strategy_cycle hands the tick's suggestions to
process_fires() after the strategy loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import bot_config
import kalshi_client
import paper
from models import KalshiSuggestion

logger = logging.getLogger(__name__)

# bot_id -> (signal product short id, target series)
CROSS_BOTS: dict[str, tuple[str, str]] = {
    "eva_wick_btc_xrp": ("BTC", "KXXRP15M"),
    "eva_wick_btc_sol": ("BTC", "KXSOL15M"),
    "eva_wick_btc_hype": ("BTC", "KXHYPE15M"),
    "eva_wick_eth_xrp": ("ETH", "KXXRP15M"),
    "eva_wick_eth_sol": ("ETH", "KXSOL15M"),
    "eva_wick_eth_hype": ("ETH", "KXHYPE15M"),
}


def _enabled_books() -> list[str]:
    return [b for b in CROSS_BOTS if b in bot_config.ENABLED_BOTS]


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _board_alignment(fire: KalshiSuggestion) -> tuple[bool, dict[str, Any]]:
    """Whether the EVA board's M15 stance points with this fire.

    Returns (aligned, audit) where audit carries what the board said, so a
    skipped window is as queryable as a traded one. Fail-closed: no board,
    no trade — eva_intel.get_stances already returns None on staleness,
    missing timeframes or an unreachable hub.
    """
    import eva_intel

    coinbase = bot_config.PRODUCT_TO_COINBASE.get(
        str(fire.product_id or "").upper()
    )
    if not coinbase:
        return False, {"eva_gate": "no_product"}
    stances = eva_intel.get_stances(coinbase)
    if not stances:
        return False, {"eva_gate": "board_unavailable"}
    m15 = stances.get("M15") or {}
    stance = str(m15.get("stance") or "neutral").lower()
    conf = float(m15.get("confidence") or 0.0)
    needed = "bullish" if fire.side == "YES" else "bearish"
    min_conf = float(bot_config.EVA_CROSS_MIN_CONF)
    audit = {
        "eva_m15_stance": stance,
        "eva_m15_conf": conf,
        "eva_needed": needed,
        "eva_min_conf": min_conf,
    }
    if stance != needed:
        audit["eva_gate"] = "stance_mismatch"
        return False, audit
    if conf < min_conf:
        audit["eva_gate"] = "low_confidence"
        return False, audit
    audit["eva_gate"] = "aligned"
    return True, audit


def process_fires(suggestions: list[KalshiSuggestion] | None) -> list[dict[str, Any]]:
    """Mirror this tick's EVA-aligned wick fires into the alt 15m books."""
    books = _enabled_books()
    if not books:
        return []
    opened: list[dict[str, Any]] = []
    for fire in suggestions or []:
        if getattr(fire, "bot_id", None) != "eva_wick":
            continue
        if not fire.is_trade() or fire.side not in ("YES", "NO"):
            continue
        try:
            opened.extend(_mirror_fire(fire, books))
        except Exception:
            logger.exception(
                "cross wick failed for fire on %s",
                getattr(fire, "market_ticker", "?"),
            )
    return opened


def _mirror_fire(
    fire: KalshiSuggestion, books: list[str]
) -> list[dict[str, Any]]:
    signal = str(fire.product_id or "").upper()
    targets = [b for b in books if CROSS_BOTS[b][0] == signal]
    if not targets:
        return []
    aligned, audit = _board_alignment(fire)
    if not aligned:
        logger.info(
            "cross wick: %s fire on %s not taken — %s (%s)",
            fire.side, fire.market_ticker, audit.get("eva_gate"), audit,
        )
        return []
    opened: list[dict[str, Any]] = []
    for bot_id in targets:
        pos = _open_target(bot_id, fire, audit)
        if pos:
            opened.append(pos)
    return opened


def _open_target(
    bot_id: str, fire: KalshiSuggestion, audit: dict[str, Any]
) -> dict[str, Any] | None:
    series = CROSS_BOTS[bot_id][1]
    try:
        markets = kalshi_client.get_open_markets(series)
    except Exception:
        logger.exception("%s: failed to list %s markets", bot_id, series)
        return None
    if not markets:
        logger.info("%s: no open %s market", bot_id, series)
        return None
    market = markets[0]
    ticker = str(market.get("ticker") or "")
    if paper.has_open_for_market(ticker, bot_id=bot_id):
        return None
    # Real ask or no trade; Kalshi's empty-book placeholder derives to a
    # ~100¢ "price" nobody offers and is a skip, not a fill.
    ask = kalshi_client.side_ask_cents_from_market(fire.side, market)
    if ask is None or float(ask) >= 99.0:
        logger.info(
            "%s: no real %s ask on %s (ask=%s) — skip, not inventing a fill",
            bot_id, fire.side, ticker, ask,
        )
        return None
    entry = float(ask)

    import kalshi_sizing

    contracts, _ = kalshi_sizing.contracts_for_entry(entry, bot_id=bot_id)
    contracts = kalshi_sizing.clamp_contracts(contracts, entry, bot_id=bot_id)
    if contracts < 1:
        return None

    coin = bot_config.SERIES_TO_PRODUCT.get(series, series)
    close = _parse_ts(market.get("close_time"))
    seconds_left = None
    if close is not None:
        seconds_left = max(
            0.0, (close - datetime.now(timezone.utc)).total_seconds()
        )
    rationale = (
        f"Cross wick: eva_wick fired {fire.side} on {fire.market_ticker} and "
        f"the EVA board's M15 {audit.get('eva_m15_stance')} "
        f"({float(audit.get('eva_m15_conf') or 0):.2f} conf) points with it; "
        f"buying {contracts} ct {fire.side} on {coin} at the {entry:.1f}¢ "
        f"ask, held to the same quarter-hour settle. Tests whether a "
        f"confirmed major-coin favourite carries across assets — no view on "
        f"{coin}'s own pricing is applied."
    )
    sug = KalshiSuggestion(
        series=series,
        market_ticker=ticker,
        side=fire.side,
        contracts=int(contracts),
        entry_cents=entry,
        expiry_ts=str(market.get("close_time") or "") or None,
        rationale=rationale,
        product_id=coin,
        mid_cents=kalshi_client.mid_cents_from_market(market),
        spot=None,
        strike=None,
        trigger_type="eva_wick_cross",
        trigger_name=f"cross_{CROSS_BOTS[bot_id][0].lower()}_{coin.lower()}",
        setup_tags=[
            "eva_wick_cross",
            f"signal_{CROSS_BOTS[bot_id][0].lower()}",
            f"side_{fire.side.lower()}",
            f"src_{fire.market_ticker}",
            f"eva_conf_{float(audit.get('eva_m15_conf') or 0):.2f}",
        ],
        cycle_id=fire.cycle_id,
        bot_id=bot_id,
        seconds_to_expiry=seconds_left,
    )
    pos = paper.open_trade(sug)
    if pos:
        logger.info(
            "%s: %d ct %s on %s @ %.1f¢ (signal %s, EVA M15 %s %.2f)",
            bot_id, contracts, fire.side, ticker, entry,
            fire.market_ticker, audit.get("eva_m15_stance"),
            float(audit.get("eva_m15_conf") or 0),
        )
    return pos
