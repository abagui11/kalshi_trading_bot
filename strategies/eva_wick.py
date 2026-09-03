"""EVA wick bot — fade pops / buy overshoots against EVA brain stances.

Zero-Claude strategy: direction comes from the hub's persisted H4/H1/M15
stances (``eva_intel``); entries are rule-based wick logic reverse-engineered
from the Sep 2 2026 manual session (see exports/wick_review_20260902).

Two entry patterns, both requiring the bought side to be cheap:

* ``fade_pop`` — price pops to the top (bottom) of the trailing session range
  against a bearish (bullish) EVA lean; buy the opposing side while the crowd
  prices the pop as continuation.
* ``buy_overshoot`` — a flush into a session-range edge with EVA's M15 stance
  already flipped against the move (oversold-reversion); buy the bounce side.

Boss rules are soft gates (reduced size + journal tag) except the hard caps:
entry above the soft price ceiling, or BTC trailing-hour move above the hard
limit, always skip.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import bot_config
import eva_intel
import kalshi_finalize
import kalshi_triggers
import paper
import research
from models import KalshiSuggestion
from strategies.context import SharedCycleContext

logger = logging.getLogger(__name__)

_DONE_META_KEY = "eva_wick_done"


def _btc_1h_move_pct(ctx: SharedCycleContext) -> float | None:
    """Net BTC % move over the trailing hour (boss rule 3 — always BTC)."""
    if ctx.coinbase == "BTC-USD" and ctx.prior_1h_ret is not None:
        return float(ctx.prior_1h_ret)
    try:
        m5 = research.get_ohlc("M5", limit=13, product_id="BTC-USD")
    except Exception:
        logger.exception("eva_wick: BTC M5 fetch failed")
        return None
    if len(m5) < 13:
        return None
    first = float(m5[-13]["close"])
    last = float(m5[-1]["close"])
    if first <= 0:
        return None
    return (last / first - 1.0) * 100.0


def _session_range(product_id: str, spot: float) -> tuple[float | None, float | None]:
    """(session_pos 0..1 over trailing ~6h, 24h range width %) from H1 bars."""
    try:
        h1 = research.get_ohlc("H1", limit=25, product_id=product_id)
    except Exception:
        logger.exception("eva_wick: H1 fetch failed for %s", product_id)
        return None, None
    if len(h1) < 7:
        return None, None
    recent = h1[-6:]
    lo = min(float(b["low"]) for b in recent)
    hi = max(float(b["high"]) for b in recent)
    lo = min(lo, spot)
    hi = max(hi, spot)
    pos = (spot - lo) / (hi - lo) if hi > lo else 0.5
    day = h1[-24:]
    d_lo = min(float(b["low"]) for b in day)
    d_hi = max(float(b["high"]) for b in day)
    width_pct = (d_hi - d_lo) / spot * 100.0 if spot > 0 else None
    return pos, width_pct


def _arm_meta(arm: dict[str, Any]) -> dict[str, Any]:
    """Parse the window-arm meta payload (stored as JSON in ``meta_json``)."""
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


def _window_quarter(expiry_ts: str | None) -> str:
    """first15 | last15 | mid — from the Kalshi window's settle minute."""
    if not expiry_ts:
        return "mid"
    try:
        dt = datetime.fromisoformat(str(expiry_ts).replace("Z", "+00:00"))
    except ValueError:
        return "mid"
    if dt.minute == 15:
        return "first15"
    if dt.minute == 0:
        return "last15"
    return "mid"


