"""Hourly piggyback books — mirror eva_wick fires into the top-of-hour binaries.

Spec'd by the operator 2026-09-30. When the live eva_wick rule fires on a 15m
BTC or ETH market, these books immediately buy the same side of the *hourly*
threshold series (KXBTCD / KXETHD) at fixed strike rungs past spot, paper only:

    eva_wick_1h_ladder   4 ct at rung 1, 2 ct at rung 2, 1 ct at rung 3.
                         At most once per hourly event per product — the first
                         wick fire inside the hour places the ladder, later
                         fires in the same hour do nothing.
    eva_wick_1h_flat     1 ct at rungs 1 and 2 on *every* wick fire, whatever
                         the clock says.

A "rung" is a strike on the hourly grid past spot in the signal direction:
wick fires NO with BTC at 84,340 → rungs are the 84,299.99 / 84,199.99 /
84,099.99 thresholds (buy NO: paid when BTC settles at or under the strike);
a YES fire mirrors above spot. The exchange grid is $100 for KXBTCD and $5
for KXETHD, which is the same distance in percent terms (~0.11% vs ~0.15%),
so "rung n" means the same thing on both products without hand-picking ETH
offsets. The operator's "$100/$200/$300 away" maps to rungs 1/2/3 exactly as
in the spec's own example.

These books are deliberately blind. No volatility scaling, no price band, no
time gate beyond what eva_wick itself enforces (it only fires with 4-10 min
left in a 15m window, so there are always at least ~4 minutes to the hourly
settle). The distances are unvalidated guesses and the whole point of the
books is to put a forward record under them before anyone tunes anything.

Accounting matches the other paper books: entry is the real ask off the
market the exchange is quoting (a rung with no ask is a skip, not an invented
fill — the eva_arb post-mortem rule), settlement pays $1.00 or $0.00 through
the same settle_due() poller every other book uses, and no taker fee is
charged, the same known overstatement that sits on every paper row in this
ledger.

Not a registry strategy on purpose: the cycle's decide(ctx) shape is one
market per series per tick, and this is a cross-series basket triggered by
another bot's entry. run_strategy_cycle calls process_fires() with the tick's
suggestions after the strategy loop; everything else lives here.
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

LADDER_BOT = "eva_wick_1h_ladder"
FLAT_BOT = "eva_wick_1h_flat"

# Wick fires carry the short product id ("BTC"/"ETH"); altcoin clones have no
# hourly series listed, so their fires are ignored by construction.
HOURLY_SERIES: dict[str, str] = {
    "BTC": "KXBTCD",
    "ETH": "KXETHD",
}

# Contracts per rung; rung 1 is the first strike past spot in the signal
# direction. The ladder's 4/2/1 weighting and the flat book's 1/1 are the
# operator's spec verbatim.
RUNG_QTY: dict[str, tuple[int, ...]] = {
    LADDER_BOT: (4, 2, 1),
    FLAT_BOT: (1, 1),
}


def _enabled_books() -> list[str]:
    return [b for b in (LADDER_BOT, FLAT_BOT) if b in bot_config.ENABLED_BOTS]


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


def _next_hourly_event(series: str) -> tuple[str | None, list[dict[str, Any]]]:
    """The soonest-settling open hourly event: (event_ticker, strikes asc).

    Only threshold ("greater") markets qualify — KXBTC/KXETH range markets
    share the same hour but a between-strike does not express "past this
    level", which is what the rungs mean.
    """
    markets = kalshi_client.get_open_markets(series)
    groups: dict[str, dict[str, Any]] = {}
    for m in markets:
        event = str(m.get("event_ticker") or "")
        close = _parse_ts(m.get("close_time"))
        if not event or close is None:
            continue
        if m.get("floor_strike") is None:
            continue
        if str(m.get("strike_type") or "greater").lower() != "greater":
            continue
        g = groups.setdefault(event, {"close": close, "markets": []})
        g["markets"].append(m)
    if not groups:
        return None, []
    event = min(groups, key=lambda ev: groups[ev]["close"])
    rows = sorted(
        groups[event]["markets"], key=lambda m: float(m["floor_strike"])
    )
    return event, rows


def _pick_rungs(
    markets: list[dict[str, Any]], spot: float, side: str
) -> list[dict[str, Any]]:
    """Strikes past spot in the signal direction, nearest first.

    NO → thresholds below spot (buy NO: paid at/under the strike), so a drop
    that keeps going pays rung after rung. YES mirrors above spot.
    """
    if side == "NO":
        below = [m for m in markets if float(m["floor_strike"]) < spot]
        return below[::-1]
    above = [m for m in markets if float(m["floor_strike"]) > spot]
    return above


def process_fires(suggestions: list[KalshiSuggestion] | None) -> list[dict[str, Any]]:
    """Mirror this tick's eva_wick fires into the enabled hourly books."""
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
                "hourly piggyback failed for wick fire on %s",
                getattr(fire, "market_ticker", "?"),
            )
    return opened


