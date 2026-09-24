"""Regime gates for the directional Kalshi books (PLAN_20260921_ROUND2 wB).

Evidence, from the recorded books (trade_ideas/analysis/_q0921_kalshi_*.py):

* Weekend: across each bot's ENTIRE history, eva_streak runs −0.559/trade on
  Sat+Sun vs +0.099 weekday (n=61/145) and eva_wick −0.226 vs +0.375
  (n=289/266). eva_arb is positive in both and is deliberately not gated.
* Loss cooldown: helps eva_streak (positive in 11 of 12 swept cells, driven by
  a 10-loss run) and HURTS eva_wick (negative in 9 of 12 — its losses are
  regime-priced, not clustered; skipping after losses skips winners). So the
  cooldown applies to eva_streak ONLY. The k=3 / 4h cell matches the mill's
  shipped precedent (LIVE_MILL_LOSS_COOLDOWN_N=3, 240 min) and sits on the
  sweep's plateau, not at its maximum.
* Chop: daily P&L correlates with range/|net| at −0.4 for both directional
  books. A chop THRESHOLD tuned on one weekend would be noise-fitting, so chop
  ships as a SHADOW measurement only: computed and logged at each gated
  decision, threshold chosen from the forward shadow log, never from the
  weekend that motivated it.

* Daily loss stop (2026-09-22, live books only): once a live bot's realized
  P&L for the UTC day is <= -KALSHI_DAILY_STOP_PCT x the day's sizing
  bankroll (25% ~ $50 on a $204 shard), it opens nothing until 00:00Z.
  Insurance, not edge: replaying eva_wick's 723 trades, every stop level
  from $10 to $100 was noise or a cost (tight stops skip the same-day
  recovery), and on weekdays nothing at 15%+ ever fired (worst weekday
  intraday -$27). 25% was chosen because it has never bound on the record,
  so it bounds a tail the record has not shown without taxing the edge.
  Paper books are exempt so the experiments stay unfiltered.

* Weekend watch, eva_wick (2026-09-24, operator decision): the blanket
  weekend pause is replaced for THIS BOT ONLY by a statistical tripwire, so
  the book can collect weekend evidence instead of never having any. The
  wick's weekend record is ONE weekend (09-19/20, n=289, -$0.226/trade,
  recorded before any gate) against a weekday baseline of ~590 trades at
  +$0.359 — far too little to call the regime effect proven, which is the
  point of trading it with a breaker instead of assuming either way.
  Sequentially, at each settled weekend trade k >= WICK_WW_MIN_N:
      p_k = Phi( (S_k - k*mu_weekday) / (sd_weekday * sqrt(k)) )
  and the bot stops opening until Monday 00:00Z when p_k < WICK_WW_ALPHA.
  Calibration (trade_ideas/analysis/scripts/_q0924_weekend_watch_cal.py,
  4000 sims/cell, sweep alpha x min_n, all cells monotone): the shipped cell
  (0.01, 20) false-trips a weekday-like weekend 9.7% of the time (median
  false trip at -$38), trips a weekend that behaves like the recorded one
  85.5% of the time (median trade 84, -$40), and replayed on the actual
  recorded weekend trips at trade 65 (-$32), saving the remaining -$33.
  NOTE: alpha is the per-check p-value, NOT the weekend false-trip rate —
  checking after every settle inflates it; ~10% per weekend is the measured
  number. The trip is persisted (regime_state), so a restart cannot untrip.
  eva_streak stays on the blanket pause: its weekend record is losing AND
  its cooldown sweep already says pausing it after losses helps.

Every gate fails open: a broken clock or a failed query must never stop the
book silently — gating is a risk reduction, not a dependency.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bot_config
import paper
import research

logger = logging.getLogger(__name__)

# Defaults live here (not bot_config) so this module lands without touching
# files that carry unrelated uncommitted work; bot_config can override.
# 2026-09-24: eva_wick left the blanket pause for the statistical weekend
# watch below; eva_streak stays hard-paused.
WEEKEND_PAUSE_BOTS: tuple[str, ...] = tuple(
    getattr(bot_config, "KALSHI_WEEKEND_PAUSE_BOTS", ("eva_streak",))
)
WICK_WW_ALPHA: float = float(
    getattr(bot_config, "EVA_WICK_WEEKEND_WATCH_ALPHA", 0.01)
)
WICK_WW_MIN_N: int = int(getattr(bot_config, "EVA_WICK_WEEKEND_WATCH_MIN_N", 20))
WICK_WW_EPOCH = "2026-09-17"          # wick redefinition; baseline starts here
WICK_WW_MIN_BASELINE = 200            # fewer weekday closes than this -> fail open
STREAK_COOLDOWN_N: int = int(getattr(bot_config, "EVA_STREAK_LOSS_COOLDOWN_N", 3))
STREAK_COOLDOWN_MIN: int = int(
    getattr(bot_config, "EVA_STREAK_LOSS_COOLDOWN_MIN", 240)
)
DAILY_STOP_PCT: float = float(
    os.getenv("KALSHI_DAILY_STOP_PCT")
    or getattr(bot_config, "KALSHI_DAILY_STOP_PCT", 0.25)
)

_day_bankroll: dict[str, float] = {}


def _bankroll_for_day(day: str) -> float:
    """Sizing bankroll snapshotted at the first check of the UTC day.

    The shard balance excludes the cost of open positions, so re-reading it
    mid-day would shrink the stop exactly while exposure is on.
    """
    if day not in _day_bankroll:
        import kalshi_sizing

        _day_bankroll.clear()
        _day_bankroll[day] = float(kalshi_sizing.sizing_bankroll_usd())
    return _day_bankroll[day]


def daily_loss_stop(bot_id: str, now: datetime | None = None) -> str | None:
    """Stop reason for a live bot that has lost its daily limit, else None."""
    try:
        if DAILY_STOP_PCT <= 0 or not bot_config.bot_is_live(bot_id):
            return None
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        with paper._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_positions
                WHERE bot_id = ? AND pnl_usd IS NOT NULL
                  AND closed_at >= ?
                """,
                (bot_id, f"{day}T00:00:00"),
            ).fetchone()
        realized = float(row[0] or 0.0)
        if realized >= 0:
            return None
        bankroll = _bankroll_for_day(day)
        limit = DAILY_STOP_PCT * bankroll
        if bankroll > 0 and realized <= -limit:
            return (
                f"daily_loss_stop: {bot_id} realized ${realized:.2f} today, "
                f"limit -${limit:.2f} ({DAILY_STOP_PCT:.0%} of ${bankroll:.2f}); "
                "no entries until 00:00Z"
            )
        return None
    except Exception:
        logger.exception("daily_loss_stop failed — failing open")
        return None


