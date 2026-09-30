"""eva_wick on the altcoin 15m series — XRP, SOL, HYPE. Paper only.

The rule is not re-derived here. These are ``EvaWickStrategy`` with a
different bot_id and a different series, so the band, the clock, the qualify-
on-mid/pay-the-ask mechanic and the hold-to-settle are the shipped ones by
construction and cannot drift from the live book.

What they are for: the favourite-longshot mispricing the live rule feeds on
was measured on KXBTC15M and KXETH15M only. It is either a property of these
15m crypto binaries in general — in which case it should show up on XRP, SOL
and HYPE too — or it is a property of the two books we happen to have looked
at, which would mean the live result is more fragile than its t-statistic
suggests. There is no backtest behind these books; they start from zero and
earn a record forward, which is the whole point.

Accounting matches the live book exactly so the rows are comparable: entry is
the real ask (skipped above EVA_FAV_MAX_PAY_CENTS, which is what makes a wide
book a skip rather than a bad fill), settlement pays $1.00 or $0.00, and no
Kalshi taker fee is charged — the same known overstatement that sits on every
other book in this ledger, roughly 1.4¢ per contract at 73¢.

The one thing added on top of the live rule is fill realism, and it exists to
stop these books flattering themselves. The live path sends a real order and
writes the returned ``fill_count`` and ``average_fill_price`` into the ledger,
so its entries are true fills including partials. A paper clone has no such
feedback, so ``size_for_fill`` reconstructs it: walk the published ask ladder
with the limit the live order would carry (the ask plus
KALSHI_LIVE_TAKE_CENTS) and record the count and volume-weighted price that
produces. On BTC/ETH this would almost never bind — the touch alone holds
thousands of contracts. On HYPE, which trades roughly a seventh of ETH's
volume, top-of-book size has been observed swinging between 5 and 283
contracts inside a minute, which is exactly why the fill has to be simulated
against the ladder rather than assumed.

No regime gate. The weekend pause covers eva_streak, the weekend watch is
keyed to the live wick's own weekday baseline, and the daily loss stop exempts
paper books by design — these are evidence-collection books and gating them
would censor the sample before it exists.
"""

from __future__ import annotations

import logging
from typing import Any

import bot_config
import kalshi_client
from strategies.context import SharedCycleContext
from strategies.eva_wick import EvaWickStrategy

logger = logging.getLogger(__name__)


class EvaWickAltStrategy(EvaWickStrategy):
    """One altcoin clone of the live favourite rule, pinned to one series."""

    needs_htf_bias = False

    def __init__(self, bot_id: str, series: str) -> None:
        self.bot_id = bot_id
        self.asset = bot_config.SERIES_TO_PRODUCT.get(series.upper(), series)
        self.display_name = f"EVA favourite · {self.asset} (paper)"
        # Read by the cycle: this book sees its own series and nothing else,
        # so it can never be handed the BTC/ETH market the live book trades.
        self.series_filter: tuple[str, ...] = (series.upper(),)

    def size_for_fill(
        self,
        ctx: SharedCycleContext,
        side: str,
        entry_cents: float,
        contracts: int,
    ) -> tuple[int, float, dict[str, Any]]:
        """Simulate the live book's immediate-or-cancel order on this ladder.

        The live path reads ``fill_count`` and ``average_fill_price`` back off
        the exchange and writes those into the ledger, so the honest paper
        analogue is to walk the published ladder with the same limit the live
        order would carry — the ask plus KALSHI_LIVE_TAKE_CENTS of aggression
        — and record the count and volume-weighted price that produces.

        Walking the ladder rather than stopping at the touch matters here:
        top-of-book size on these books swings by two orders of magnitude
        between ticks, so trimming to the touch would manufacture 3-contract
        clips in markets where a real order would have filled in full one cent
        higher.
        """
        limit = float(entry_cents) + float(bot_config.KALSHI_LIVE_TAKE_CENTS)
        meta: dict[str, Any] = {
            "requested_ct": int(contracts),
            "touch_ask": round(float(entry_cents), 2),
            "fill_limit_cents": round(limit, 2),
            "spread_cents": kalshi_client.spread_cents_from_market(ctx.market),
            "touch_depth_ct": kalshi_client.side_ask_depth_contracts(
                side, ctx.market
            ),
        }
        if not bool(bot_config.EVA_FAV_ALT_TRIM_TO_DEPTH):
            meta["fill_model"] = "off"
            return contracts, entry_cents, meta

        ladder = kalshi_client.get_ask_ladder(side, ctx.market_ticker)
        if not ladder:
            # The book is unreadable, not empty — a fetch failure must not be
            # recorded as "no liquidity". Fall through on the touch price and
            # flag the row so it can be excluded from any fill analysis.
            logger.warning(
                "%s: ladder unavailable for %s — filling at the touch",
                self.bot_id, ctx.market_ticker,
            )
            meta["fill_model"] = "unavailable"
            return contracts, entry_cents, meta

        filled, vwap = kalshi_client.simulate_ioc_fill(ladder, contracts, limit)
        meta["fill_model"] = "ladder"
        meta["ladder_levels"] = len(ladder)
        if filled < 1 or vwap is None:
            logger.info(
                "%s: %s %s nothing resting inside %.1f¢ on %s — no entry",
                self.bot_id, self.asset, side, limit, ctx.market_ticker,
            )
            return 0, entry_cents, meta
        if filled < contracts:
            logger.info(
                "%s: %s %s partial fill %d/%d ct at %.2f¢ (touch %.1f¢) on %s",
                self.bot_id, self.asset, side, filled, contracts, vwap,
                entry_cents, ctx.market_ticker,
            )
        meta["filled_ct"] = filled
        meta["fill_vwap_cents"] = vwap
        meta["slippage_vs_touch"] = round(vwap - float(entry_cents), 2)
        return filled, vwap, meta


def alt_wick_strategies() -> list[EvaWickAltStrategy]:
    """One clone per configured altcoin series, in registry order."""
    return [
        EvaWickAltStrategy(bot_id, series)
        for bot_id, series in bot_config.ALT_WICK_VARIANTS.items()
    ]