def _mirror_fire(
    fire: KalshiSuggestion, books: list[str]
) -> list[dict[str, Any]]:
    series = HOURLY_SERIES.get(str(fire.product_id or "").upper())
    if series is None:
        return []
    if fire.spot is None:
        logger.warning(
            "hourly piggyback: wick fire on %s carries no spot — cannot "
            "place rungs, skipping the basket",
            fire.market_ticker,
        )
        return []
    spot = float(fire.spot)
    event, strikes = _next_hourly_event(series)
    if not event:
        logger.info("hourly piggyback: no open %s threshold event", series)
        return []
    rungs = _pick_rungs(strikes, spot, fire.side)
    opened: list[dict[str, Any]] = []
    for bot_id in books:
        if bot_id == LADDER_BOT and paper.has_any_for_event(bot_id, event):
            logger.info(
                "%s: already placed on %s — once per hourly event", bot_id, event
            )
            continue
        for idx, qty in enumerate(RUNG_QTY[bot_id]):
            if idx >= len(rungs):
                logger.info(
                    "%s: only %d strike(s) past spot %.2f on %s — rung %d "
                    "unavailable",
                    bot_id, len(rungs), spot, event, idx + 1,
                )
                break
            pos = _open_rung(bot_id, fire, series, rungs[idx], idx + 1, qty)
            if pos:
                opened.append(pos)
    return opened


def _open_rung(
    bot_id: str,
    fire: KalshiSuggestion,
    series: str,
    market: dict[str, Any],
    rung: int,
    contracts: int,
) -> dict[str, Any] | None:
    ticker = str(market.get("ticker") or "")
    strike = float(market["floor_strike"])
    # Real ask or no trade. Inventing a fill from the mid in a book nobody is
    # quoting is how paper records go bad; a skipped rung is itself data.
    ask = kalshi_client.side_ask_cents_from_market(fire.side, market)
    if ask is None:
        logger.info(
            "%s: no %s ask on %s (rung %d) — skip, not inventing a fill",
            bot_id, fire.side, ticker, rung,
        )
        return None
    close = _parse_ts(market.get("close_time"))
    seconds_left = None
    if close is not None:
        seconds_left = max(
            0.0, (close - datetime.now(timezone.utc)).total_seconds()
        )
    label = bot_config.product_label(
        bot_config.PRODUCT_TO_COINBASE.get(
            str(fire.product_id or "").upper(), str(fire.product_id)
        )
    )
    rationale = (
        f"Piggyback rung {rung}: eva_wick fired {fire.side} on "
        f"{fire.market_ticker} with {label} spot at {spot_fmt(fire.spot)}; "
        f"buying {contracts} ct {fire.side} past the {strike:,.2f} threshold "
        f"at the {ask:.1f}¢ ask, held to the hourly settle. Blind fixed-rung "
        f"distance — this book exists to price the idea, not to prove it."
    )
    sug = KalshiSuggestion(
        series=series,
        market_ticker=ticker,
        side=fire.side,
        contracts=int(contracts),
        entry_cents=float(ask),
        expiry_ts=str(market.get("close_time") or "") or None,
        rationale=rationale,
        product_id=str(fire.product_id or ""),
        mid_cents=kalshi_client.mid_cents_from_market(market),
        spot=fire.spot,
        strike=strike,
        trigger_type="eva_wick_hourly",
        trigger_name=(
            "wick_piggyback_ladder" if bot_id == LADDER_BOT
            else "wick_piggyback_flat"
        ),
        setup_tags=[
            "eva_wick_hourly",
            f"rung{rung}",
            f"side_{fire.side.lower()}",
            f"src_{fire.market_ticker}",
        ],
        cycle_id=fire.cycle_id,
        bot_id=bot_id,
        seconds_to_expiry=seconds_left,
    )
    pos = paper.open_trade(sug)
    if pos:
        logger.info(
            "%s: rung %d — %d ct %s on %s @ %.1f¢ (spot %.2f, strike %.2f)",
            bot_id, rung, contracts, fire.side, ticker, ask,
            float(fire.spot or 0.0), strike,
        )
    return pos


def spot_fmt(spot: float | None) -> str:
    return f"{float(spot):,.0f}" if spot is not None else "?"
