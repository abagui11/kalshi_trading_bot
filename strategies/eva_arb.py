"""EVA arb bot — last-2-minute favorite-dip inefficiency, paper (2026-09-08).

Premise under test (Eva #3): near window close, outsized moves against the
already-decided side are noise — Kalshi stops matching ~45 seconds before
settlement, so a favorite that touched 90¢ and prints back inside 75–85¢
late is a discounted near-certain winner *if* the dip really is noise.

Rules:

* Track the window's YES-mid high/low water every cycle tick (60s samples;
  brief touches between ticks are missed — same information the bot would
  have live).
* Only act inside the final ``EVA_ARB_WINDOW_MINUTES``, and never with less
  than ``EVA_ARB_MIN_SECONDS_LEFT`` on the clock (couldn't get filled).
* Favored side = the one that printed >= ``EVA_ARB_TOUCH_CENTS`` earlier in
  the window. Buy it only when its mid sits inside the 75–85¢ dip zone.
* One entry per window, held to settlement — there is no time to manage.

The logger evidence (deploy/lastmin_logger.py, ~190 windows) has the favored
side settling ~70% on these dips vs a ~80¢ cost, i.e. currently -EV. This
bot exists to test the premise with paper money in the same epoch as the
other sleeves; it must not route live until the book clears break-even.
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

_DONE_META_KEY = "eva_arb_done"


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


class EvaArbStrategy:
    bot_id = "eva_arb"
    display_name = "EVA arb"
    needs_htf_bias = False

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        mid = ctx.yes_mid_cents
        if mid is None:
            return None
        minutes_left = kalshi_triggers.minutes_to_expiry(
            ctx.expiry_ts, now=ctx.clock()
        )
        if minutes_left is None or minutes_left <= 0:
            return None

        # 1) High/low-water tracking every tick, whole window.
        mid_f = float(mid)
        arm = paper.get_window_arm(self.bot_id, ctx.market_ticker)
        if arm is None:
            hi, lo = mid_f, mid_f
            paper.set_window_arm(
                bot_id=self.bot_id,
                market_ticker=ctx.market_ticker,
                armed_side="TRACK",
                arm_yes_mid=mid_f,
                arm_side_mid=mid_f,
                arm_spot=ctx.spot,
                arm_strike=ctx.strike,
                ict_bias=None,
                htf_bias=None,
                meta={"hi_yes": hi, "lo_yes": lo, "cycle_id": ctx.cycle_id},
            )
            meta: dict[str, Any] = {}
        else:
            meta = _arm_meta(arm)
            hi = max(float(meta.get("hi_yes") or mid_f), mid_f)
            lo = min(float(meta.get("lo_yes") or mid_f), mid_f)
            if hi != meta.get("hi_yes") or lo != meta.get("lo_yes"):
                paper.update_window_arm_meta(
                    self.bot_id, ctx.market_ticker, {"hi_yes": hi, "lo_yes": lo}
                )

        if meta.get(_DONE_META_KEY):
            return None
        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return None

        # 2) Only the final minutes are tradeable, and not the frozen tail.
        if minutes_left > float(bot_config.EVA_ARB_WINDOW_MINUTES):
            return None
        if minutes_left * 60.0 < float(bot_config.EVA_ARB_MIN_SECONDS_LEFT):
            return None

        # 3) Favored side that touched 90¢, now printing inside the dip zone.
        touch = float(bot_config.EVA_ARB_TOUCH_CENTS)
        z_lo = float(bot_config.EVA_ARB_MIN_ENTRY_CENTS)
        z_hi = float(bot_config.EVA_ARB_MAX_ENTRY_CENTS)
        side = None
        if hi >= touch and z_lo <= mid_f <= z_hi:
            side = "YES"
        elif lo <= 100.0 - touch and z_lo <= (100.0 - mid_f) <= z_hi:
            side = "NO"
        if side is None:
            return None
        side_now = kalshi_triggers.side_mid_cents(side, mid_f)

        import kalshi_sizing

        contracts, _ = kalshi_sizing.contracts_for_entry(side_now)
        contracts = kalshi_sizing.clamp_contracts(contracts, side_now)
        if contracts < 1:
            return None

        label = bot_config.product_label(ctx.coinbase)
        peak = hi if side == "YES" else 100.0 - lo
        rationale = (
            f"{label} {side} was priced {peak:.0f}¢ earlier this window and has "
            f"dipped to {side_now:.0f}¢ with ~{minutes_left * 60.0:.0f}s left. "
            f"Late swings against a decided window are usually noise once Kalshi "
            f"stops matching — buying the discounted favorite and holding to "
            f"settlement."
        )

        paper.update_window_arm_meta(
            self.bot_id,
            ctx.market_ticker,
            {_DONE_META_KEY: 1, "entry_side": side, "peak_side_mid": peak},
        )
        return KalshiSuggestion(
            series=ctx.series,
            market_ticker=ctx.market_ticker,
            side=side,
            contracts=contracts,
            entry_cents=float(side_now),
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
            trigger_type="eva_arb",
            trigger_name="lastmin_dip",
            setup_tags=[
                "eva_arb",
                f"favored_{side.lower()}",
                f"peak{peak:.0f}",
                "lastmin_dip",
            ],
            cycle_id=ctx.cycle_id,
            bot_id=self.bot_id,
            seconds_to_expiry=minutes_left * 60.0,
        )
