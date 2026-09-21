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

Every gate fails open: a broken clock or a failed query must never stop the
book silently — gating is a risk reduction, not a dependency.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import bot_config
import paper
import research

logger = logging.getLogger(__name__)

# Defaults live here (not bot_config) so this module lands without touching
# files that carry unrelated uncommitted work; bot_config can override.
WEEKEND_PAUSE_BOTS: tuple[str, ...] = tuple(
    getattr(bot_config, "KALSHI_WEEKEND_PAUSE_BOTS", ("eva_streak", "eva_wick"))
)
STREAK_COOLDOWN_N: int = int(getattr(bot_config, "EVA_STREAK_LOSS_COOLDOWN_N", 3))
STREAK_COOLDOWN_MIN: int = int(
    getattr(bot_config, "EVA_STREAK_LOSS_COOLDOWN_MIN", 240)
)


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
    if bot_id == "eva_streak":
        reason = streak_loss_cooldown(product_id, now=now)
        if reason:
            return reason, {"chop": None}
    return None, chop_shadow(product_id)
