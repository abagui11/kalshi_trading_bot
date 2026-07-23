"""Adverse / wick-hunt bot — enter after move against shared HTF bias."""

from __future__ import annotations

import bot_config
import kalshi_finalize
import kalshi_triggers
import paper
from models import KalshiSuggestion
from strategies.context import SharedCycleContext


class AdverseStrategy:
    bot_id = "adverse"
    display_name = "Adverse / wick-hunt"
    needs_htf_bias = True

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        base = ctx.with_bot(self.bot_id)
        mid = ctx.yes_mid_cents
        if mid is None:
            return None

        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return None

        # Last-3m block (strong-signal override stubbed off).
        if kalshi_triggers.in_last_minutes(ctx.expiry_ts):
            if not bot_config.STRONG_SIGNAL_OVERRIDE:
                # Still allow if already armed and cheap enough? Plan says respect block.
                return None

        htf = ctx.htf
        side = htf.side if htf else None
        if side is None and htf:
            # ICT may say no_trade while HTF bias is still clear — arm from HTF.
            if htf.htf_bias == "bear":
                side = "NO"
            elif htf.htf_bias == "bull":
                side = "YES"
        if side is None:
            arm = paper.get_window_arm(self.bot_id, ctx.market_ticker)
            if arm and arm.get("armed_side") in ("YES", "NO"):
                side = str(arm["armed_side"])
        if side is None:
            shared = paper.get_shared_htf_bias(ctx.market_ticker)
            if shared and shared.get("side") in ("YES", "NO"):
                side = str(shared["side"])
            elif shared and shared.get("htf_bias") == "bear":
                side = "NO"
            elif shared and shared.get("htf_bias") == "bull":
                side = "YES"
            elif ctx.near_decision:
                return kalshi_finalize.make_skip(
                    rationale="adverse: no directional bias to arm wick-hunt",
                    base=base,
                    htf_bias=(htf.htf_bias if htf else "unknown"),
                    skip_codes=["adverse_no_bias"],
                    setup_tags=["adverse"],
                    trigger_type="adverse",
                    trigger_name="wick_hunt",
                )
            else:
                return None

        assert side in ("YES", "NO")
        yes_mid = float(mid)
        side_mid = kalshi_triggers.side_mid_cents(side, yes_mid)
        htf_bias = htf.htf_bias if htf else "unknown"
        ict_bias = htf.ict_bias if htf else htf_bias
        ict_rationale = htf.ict_rationale if htf else ""

        arm = paper.get_window_arm(self.bot_id, ctx.market_ticker)
        if arm is None:
            # Arm only when we have a fresh bias (typically near decision).
            if not ctx.near_decision and htf is None:
                return None
            # Do not arm into an already-cheap coinflip-avoiding fill — wait for adverse.
            paper.set_window_arm(
                bot_id=self.bot_id,
                market_ticker=ctx.market_ticker,
                armed_side=side,
                arm_yes_mid=yes_mid,
                arm_side_mid=side_mid,
                arm_spot=ctx.spot,
                arm_strike=ctx.strike,
                ict_bias=ict_bias,
                htf_bias=htf_bias,
                meta={"cycle_id": ctx.cycle_id},
            )
            return kalshi_finalize.make_skip(
                rationale=(
                    f"adverse armed: bias {side} (htf={htf_bias}) at YES mid "
                    f"{yes_mid:.1f}¢ / side mid {side_mid:.1f}¢. "
                    "Waiting for adverse excursion (wick) before entry — "
                    "avoid ~50¢ coinflip."
                ),
                base=base,
                htf_bias=htf_bias,
                setup_tags=["adverse", "armed"],
                skip_codes=["adverse_armed"],
                structure_chart_path=(htf.structure_chart_path if htf else None),
                entry_chart_path=(htf.entry_chart_path if htf else None),
                ict_action=(htf.ict_action if htf else None),
                ict_bias=ict_bias,
                trigger_type="adverse",
                trigger_name="wick_hunt_arm",
            )

        armed_side = str(arm.get("armed_side") or side)
        arm_side_mid = arm.get("arm_side_mid")
        arm_spot = arm.get("arm_spot")
        arm_strike = arm.get("arm_strike") or ctx.strike
        arm_yes = arm.get("arm_yes_mid")

        # Adverse excursion: price moved against bias through strike / mid worsened.
        adverse_ok = False
        excursion_pct: float | None = None
        mid_improvement: float | None = None

        if ctx.spot is not None and arm_strike and float(arm_strike) > 0:
            through = (float(ctx.spot) / float(arm_strike) - 1.0) * 100.0
            if armed_side == "NO":
                # Bias down: want spike above strike (positive through).
                excursion_pct = through
                adverse_ok = through >= float(bot_config.ADVERSE_MIN_EXCURSION_PCT)
            else:
                # Bias up: want dip below strike.
                excursion_pct = -through
                adverse_ok = (-through) >= float(bot_config.ADVERSE_MIN_EXCURSION_PCT)

        if arm_side_mid is not None:
            mid_improvement = float(arm_side_mid) - side_mid
            if mid_improvement >= float(bot_config.ADVERSE_MIN_MID_IMPROVEMENT_CENTS):
                adverse_ok = True

        # Also treat YES mid rising (for NO bias) / falling (for YES) as adverse.
        if arm_yes is not None:
            if armed_side == "NO" and yes_mid > float(arm_yes) + 1.0:
                adverse_ok = True
            if armed_side == "YES" and yes_mid < float(arm_yes) - 1.0:
                adverse_ok = True

        if not adverse_ok:
            return None  # stay quiet while waiting

        if side_mid > float(bot_config.ADVERSE_MAX_ENTRY_CENTS):
            return kalshi_finalize.make_skip(
                rationale=(
                    f"adverse excursion seen but side mid {side_mid:.1f}¢ still "
                    f"> {bot_config.ADVERSE_MAX_ENTRY_CENTS:.0f}¢ max entry"
                ),
                base=base,
                htf_bias=str(arm.get("htf_bias") or htf_bias),
                setup_tags=["adverse", "excursion_rich"],
                skip_codes=["adverse_still_rich"],
                trigger_type="adverse",
                trigger_name="wick_hunt",
            )

        # Avoid buying near 50¢ even after excursion.
        if 45.0 <= side_mid <= 55.0:
            return kalshi_finalize.make_skip(
                rationale=(
                    f"adverse: excursion seen but side mid {side_mid:.1f}¢ still "
                    "near coinflip — wait for cheaper wick"
                ),
                base=base,
                htf_bias=str(arm.get("htf_bias") or htf_bias),
                setup_tags=["adverse", "still_coinflip"],
                skip_codes=["adverse_coinflip"],
                trigger_type="adverse",
                trigger_name="wick_hunt",
            )

        sug = kalshi_finalize.finalize_directional(
            side=armed_side,
            trigger_reason=(
                f"wick-hunt after adverse excursion "
                f"(excursion_pct={excursion_pct}, mid_improvement={mid_improvement}, "
                f"arm_side_mid={arm_side_mid}, now_side_mid={side_mid:.1f}, "
                f"arm_spot={arm_spot}, spot={ctx.spot})"
            ),
            trigger_type="adverse",
            base=base,
            mid=yes_mid,
            fair_cents=ctx.fair_yes_cents,
            edge=ctx.edge_cents,
            expiry_s=ctx.expiry_ts,
            htf_bias=str(arm.get("htf_bias") or htf_bias),
            ict_action=(htf.ict_action if htf else None),
            ict_bias=str(arm.get("ict_bias") or ict_bias),
            ict_rationale=ict_rationale or "shared HTF bias",
            gate_outcome=(htf.gate_outcome if htf else "adverse_excursion"),
            setup_tags=["adverse", "wick_hunt"],
            lottery=False,
            structure_chart_path=(htf.structure_chart_path if htf else None),
            entry_chart_path=(htf.entry_chart_path if htf else None),
            trigger_name="wick_hunt",
        )
        sug.bot_id = self.bot_id
        if sug.is_trade():
            # Clear arm so we don't double-enter; open handled by apply_and_log.
            paper.clear_window_arm(self.bot_id, ctx.market_ticker)
        return sug
