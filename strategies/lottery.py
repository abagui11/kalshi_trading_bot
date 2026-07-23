"""Lottery bot — hail-mary + coinflip tickets in last 5 minutes."""

from __future__ import annotations

import bot_config
import kalshi_finalize
import kalshi_triggers
import paper
from models import KalshiSuggestion
from strategies.context import SharedCycleContext


def _lottery_contracts(entry_cents: float) -> int:
    bankroll = float(bot_config.KALSHI_BANKROLL_USD)
    budget = max(0.0, bankroll * float(bot_config.LOTTERY_DEPLOY_PCT))
    price = float(entry_cents) / 100.0
    cap = max(1, int(bot_config.LOTTERY_MAX_CONTRACTS))
    if price <= 0:
        return 0
    contracts = max(0, min(cap, int(budget // price)))
    if contracts < 1 and budget >= price:
        contracts = 1
    return contracts


class LotteryStrategy:
    bot_id = "lottery"
    display_name = "Lottery / hail-mary"
    needs_htf_bias = False

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        base = ctx.with_bot(self.bot_id)
        mid = ctx.yes_mid_cents
        if mid is None:
            return None

        if not kalshi_triggers.in_lottery_bot_window(
            ctx.expiry_ts,
            minutes=float(bot_config.LOTTERY_WINDOW_MINUTES),
        ):
            return None

        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return None
        if paper.has_pending_order(ctx.market_ticker, bot_id=self.bot_id):
            return None

        # Past cancel deadline — do not place new lottery tickets.
        minutes_left = kalshi_triggers.minutes_to_expiry(ctx.expiry_ts)
        if minutes_left is not None and minutes_left <= float(
            bot_config.LOTTERY_CANCEL_MINUTES_BEFORE_EXPIRY
        ):
            return None

        cancel_at = kalshi_triggers.lottery_cancel_at_iso(ctx.expiry_ts)
        swept, sweep_dir = kalshi_triggers.prior_5m_swept_liquidity(ctx.m5_bars)

        # --- Hail Mary: cheap side 5–10¢ after liquidity sweep ---
        yes_mid = float(mid)
        no_mid = 100.0 - yes_mid
        hail_side: str | None = None
        hail_entry: float | None = None
        if kalshi_triggers.is_lottery_ticket(yes_mid) and swept:
            hail_side, hail_entry = "YES", yes_mid
        elif kalshi_triggers.is_lottery_ticket(no_mid) and swept:
            hail_side, hail_entry = "NO", no_mid

        if hail_side and hail_entry is not None:
            contracts = _lottery_contracts(hail_entry)
            if contracts < 1:
                return kalshi_finalize.make_skip(
                    rationale=(
                        f"lottery hail-mary undersized at {hail_entry:.1f}¢ "
                        f"(sweep={sweep_dir})"
                    ),
                    base=base,
                    htf_bias=(ctx.htf.htf_bias if ctx.htf else "unknown"),
                    setup_tags=["lottery", "hail_mary", sweep_dir or "sweep"],
                    skip_codes=["undersized"],
                    trigger_type="lottery_ticket",
                    trigger_name="hail_mary",
                )
            sug = KalshiSuggestion(
                series=ctx.series,
                market_ticker=ctx.market_ticker,
                side=hail_side,
                contracts=contracts,
                entry_cents=float(hail_entry),
                expiry_ts=ctx.expiry_ts,
                rationale=(
                    f"Lottery hail-mary: {hail_side} @ live mid {hail_entry:.1f}¢ "
                    f"(band 5–10¢) after prior M5 {sweep_dir} liquidity sweep. "
                    f"Limit working until cancel_at={cancel_at}; "
                    "KalshiRules lottery ticket."
                ),
                product_id=ctx.product_id,
                fair_yes_cents=ctx.fair_yes_cents,
                mid_cents=yes_mid,
                edge_cents=ctx.edge_cents,
                spot=ctx.spot,
                strike=ctx.strike,
                spot_vs_strike_pct=ctx.spot_vs_strike_pct,
                tau_sec=ctx.tau_sec,
                sigma=ctx.sigma,
                prior_5m_ret=ctx.prior_5m_ret,
                prior_15m_ret=ctx.prior_15m_ret,
                prior_1h_ret=ctx.prior_1h_ret,
                trigger_type="lottery_ticket",
                trigger_name="hail_mary",
                setup_tags=["lottery", "hail_mary", sweep_dir or "sweep"],
                cycle_id=ctx.cycle_id,
                bot_id=self.bot_id,
                pending_limit=True,
                cancel_at_ts=cancel_at,
                seconds_to_expiry=(
                    minutes_left * 60.0 if minutes_left is not None else None
                ),
                structure_chart_path=(
                    ctx.htf.structure_chart_path if ctx.htf else None
                ),
                entry_chart_path=(ctx.htf.entry_chart_path if ctx.htf else None),
            )
            return sug

        # --- Coinflip: near-mark mid 45–55¢ → cheap 7–10¢ limit on bias side ---
        # Pick the side near 50¢; limit far below mid (underdog spike ticket).
        if not (
            bot_config.LOTTERY_COINFLIP_MIN_CENTS
            <= yes_mid
            <= bot_config.LOTTERY_COINFLIP_MAX_CENTS
        ):
            return None

        side: str | None = None
        if ctx.htf and ctx.htf.side in ("YES", "NO"):
            side = ctx.htf.side
        elif ctx.htf and ctx.htf.htf_bias == "bull":
            side = "YES"
        elif ctx.htf and ctx.htf.htf_bias == "bear":
            side = "NO"
        elif ctx.fair_yes_cents is not None:
            side = "YES" if ctx.fair_yes_cents >= 50 else "NO"
        else:
            return kalshi_finalize.make_skip(
                rationale=(
                    f"coinflip mid {yes_mid:.1f}¢ but no bias to pick underdog side"
                ),
                base=base,
                skip_codes=["coinflip_no_bias"],
                setup_tags=["lottery", "coinflip"],
                trigger_type="lottery_ticket",
                trigger_name="coinflip",
            )

        limit = float(bot_config.LOTTERY_COINFLIP_LIMIT_MAX_CENTS)
        # Prefer mid of 7–10 band (use 8.5 as default intended).
        limit = (
            float(bot_config.LOTTERY_COINFLIP_LIMIT_MIN_CENTS)
            + float(bot_config.LOTTERY_COINFLIP_LIMIT_MAX_CENTS)
        ) / 2.0
        contracts = _lottery_contracts(limit)
        if contracts < 1:
            return None

        htf_bias = ctx.htf.htf_bias if ctx.htf else "unknown"
        sug = KalshiSuggestion(
            series=ctx.series,
            market_ticker=ctx.market_ticker,
            side=side,
            contracts=contracts,
            entry_cents=limit,
            expiry_ts=ctx.expiry_ts,
            rationale=(
                f"Lottery coinflip: YES mid≈{yes_mid:.1f}¢ (45–55¢ near mark). "
                f"Bias {htf_bias} → {side} limit @{limit:.1f}¢ (7–10¢ underdog ticket), "
                f"not buying the 50¢ coinflip. Cancel_at={cancel_at}."
            ),
            product_id=ctx.product_id,
            fair_yes_cents=ctx.fair_yes_cents,
            mid_cents=yes_mid,
            edge_cents=ctx.edge_cents,
            spot=ctx.spot,
            strike=ctx.strike,
            spot_vs_strike_pct=ctx.spot_vs_strike_pct,
            tau_sec=ctx.tau_sec,
            sigma=ctx.sigma,
            prior_5m_ret=ctx.prior_5m_ret,
            prior_15m_ret=ctx.prior_15m_ret,
            prior_1h_ret=ctx.prior_1h_ret,
            trigger_type="lottery_ticket",
            trigger_name="coinflip",
            setup_tags=["lottery", "coinflip"],
            h1_bias_tag=htf_bias,
            cycle_id=ctx.cycle_id,
            bot_id=self.bot_id,
            pending_limit=True,
            cancel_at_ts=cancel_at,
            seconds_to_expiry=(
                minutes_left * 60.0 if minutes_left is not None else None
            ),
            structure_chart_path=(
                ctx.htf.structure_chart_path if ctx.htf else None
            ),
            entry_chart_path=(ctx.htf.entry_chart_path if ctx.htf else None),
        )
        return sug
