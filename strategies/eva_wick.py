"""EVA wick, redefined 2026-09-17 — buy the mid-window favourite.

This sleeve replaces the fade strategy that used to carry this bot_id. The old
one bought the cheap side (20-33c) on a wick-fade setup and lost 17.6 dollars
over 87 trades. The reason it lost is the finding this rule is built on:
buying *blind* in the 20-33c band, with no signal whatsoever, loses ~11 points
against the price paid, and the old strategy lost 10.7. Its trigger, M15 gate
and excursion thresholds added nothing measurable. The band was the whole
result.

The same search says the opposite band is mispriced the other way, but only in
the middle of the window:

    12.5-15 min left   +2.7 pts  (t=0.3)  <- where the bot used to trade
    4-10 min left     +13.0 pts  (t=3.5)  <- this rule
    last 2 min         -4.9 pts  (t=-3.3) <- where eva_arb trades and loses

So: with 4 to 10 minutes left, buy whichever side the book prices at 67-80c,
one entry per window, held to settlement. Deliberately no directional signal,
no EVA stance gate and no excursion filter — every one of those was tested and
added nothing over the band and the clock, and the two stance gates we did ship
both failed out of sample.

Backtest over the epoch (151 entries, Sep 3-17, one per market, held to
settle, Kalshi taker fee charged): 86.1% win at 73.3c, +$0.098/contract priced
at mid+1c, t=3.48, 13 of 15 days profitable, worst drawdown $1.84/contract and
never more than 2 losses in a row. It stays profitable even paying 6c through
the mid, which is what makes it worth trading before we have recorded bid/ask
for this part of the window: the edge is far larger than any plausible spread.

Held to settlement on purpose. A stop would probably help - 86% win at 73c
means the losses are the whole variance - but nothing about a stop here has
been measured, and shipping unmeasured mechanics is how the last two filters
got us. Hold-to-settle is exactly what the backtest scored.

Like eva_arb, this builds its suggestion directly rather than going through
kalshi_finalize, which hard-blocks anything above 55c.
"""

from __future__ import annotations

import logging
from typing import Any

import bot_config
import kalshi_triggers
import paper
from models import KalshiSuggestion
from strategies.context import SharedCycleContext

logger = logging.getLogger(__name__)

_DONE_META_KEY = "eva_fav_done"