def weekend_pause(bot_id: str, now: datetime | None = None) -> bool:
    """True when this bot should not OPEN a new position (Sat/Sun UTC).

    Settlement and management of already-open positions are untouched — the
    gate is on entries, because that is where the recorded weekend bleed is.
    """
    if bot_id not in WEEKEND_PAUSE_BOTS:
        return False
    try:
        now = now or datetime.now(timezone.utc)
        return now.weekday() >= 5
    except Exception:
        logger.exception("weekend_pause failed — failing open")
        return False


def streak_loss_cooldown(
    product_id: str, now: datetime | None = None
) -> str | None:
    """Cooldown reason for eva_streak on one product, or None to trade.

    After STREAK_COOLDOWN_N consecutive settled losses on this product, no new
    entries until STREAK_COOLDOWN_MIN minutes after the last loss settled.
    eva_wick must never be wired through this — the sweep says a wick cooldown
    costs money (9 of 12 cells negative).
    """
    try:
        now = now or datetime.now(timezone.utc)
        with paper._connect() as conn:
            rows = conn.execute(
                """
                SELECT pnl_usd, closed_at FROM paper_positions
                WHERE bot_id = 'eva_streak' AND product_id = ?
                  AND pnl_usd IS NOT NULL AND closed_at IS NOT NULL
                ORDER BY closed_at DESC, id DESC LIMIT ?
                """,
                (product_id, STREAK_COOLDOWN_N),
            ).fetchall()
        if len(rows) < STREAK_COOLDOWN_N:
            return None
        if any(float(r[0]) > 0 for r in rows):
            return None
        newest = str(rows[0][1]).replace("Z", "+00:00")
        settled = datetime.fromisoformat(newest)
        if settled.tzinfo is None:
            settled = settled.replace(tzinfo=timezone.utc)
        until = settled + timedelta(minutes=STREAK_COOLDOWN_MIN)
        if now < until:
            return (
                f"loss_cooldown: {STREAK_COOLDOWN_N} straight losses on "
                f"{product_id}, paused until {until.strftime('%H:%M')}Z"
            )
        return None
    except Exception:
        logger.exception("streak_loss_cooldown failed — failing open")
        return None


def _state_get(key: str) -> str | None:
    with paper._connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS regime_state "
            "(key TEXT PRIMARY KEY, value TEXT)"
        )
        row = conn.execute(
            "SELECT value FROM regime_state WHERE key = ?", (key,)
        ).fetchone()
    return str(row[0]) if row else None


def _state_set(key: str, value: str) -> None:
    with paper._connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS regime_state "
            "(key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT INTO regime_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def _norm_cdf(z: float) -> float:
    from math import erf, sqrt

    return 0.5 * (1 + erf(z / sqrt(2)))


