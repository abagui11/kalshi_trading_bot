"""EVA streak bot — Dan's streak-reversal signal, mid entry (2026-09-08).

Signal reverse-engineered from Dan's manual session and validated on 60 days
of Coinbase candles (``backtest/dan_rules_study.py``, ``dan_rules_pricing.py``,
``eva_streak_bt.py``):

* Signal — k >= 3 consecutive same-direction 15m candles immediately before
  the current window, with the last candle *sweeping* the prior candle's
  extreme (Dan: "it didn't sweep the previous low yet... now it has, so I
  feel even more confident about the next UP trade").
* Entry — take the reversal side at the mid, near the window open. The
  original resting-limit-at-35¢ variant backtested badly (adverse selection:
  it only fills when the streak keeps running); paying the open mid keeps
  every signal and was the profitable variant, so that is what runs.
* Exits — cash out both ways: TP when the side reaches the configured
  multiple, SL when it decays to the configured fraction, else settle.
  When this bot is live (``bot_config.bot_is_live``) exits execute on the
  exchange fill-or-kill before touching the ledger, mirroring eva_wick.
* Break — after consecutive stop-outs, pause entries ("until it either sucks
  so much it hits the SL trigger and takes a break").
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import bot_config
import kalshi_finalize
import kalshi_triggers
import paper
import research
from models import KalshiSuggestion
from strategies.context import SharedCycleContext

logger = logging.getLogger(__name__)

_DONE_META_KEY = "eva_streak_done"
_SL_REASON = "eva_streak_sl"
_TP_REASON = "eva_streak_tp"


def _parse_ts(ts: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resample_15m(m5: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate M5 bars into :00/:15/:30/:45-aligned 15m candles (complete only)."""
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for b in m5:
        ts = _parse_ts(b.get("ts"))
        if ts is None:
            continue
        key = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        buckets.setdefault(key, []).append((ts, b))
    out: list[dict[str, Any]] = []
    for key in sorted(buckets):
        bars = [b for _, b in sorted(buckets[key], key=lambda p: p[0])]
        if len(bars) < 3:  # incomplete 15m bucket
            continue
        try:
            out.append(
                {
                    "ts": key,
                    "open": float(bars[0]["open"]),
                    "high": max(float(x["high"]) for x in bars),
                    "low": min(float(x["low"]) for x in bars),
                    "close": float(bars[-1]["close"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _direction(c: dict[str, Any]) -> int:
    if c["close"] > c["open"]:
        return 1
    if c["close"] < c["open"]:
        return -1
    return 0


def detect_streak(
    candles: list[dict[str, Any]], window_open: datetime
) -> tuple[int, int, bool]:
    """(run_length, direction, swept) for candles strictly before window_open.

    Direction: +1 = run of up candles, -1 = run of down candles, 0 = none.
    ``swept``: the last run candle took out the prior candle's extreme.
    """
    prior = [c for c in candles if c["ts"] < window_open]
    # Candles must be contiguous 15m steps ending exactly one slot before open.
    if not prior or prior[-1]["ts"] != window_open - timedelta(minutes=15):
        return 0, 0, False
    run = 0
    d = 0
    for c in reversed(prior[-int(bot_config.EVA_STREAK_MAX_LOOKBACK) :]):
        dd = _direction(c)
        if dd == 0:
            break
        if d == 0:
            d = dd
        if dd != d:
            break
        run += 1
    if run < 1 or len(prior) < run + 1:
        return run, d, False
    last, before = prior[-1], prior[-2]
    swept = (
        last["low"] < before["low"] if d == -1 else last["high"] > before["high"]
    )
    return run, d, swept


class EvaStreakStrategy:
    bot_id = "eva_streak"
    display_name = "EVA reversal"
    needs_htf_bias = False

    # ---------------------------------------------------------------- decide
    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        mid = ctx.yes_mid_cents
        if mid is None:
            return None

        # 1) Manage the open position every tick (TP up / SL down).
        if self._manage_open(ctx):
            return None
        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return None

        # One entry per window, taken or not.
        arm = paper.get_window_arm(self.bot_id, ctx.market_ticker)
        if arm and self._arm_meta(arm).get(_DONE_META_KEY):
            return None

        # Mid entry happens at the open mid — only decide near the offset,
        # exactly the price the backtest paid. Off-offset ticks manage only.
        if not ctx.near_decision:
            return None
        if kalshi_triggers.in_last_minutes(ctx.expiry_ts, now=ctx.clock()):
            return None
        minutes_left = kalshi_triggers.minutes_to_expiry(
            ctx.expiry_ts, now=ctx.clock()
        )
        if minutes_left is None:
            return None

        expiry = _parse_ts(ctx.expiry_ts)
        if expiry is None:
            return None
        window_open = expiry - timedelta(minutes=15)

        # 2) Streak state from fresh M5 (ctx.m5_bars is only ~20 bars).
        try:
            m5 = research.get_ohlc(
                "M5",
                limit=int(bot_config.EVA_STREAK_MAX_LOOKBACK) * 3 + 12,
                product_id=ctx.coinbase,
            )
        except Exception:
            logger.exception("eva_streak: M5 fetch failed for %s", ctx.coinbase)
            return None
        candles = resample_15m(m5)
        run, d, swept = detect_streak(candles, window_open)
        if d == 0 or run < int(bot_config.EVA_STREAK_MIN_RUN):
            return None
        if bool(bot_config.EVA_STREAK_REQUIRE_SWEEP) and not swept:
            return self._skip_near(
                ctx,
                f"eva_streak: {run} straight {'down' if d < 0 else 'up'} candles "
                "but no sweep of the prior extreme yet — waiting for the stop-run",
                ["streak_no_sweep"],
                run,
                d,
            )

        # 3) Cooldown after consecutive stop-outs ("takes a break").
        if self._in_cooldown(ctx):
            return self._skip_near(
                ctx,
                "eva_streak: cooling down after consecutive stop-outs",
                ["streak_cooldown"],
                run,
                d,
            )

        # 4) Reversal side taken at the mid.
        side = "YES" if d == -1 else "NO"
        side_mid = kalshi_triggers.side_mid_cents(side, float(mid))
        if side_mid < float(bot_config.EVA_STREAK_MIN_SIDE_MID):
            return self._skip_near(
                ctx,
                f"eva_streak: {side} already {side_mid:.0f}¢ — a longshot here "
                "means trend continuation, not a wick",
                ["streak_too_cheap"],
                run,
                d,
            )

        import kalshi_sizing

        contracts, _ = kalshi_sizing.contracts_for_entry(side_mid)
        contracts = kalshi_sizing.clamp_contracts(contracts, side_mid)
        if contracts < 1:
            return None

        label = bot_config.product_label(ctx.coinbase)
        dir_word = "down" if d == -1 else "up"
        rev_word = "UP" if side == "YES" else "DOWN"
        rationale = (
            f"{label} printed {run} straight {dir_word} 15m candles and the last "
            f"one swept the prior candle's {'low' if d == -1 else 'high'} — the "
            f"stop-run that usually ends a move. Taking {side} ({rev_word}) at "
            f"the {side_mid:.0f}¢ mid right at the open: the backtest says the "
            f"edge is in the signal, not in haggling for a fill."
        )

        sug = KalshiSuggestion(
            series=ctx.series,
            market_ticker=ctx.market_ticker,
            side=side,
            contracts=contracts,
            entry_cents=float(side_mid),
            expiry_ts=ctx.expiry_ts,
            rationale=rationale,
            product_id=ctx.product_id,
            fair_yes_cents=ctx.fair_yes_cents,
            mid_cents=float(mid),
            edge_cents=ctx.edge_cents,
            spot=ctx.spot,
            strike=ctx.strike,
            spot_vs_strike_pct=ctx.spot_vs_strike_pct,
            tau_sec=ctx.tau_sec,
            sigma=ctx.sigma,
            prior_5m_ret=ctx.prior_5m_ret,
            prior_15m_ret=ctx.prior_15m_ret,
            prior_1h_ret=ctx.prior_1h_ret,
            trigger_type="eva_streak",
            trigger_name="streak_reversal",
            setup_tags=[
                "eva_streak",
                f"run{run}",
                "sweep" if swept else "no_sweep",
                f"fade_{dir_word}_run",
            ],
            cycle_id=ctx.cycle_id,
            bot_id=self.bot_id,
            seconds_to_expiry=minutes_left * 60.0,
        )
        paper.set_window_arm(
            bot_id=self.bot_id,
            market_ticker=ctx.market_ticker,
            armed_side=side,
            arm_yes_mid=float(mid),
            arm_side_mid=side_mid,
            arm_spot=ctx.spot,
            arm_strike=ctx.strike,
            ict_bias="bull" if side == "YES" else "bear",
            htf_bias="bull" if side == "YES" else "bear",
            meta={
                _DONE_META_KEY: 1,
                "run": run,
                "swept": bool(swept),
                "cycle_id": ctx.cycle_id,
            },
        )
        return sug

    # ------------------------------------------------------------- open mgmt
    def _manage_open(self, ctx: SharedCycleContext) -> bool:
        """TP when the side hits the multiple; SL when it decays to the fraction.

        When the bot is live the exit executes on the exchange first (buy the
        opposite side fill-or-kill so Kalshi nets the position) and the ledger
        is only flattened at the real fill — same discipline as eva_wick. If
        the FOK doesn't fill, the position stays open and we retry next tick.
        """
        mid = ctx.yes_mid_cents
        if mid is None:
            return False
        try:
            positions = paper.get_open_positions(bot_id=self.bot_id)
        except Exception:
            logger.exception("eva_streak: open-position lookup failed")
            return False
        for pos in positions:
            if str(pos.get("market_ticker")) != ctx.market_ticker:
                continue
            entry = float(pos.get("entry_cents") or 0)
            if entry <= 0:
                continue
            side = str(pos.get("side") or "").upper()
            contracts = int(pos.get("contracts") or 0)
            side_now = kalshi_triggers.side_mid_cents(side, float(mid))
            tp_at = entry * float(bot_config.EVA_STREAK_TP_MULTIPLE)
            sl_at = entry * float(bot_config.EVA_STREAK_SL_FRACTION)
            reason = None
            if side_now >= tp_at:
                reason = _TP_REASON
            elif side_now <= sl_at:
                reason = _SL_REASON
            if reason is None:
                continue
            exit_cents = self._execute_live_exit(
                ctx, pos_side=side, contracts=contracts, side_now=side_now
            )
            if exit_cents is None:
                # Live exit unfilled/rejected — keep position, retry next tick.
                return False
            closed = paper.flatten_position_early(
                int(pos["id"]), exit_side_cents=exit_cents, reason=reason
            )
            if closed:
                self._notify_exit(ctx, pos, entry, exit_cents, reason)
                return True
        return False

    def _execute_live_exit(
        self,
        ctx: SharedCycleContext,
        *,
        pos_side: str,
        contracts: int,
        side_now: float,
    ) -> float | None:
        """Exit on the exchange by buying the opposite side (Kalshi nets).

        Returns the realized exit in *our side's* cents, or None when the
        exit did not fully fill. Paper-routed bots get the mid mark back.
        """
        import kalshi_client

        opp = "NO" if pos_side == "YES" else "YES"
        opp_mid = max(1.0, min(99.0, 100.0 - side_now))
        try:
            resp = kalshi_client.place_order(
                ctx.market_ticker,
                opp,
                contracts,
                yes_price_cents=int(round(opp_mid)),
                time_in_force="fill_or_kill",
                closing=True,
                paper=not bot_config.bot_is_live(self.bot_id),
            )
        except Exception:
            logger.exception(
                "eva_streak: live exit failed for %s — leaving position open",
                ctx.market_ticker,
            )
            return None

        status = str((resp or {}).get("status") or "")
        if status == "paper_only":
            return side_now
        if not isinstance(resp, dict) or resp.get("error") or status == "rejected":
            logger.warning("eva_streak: exit order error: %s", resp)
            return None
        filled = kalshi_client.filled_contract_count(resp)
        if filled < contracts:
            logger.info(
                "eva_streak: exit FOK unfilled (%s/%s) on %s — retry next tick",
                filled,
                contracts,
                ctx.market_ticker,
            )
            return None
        return kalshi_client.side_fill_cents_from_response(
            pos_side,
            resp,
            fallback_side_cents=side_now,
        )

    def _in_cooldown(self, ctx: SharedCycleContext) -> bool:
        need = int(bot_config.EVA_STREAK_COOLDOWN_LOSSES)
        if need < 1:
            return False
        try:
            recent = paper.get_closed_positions(limit=need, bot_id=self.bot_id)
        except Exception:
            logger.exception("eva_streak: closed-position lookup failed")
            return False
        if len(recent) < need:
            return False
        cutoff = ctx.clock() - timedelta(
            minutes=float(bot_config.EVA_STREAK_COOLDOWN_MINUTES)
        )
        for pos in recent:
            if _SL_REASON not in str(pos.get("rationale") or ""):
                return False
            closed_at = _parse_ts(pos.get("closed_at"))
            if closed_at is None or closed_at < cutoff:
                return False
        return True

    # -------------------------------------------------------------- helpers
    @staticmethod
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

    def _skip_near(
        self,
        ctx: SharedCycleContext,
        rationale: str,
        skip_codes: list[str],
        run: int,
        d: int,
    ) -> KalshiSuggestion | None:
        """Log a skip only near the decision offset to keep the ledger light."""
        if not ctx.near_decision:
            return None
        return kalshi_finalize.make_skip(
            rationale=rationale,
            base=ctx.with_bot(self.bot_id),
            skip_codes=skip_codes,
            setup_tags=["eva_streak", f"run{run}", "down" if d < 0 else "up"],
            trigger_type="eva_streak",
            trigger_name="streak_reversal",
        )

    @staticmethod
    def _notify_exit(
        ctx: SharedCycleContext,
        pos: dict[str, Any],
        entry: float,
        exit_c: float,
        reason: str,
    ) -> None:
        try:
            import notify

            pnl = (exit_c - entry) / 100.0 * float(pos.get("contracts") or 0)
            if reason == _TP_REASON:
                head = "💰 [eva_streak] Take-profit"
                tail = "Reversal played out — cashing the wick, not sweating the settle."
            else:
                head = "✂️ [eva_streak] Stop-loss"
                tail = "Cut at half — never ride a broken reversal to zero."
            notify.broadcast_plain_text(
                f"{head} {ctx.market_ticker}: {pos.get('side')} "
                f"×{pos.get('contracts')} {entry:.0f}¢ → {exit_c:.0f}¢ "
                f"({'+' if pnl >= 0 else ''}${pnl:.2f}). {tail}"
            )
        except Exception:
            logger.exception("eva_streak: exit notify failed")