class EvaWickStrategy:
    bot_id = "eva_wick"
    display_name = "EVA wick (fade/overshoot)"
    needs_htf_bias = False  # never triggers the Claude HTF refresh

    # ---------------------------------------------------------------- decide
    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        base = ctx.with_bot(self.bot_id)
        mid = ctx.yes_mid_cents
        if mid is None:
            return None

        # 1) Manage an open position first (take-profit runs every tick).
        if self._maybe_take_profit(ctx):
            return None
        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return None

        # One entry per window (incl. after a TP exit).
        arm = paper.get_window_arm(self.bot_id, ctx.market_ticker)
        if arm and _arm_meta(arm).get(_DONE_META_KEY):
            return None

        # Respect the last-3m settlement block quietly.
        if kalshi_triggers.in_last_minutes(ctx.expiry_ts, now=ctx.clock()):
            return None

        if ctx.spot is None or ctx.strike is None or ctx.spot_vs_strike_pct is None:
            return None

        # 2) EVA stances — fail closed.
        now = ctx.now if isinstance(ctx.now, datetime) else None
        stances = eva_intel.get_stances(ctx.coinbase, now=now)
        if stances is None:
            if ctx.near_decision:
                return kalshi_finalize.make_skip(
                    rationale="eva_wick: EVA stances unavailable/stale — fail closed, no trade",
                    base=base,
                    skip_codes=["eva_stale"],
                    setup_tags=["eva_wick"],
                    trigger_type="eva_wick",
                    trigger_name="no_bias",
                )
            return None

        h4, h1, m15 = stances["H4"], stances["H1"], stances["M15"]
        size_factor = 1.0
        tags: list[str] = ["eva_wick"]

        # 3) BTC trailing-hour move gate (hard above HARD, soft above SOFT).
        btc_move = _btc_1h_move_pct(ctx)
        if btc_move is not None:
            move = abs(btc_move)
            if move > float(bot_config.EVA_WICK_BTC_MOVE_HARD_PCT):
                if ctx.near_decision:
                    return kalshi_finalize.make_skip(
                        rationale=(
                            f"eva_wick: BTC moved {btc_move:+.2f}% in the past hour "
                            f"(hard limit {bot_config.EVA_WICK_BTC_MOVE_HARD_PCT}%) — "
                            "expansion hour, wick fades untrustworthy"
                        ),
                        base=base,
                        skip_codes=["eva_btc_move"],
                        setup_tags=tags + ["btc_expansion"],
                        trigger_type="eva_wick",
                        trigger_name="btc_move_gate",
                    )
                return None
            if move > float(bot_config.EVA_WICK_BTC_MOVE_SOFT_PCT):
                size_factor *= float(bot_config.EVA_WICK_SOFT_FACTOR)
                tags.append("soft_btc_move")

        # 4) Location inside the trailing session range.
        session_pos, day_range_pct = _session_range(ctx.coinbase, float(ctx.spot))
        if session_pos is None:
            return None
        if day_range_pct is not None and day_range_pct > float(
            bot_config.EVA_WICK_MAX_DAY_RANGE_PCT
        ):
            size_factor *= 0.75
            tags.append("wide_day_range")

        # 5) Pattern selection.
        lean = h1["stance"] if h1["stance"] != "neutral" else h4["stance"]
        excursion = float(ctx.spot_vs_strike_pct)  # + == spot above strike
        min_exc = float(bot_config.EVA_WICK_MIN_EXCURSION_PCT)
        edge_lo = float(bot_config.EVA_WICK_RANGE_EDGE_LOW)
        edge_hi = float(bot_config.EVA_WICK_RANGE_EDGE_HIGH)
        min_m15 = float(bot_config.EVA_WICK_MIN_M15_CONF)

        side: str | None = None
        pattern = ""
        wick_line = ""
        if lean == "bearish" and excursion >= min_exc and session_pos >= edge_hi:
            side, pattern = "NO", "fade_pop"
            wick_line = (
                "Pop into the top of the session range against a bearish EVA lean — "
                "most candles don't close on their highs; fading the wick."
            )
        elif lean == "bullish" and excursion <= -min_exc and session_pos <= edge_lo:
            side, pattern = "YES", "fade_pop"
            wick_line = (
                "Flush into the bottom of the session range against a bullish EVA "
                "lean — most candles don't close on their lows; fading the wick."
            )
        elif (
            m15["stance"] == "bullish"
            and m15["confidence"] >= min_m15
            and excursion <= -min_exc
            and session_pos <= edge_lo
        ):
            side, pattern = "YES", "buy_overshoot"
            wick_line = (
                "Overshoot into the session low with EVA's M15 already flipped "
                "bullish — buying the oversold reversion."
            )
        elif (
            m15["stance"] == "bearish"
            and m15["confidence"] >= min_m15
            and excursion >= min_exc
            and session_pos >= edge_hi
        ):
            side, pattern = "NO", "buy_overshoot"
            wick_line = (
                "Overshoot into the session high with EVA's M15 already flipped "
                "bearish — selling the overbought reversion."
            )

        if side is None:
            if ctx.near_decision:
                return kalshi_finalize.make_skip(
                    rationale=(
                        f"eva_wick: no setup — lean={lean}, m15={m15['stance']} "
                        f"{m15['confidence']:.2f}, excursion={excursion:+.3f}%, "
                        f"session_pos={session_pos:.2f}"
                    ),
                    base=base,
                    skip_codes=["eva_no_setup"],
                    setup_tags=tags,
                    trigger_type="eva_wick",
                    trigger_name="no_setup",
                )
            return None

        # 6) Price gates on the side we buy (boss rule 1, soft band above 33c).
        side_mid = kalshi_triggers.side_mid_cents(side, float(mid))
        soft_max = float(bot_config.EVA_WICK_SOFT_MAX_ENTRY_CENTS)
        hard_max = float(bot_config.EVA_WICK_MAX_ENTRY_CENTS)
        min_entry = float(bot_config.EVA_WICK_MIN_ENTRY_CENTS)
        if side_mid > soft_max:
            return kalshi_finalize.make_skip(
                rationale=(
                    f"eva_wick {pattern}: setup present but {side} at "
                    f"{side_mid:.1f}¢ > {soft_max:.0f}¢ ceiling — no edge in "
                    "paying up for a wick"
                ),
                base=base,
                skip_codes=["eva_rich"],
                setup_tags=tags + [pattern, "rich"],
                trigger_type="eva_wick",
                trigger_name=pattern,
            )
        if side_mid < min_entry:
            return kalshi_finalize.make_skip(
                rationale=(
                    f"eva_wick {pattern}: {side} at {side_mid:.1f}¢ < "
                    f"{min_entry:.0f}¢ — lottery-cheap usually means trend, not wick"
                ),
                base=base,
                skip_codes=["eva_too_cheap"],
                setup_tags=tags + [pattern, "too_cheap"],
                trigger_type="eva_wick",
                trigger_name=pattern,
            )
        if side_mid > hard_max:
            size_factor *= float(bot_config.EVA_WICK_SOFT_FACTOR)
            tags.append("soft_rich")

        # 7) Hour-quarter gate (boss rule 2, soft) + last-15 priority.
        quarter = _window_quarter(ctx.expiry_ts)
        if quarter == "mid":
            size_factor *= float(bot_config.EVA_WICK_SOFT_FACTOR)
            tags.append("soft_mid_hour")
        elif quarter == "last15":
            size_factor *= float(bot_config.EVA_WICK_PRIORITY_BOOST)
            tags.append("last15_priority")

        # 8) Alignment boost: confident M15 agreeing with the side.
        m15_agrees = (side == "YES" and m15["stance"] == "bullish") or (
            side == "NO" and m15["stance"] == "bearish"
        )
        if m15_agrees and m15["confidence"] >= float(
            bot_config.EVA_WICK_STRONG_M15_CONF
        ):
            size_factor *= float(bot_config.EVA_WICK_PRIORITY_BOOST)
            tags.append("m15_conviction")

        deploy = float(bot_config.KALSHI_DEPLOY_PCT) * size_factor
        deploy = max(0.005, min(deploy, float(bot_config.KALSHI_MAX_DEPLOY_PCT)))

        # 9) Broadcast rationale — location / EVA lean / mispricing / wick logic.
        implied_against = 100.0 - side_mid
        rationale = (
            f"{bot_config.product_label(ctx.coinbase)} at {session_pos:.0%} of its "
            f"session range, {excursion:+.2f}% through the strike. "
            f"EVA: H4 {h4['stance']} {h4['confidence']:.2f} / H1 {h1['stance']} "
            f"{h1['confidence']:.2f} / M15 {m15['stance']} {m15['confidence']:.2f}. "
            f"Market pricing {implied_against:.0f}% against that lean — "
            f"{side} on sale at {side_mid:.0f}¢. {wick_line}"
        )

        htf_bias = {"bullish": "bull", "bearish": "bear"}.get(lean, "mixed")
        sug = kalshi_finalize.finalize_directional(
            side=side,
            trigger_reason=(
                f"{pattern}: session_pos={session_pos:.2f}, "
                f"excursion={excursion:+.3f}%, quarter={quarter}, "
                f"btc_1h={btc_move if btc_move is None else round(btc_move, 3)}%"
            ),
            trigger_type="eva_wick",
            base=base,
            mid=float(mid),
            fair_cents=ctx.fair_yes_cents,
            edge=ctx.edge_cents,
            expiry_s=ctx.expiry_ts,
            htf_bias=htf_bias,
            ict_bias=htf_bias,
            ict_rationale=rationale,
            gate_outcome=pattern,
            setup_tags=tags + [pattern],
            require_edge=False,
            deploy_pct=deploy,
            trigger_name=pattern,
        )
        sug.bot_id = self.bot_id
        if sug.is_trade():
            # Broadcast chart: reconstructed H1/M15/M5 with order blocks and
            # the EVA stances in the header (structure slot -> sent first).
            try:
                import eva_charts

                sug.structure_chart_path = eva_charts.build_eva_entry_chart(
                    product_id=ctx.product_id,
                    coinbase=ctx.coinbase,
                    side=side,
                    entry_side_cents=side_mid,
                    strike=float(ctx.strike),
                    expiry_ts=str(ctx.expiry_ts),
                    stances=stances,
                    pattern=pattern,
                    wick_line=wick_line,
                    session_pos=session_pos,
                    btc_move=btc_move,
                )
            except Exception:
                logger.exception("eva_wick: entry chart build failed")
            paper.set_window_arm(
                bot_id=self.bot_id,
                market_ticker=ctx.market_ticker,
                armed_side=side,
                arm_yes_mid=float(mid),
                arm_side_mid=side_mid,
                arm_spot=ctx.spot,
                arm_strike=ctx.strike,
                ict_bias=htf_bias,
                htf_bias=htf_bias,
                meta={_DONE_META_KEY: 1, "pattern": pattern, "cycle_id": ctx.cycle_id},
            )
        return sug

    # ---------------------------------------------------------- take-profit
    def _maybe_take_profit(self, ctx: SharedCycleContext) -> bool:
        """Flatten an open position early when the side roughly doubles."""
        mid = ctx.yes_mid_cents
        if mid is None:
            return False
        try:
            positions = paper.get_open_positions(bot_id=self.bot_id)
        except Exception:
            logger.exception("eva_wick: open-position lookup failed")
            return False
        for pos in positions:
            if str(pos.get("market_ticker")) != ctx.market_ticker:
                continue
            entry = float(pos.get("entry_cents") or 0)
            if entry <= 0:
                continue
            side_now = kalshi_triggers.side_mid_cents(
                str(pos.get("side")), float(mid)
            )
            target = entry * float(bot_config.EVA_WICK_TP_MULTIPLE)
            if side_now < target:
                continue
            closed = paper.flatten_position_early(
                int(pos["id"]),
                exit_side_cents=side_now,
                reason="eva_wick_tp",
            )
            if closed:
                logger.info(
                    "eva_wick TP: %s %s %s @ %.1f¢ -> %.1f¢",
                    ctx.market_ticker,
                    pos.get("side"),
                    pos.get("contracts"),
                    entry,
                    side_now,
                )
                self._notify_tp(ctx, pos, entry, side_now)
                return True
        return False

    @staticmethod
    def _notify_tp(
        ctx: SharedCycleContext, pos: dict[str, Any], entry: float, exit_c: float
    ) -> None:
        try:
            import notify

            pnl = (exit_c - entry) / 100.0 * float(pos.get("contracts") or 0)
            notify.broadcast_plain_text(
                f"💰 [eva_wick] Take-profit {ctx.market_ticker}: "
                f"{pos.get('side')} ×{pos.get('contracts')} "
                f"{entry:.0f}¢ → {exit_c:.0f}¢ (+${pnl:.2f}). "
                f"Wick played out — not waiting on settlement risk."
            )
        except Exception:
            logger.exception("eva_wick: TP notify failed")