def wick_weekend_watch(now: datetime | None = None) -> str | None:
    """Weekend tripwire for eva_wick. Reason to stand down, or None to trade.

    Active Sat/Sun UTC only. Trips when the weekend's running P&L is lower
    than random draws from the bot's own weekday distribution can plausibly
    explain (see module docstring for the calibration), then holds until
    Monday 00:00Z. The trip is persisted so a process restart cannot untrip.
    """
    try:
        now = now or datetime.now(timezone.utc)
        if now.weekday() < 5:
            return None
        saturday = (now - timedelta(days=now.weekday() - 5)).strftime("%Y-%m-%d")
        trip_key = f"wick_weekend_trip:{saturday}"
        tripped = _state_get(trip_key)
        if tripped:
            return (
                f"weekend_watch: tripped {tripped} — no entries until Monday 00:00Z"
            )

        with paper._connect() as conn:
            rows = conn.execute(
                """
                SELECT opened_at, pnl_usd FROM paper_positions
                WHERE bot_id = 'eva_wick' AND opened_at >= ?
                  AND pnl_usd IS NOT NULL AND closed_at IS NOT NULL
                """,
                (WICK_WW_EPOCH,),
            ).fetchall()

        def _wd(s: Any) -> int:
            ts = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return ts.weekday() if ts.tzinfo else ts.replace(
                tzinfo=timezone.utc).weekday()

        baseline = [float(p) for o, p in rows if _wd(o) < 5]
        weekend = [
            float(p) for o, p in rows
            if _wd(o) >= 5 and str(o) >= saturday
        ]
        k = len(weekend)
        if k < WICK_WW_MIN_N or len(baseline) < WICK_WW_MIN_BASELINE:
            return None
        n = len(baseline)
        mu = sum(baseline) / n
        var = sum((x - mu) ** 2 for x in baseline) / (n - 1)
        sd = var ** 0.5
        if sd <= 0:
            return None
        s = sum(weekend)
        p = _norm_cdf((s - k * mu) / (sd * k ** 0.5))
        if p < WICK_WW_ALPHA:
            stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            _state_set(trip_key, stamp)
            reason = (
                f"weekend_watch: {k} weekend trades at ${s:+.2f}, "
                f"p={p:.4f} < {WICK_WW_ALPHA} vs weekday baseline "
                f"(mu={mu:+.3f}, n={n}) — no entries until Monday 00:00Z"
            )
            logger.warning("eva_wick %s", reason)
            return reason
        return None
    except Exception:
        logger.exception("wick_weekend_watch failed — failing open")
        return None


def chop_shadow(product_id: str) -> dict[str, Any]:
    """Trailing-24h chop measurement, for LOGGING ONLY. Never gates.

    chop = 24h range / |24h net|. The weekend that motivated this measured
    3.1 and 18.7 on the two losing days against 1.1 on the winning trend day;
    the gate threshold will be chosen from this forward shadow log once it
    holds a couple of weeks, not from the weekend it would flatter.
    """
    out: dict[str, Any] = {"chop": None, "range_pct": None, "net_pct": None}
    try:
        bars = research.get_ohlc("H1", limit=25, product_id=product_id)
        if len(bars) < 12:
            return out
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        opens = float(bars[0]["open"])
        close = float(bars[-1]["close"])
        rng = (max(highs) - min(lows)) / opens * 100
        net = abs(close / opens - 1) * 100
        out["range_pct"] = round(rng, 3)
        out["net_pct"] = round(net, 3)
        out["chop"] = round(rng / max(net, 0.05), 2)
    except Exception:
        logger.exception("chop_shadow failed for %s", product_id)
    return out


def entry_gate(
    bot_id: str,
    product_id: str,
    now: datetime | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """(skip_reason | None, shadow_tags). One call per entry decision.

    Order matters for cost: the calendar and ledger gates are free and run
    first; the chop shadow costs an OHLC fetch and is computed only when the
    decision actually proceeds — which is exactly the sample the future chop
    threshold has to be chosen from (chop at entry, joined to outcome). A
    gated weekend tick logs chop=None rather than paying an HTTP call to
    decorate a skip.
    """
    if weekend_pause(bot_id, now=now):
        return "weekend_pause", {"chop": None}
    reason = daily_loss_stop(bot_id, now=now)
    if reason:
        return reason, {"chop": None}
    if bot_id == "eva_wick":
        reason = wick_weekend_watch(now=now)
        if reason:
            return reason, {"chop": None}
    if bot_id == "eva_streak":
        reason = streak_loss_cooldown(product_id, now=now)
        if reason:
            return reason, {"chop": None}
    return None, chop_shadow(product_id)