def _arm_meta(arm: dict[str, Any]) -> dict[str, Any]:
    raw = arm.get("meta") if isinstance(arm.get("meta"), dict) else arm.get("meta_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json

            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


class EvaWickStrategy:
    bot_id = "eva_wick"
    display_name = "EVA favourite (mid-window)"
    needs_htf_bias = False  # never triggers the Claude HTF refresh

    def size_for_fill(
        self,
        ctx: SharedCycleContext,
        side: str,
        entry_cents: float,
        contracts: int,
    ) -> tuple[int, float, dict[str, Any]]:
        """The fill this book would really get: (contracts, price, context).

        The live book does not need this — it sends a real order and reads the
        count and average price back off the response, which is why its ledger
        carries true fills. Paper clones of this rule override it to simulate
        the same thing against the published ladder, so they cannot record a
        fill against liquidity that was not there. Returning 0 contracts
        abandons the entry without arming the window, leaving the setup live
        for a later tick.
        """
        return contracts, entry_cents, {}

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        mid = ctx.yes_mid_cents
        if mid is None:
            return None
        minutes_left = kalshi_triggers.minutes_to_expiry(
            ctx.expiry_ts, now=ctx.clock()
        )
        if minutes_left is None or minutes_left <= 0:
            return None
        seconds_left = float(minutes_left) * 60.0

        # One entry per window, and never re-enter after an exit.
        arm = paper.get_window_arm(self.bot_id, ctx.market_ticker)
        meta = _arm_meta(arm) if arm else {}
        if meta.get(_DONE_META_KEY):
            return None
        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return None

        # The clock is half the rule. Outside this band the same trade is
        # either fairly priced (early) or actively bad (last 2 minutes).
        lo_sec = float(bot_config.EVA_FAV_MIN_SECONDS_LEFT)
        hi_sec = float(bot_config.EVA_FAV_MAX_SECONDS_LEFT)
        if not (lo_sec <= seconds_left <= hi_sec):
            return None

        # Favourite = whichever side the book has above 50c. No view of our
        # own is applied; the mispricing is in the market's own pricing.
        mid_f = float(mid)
        side = "YES" if mid_f >= 50.0 else "NO"
        side_now = kalshi_triggers.side_mid_cents(side, mid_f)

        min_px = float(bot_config.EVA_FAV_MIN_ENTRY_CENTS)
        max_px = float(bot_config.EVA_FAV_MAX_ENTRY_CENTS)
        if not (min_px <= side_now <= max_px):
            if ctx.near_decision:
                logger.debug(
                    "eva_wick: %s favourite at %.1fc outside %.0f-%.0f band",
                    ctx.market_ticker, side_now, min_px, max_px,
                )
            return None

        # Regime gates (kalshi_regime.py): daily loss stop, plus the weekend
        # watch that replaced this book's blanket weekend pause on 2026-09-24.
        # Sits after the band check, so the chop shadow costs one OHLC call
        # per would-be entry, not per tick. Deliberately NO loss cooldown on
        # this book: the sweep says skipping after losses skips winners here
        # (negative in 9 of 12 cells).
        #
        # Paper-only clones are exempt. The weekend watch is calibrated on the
        # live book's own weekday distribution and the daily stop exempts
        # paper by design, so applying either to a shadow book would censor
        # the sample it exists to collect.
        if self.bot_id not in bot_config.PAPER_ONLY_BOTS:
            import kalshi_regime

            gate_reason, chop = kalshi_regime.entry_gate(
                self.bot_id, ctx.coinbase, now=ctx.clock()
            )
            if gate_reason:
                logger.info(
                    "%s gated (%s) chop=%s on %s",
                    self.bot_id, gate_reason, chop.get("chop"),
                    ctx.market_ticker,
                )
                return None

        # Qualify on the mid, but pay the ask. A limit at the mid only fills
        # when the book comes to us, which selects against this sleeve: the
        # favourite runs away precisely when the tape is confirming it, so the
        # trades we miss are the ones we wanted. The backtest is still positive
        # paying 6c through the mid, so crossing is affordable; overpaying past
        # max_pay is not, and skipping there also records the wide-book cases
        # we have no historical bid/ask for.
        import kalshi_client

        ask = kalshi_client.side_ask_cents_from_market(side, ctx.market)
        entry = side_now if ask is None else float(ask)
        max_pay = float(bot_config.EVA_FAV_MAX_PAY_CENTS)
        if entry > max_pay:
            logger.info(
                "eva_wick: %s %s ask %.1fc over max pay %.0fc (mid %.1fc) — skip",
                ctx.market_ticker, side, entry, max_pay, side_now,
            )
            return None

        import kalshi_sizing

        contracts, _ = kalshi_sizing.contracts_for_entry(entry, bot_id=self.bot_id)
        contracts = kalshi_sizing.clamp_contracts(contracts, entry, bot_id=self.bot_id)
        if contracts < 1:
            return None

        contracts, entry, fill_meta = self.size_for_fill(
            ctx, side, entry, contracts
        )
        if contracts < 1:
            return None

        label = bot_config.product_label(ctx.coinbase)
        dir_word = "up" if side == "YES" else "down"
        rationale = (
            f"{label} is {dir_word} through the strike and the book prices that "
            f"side at {side_now:.0f}¢ with ~{seconds_left / 60.0:.1f} min left; "
            f"paying the {entry:.1f}¢ ask ({entry - side_now:+.1f}¢ vs mid). "
            f"Mid-window favourites in the {min_px:.0f}-{max_px:.0f}¢ band settled "
            f"86% over the epoch against a 73¢ average cost — the longshot side "
            f"is the overpriced one here. No directional view of our own: the "
            f"edge is in the market's own pricing of the tail. Held to settlement."
        )

        paper.set_window_arm(
            bot_id=self.bot_id,
            market_ticker=ctx.market_ticker,
            armed_side=side,
            arm_yes_mid=mid_f,
            arm_side_mid=side_now,
            arm_spot=ctx.spot,
            arm_strike=ctx.strike,
            ict_bias=None,
            htf_bias=None,
            meta={
                _DONE_META_KEY: 1,
                "entry_side": side,
                "entry_side_mid": side_now,
                "entry_ask": entry,
                "ask_vs_mid": round(entry - side_now, 2),
                "seconds_left": seconds_left,
                "cycle_id": ctx.cycle_id,
                **fill_meta,
            },
        )
        return KalshiSuggestion(
            series=ctx.series,
            market_ticker=ctx.market_ticker,
            side=side,
            contracts=contracts,
            entry_cents=float(entry),
            expiry_ts=ctx.expiry_ts,
            rationale=rationale,
            product_id=ctx.product_id,
            fair_yes_cents=ctx.fair_yes_cents,
            mid_cents=mid_f,
            edge_cents=ctx.edge_cents,
            spot=ctx.spot,
            strike=ctx.strike,
            spot_vs_strike_pct=ctx.spot_vs_strike_pct,
            tau_sec=ctx.tau_sec,
            sigma=ctx.sigma,
            prior_5m_ret=ctx.prior_5m_ret,
            prior_15m_ret=ctx.prior_15m_ret,
            prior_1h_ret=ctx.prior_1h_ret,
            trigger_type="eva_wick",
            trigger_name="midwindow_favourite",
            setup_tags=[
                "eva_wick",
                f"favourite_{side.lower()}",
                f"t{seconds_left / 60.0:.0f}min",
                "midwindow_favourite",
            ],
            cycle_id=ctx.cycle_id,
            bot_id=self.bot_id,
            seconds_to_expiry=seconds_left,
        )
