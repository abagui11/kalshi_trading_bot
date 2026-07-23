"""Mechanical short-horizon trigger + HTF veto + KalshiRules helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from patterns.market_context import MarketContext

# Spot must be at least this far through strike (percent).
MIN_THROUGH_STRIKE_PCT = 0.05
# KalshiRules: NEVER BUY > 55¢.
MAX_ENTRY_CENTS = 55.0
# Prefer waiting above 50¢ (entry procedure); shadow-tag rich vs preferred.
PREFERRED_MAX_ENTRY_CENTS = 50.0
# Intended limit improvement vs mid (¢) at window open.
LIMIT_IMPROVE_CENTS = 3.0
# Last N minutes of window: default block (except lottery / strong signal — later).
BLOCK_LAST_MINUTES = 3.0
LOTTERY_MIN_CENTS = 5.0
LOTTERY_MAX_CENTS = 10.0
M5_RETRACE_PCT = 0.25  # M5 ≥0.25% likely retraces next 5m

_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TriggerResult:
    side: str | None  # YES | NO | None
    reason: str
    htf_bias: str  # bull | bear | mixed | unknown
    through_strike_pct: float | None
    momentum_pct: float | None
    vetoed: bool = False


def session_label_et(now: datetime | None = None) -> str:
    """KalshiRules session bucket in America/New_York."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    et = now.astimezone(_ET)
    if et.weekday() >= 5:
        return "weekend"
    # Asia 9pm–4am ET
    hour = et.hour
    if hour >= 21 or hour < 4:
        return "asia"
    # Rough US cash hours 9:30–16:00
    if (hour > 9 or (hour == 9 and et.minute >= 30)) and hour < 16:
        return "us_rth"
    return "us_off_hours"


def minutes_to_expiry(expiry_ts: str | None, *, now: datetime | None = None) -> float | None:
    if not expiry_ts:
        return None
    now = now or datetime.now(timezone.utc)
    s = str(expiry_ts).strip()
    try:
        if s.endswith("Z"):
            if "." in s:
                exp = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    tzinfo=timezone.utc
                )
            else:
                exp = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
        else:
            exp = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (exp - now).total_seconds() / 60.0


def in_last_minutes(
    expiry_ts: str | None,
    *,
    minutes: float = BLOCK_LAST_MINUTES,
    now: datetime | None = None,
) -> bool:
    m = minutes_to_expiry(expiry_ts, now=now)
    return m is not None and 0 <= m <= float(minutes)


def intended_limit_cents(side: str, yes_mid_cents: float, improve: float = LIMIT_IMPROVE_CENTS) -> float:
    """KalshiRules: bid ~3¢ more favorable than mid at window open."""
    mid = float(yes_mid_cents)
    if side.upper() == "YES":
        return max(1.0, min(99.0, mid - float(improve)))
    return max(1.0, min(99.0, (100.0 - mid) - float(improve)))


def is_lottery_ticket(entry_cents: float) -> bool:
    return LOTTERY_MIN_CENTS <= float(entry_cents) <= LOTTERY_MAX_CENTS


def htf_bias_from_context(ctx: MarketContext | None) -> str:
    if ctx is None:
        return "unknown"
    tags = {t.lower() for t in (ctx.setup_tags or [])}
    if "htf_mixed" in tags or "htf_zone_conflict" in tags:
        return "mixed"
    if "htf_bull" in tags and "htf_bear" not in tags:
        return "bull"
    if "htf_bear" in tags and "htf_bull" not in tags:
        return "bear"
    return "mixed" if tags else "unknown"


def htf_vetoes(side: str, htf_bias: str) -> bool:
    """True if HTF clearly conflicts with proposed side."""
    s = side.upper()
    if htf_bias == "bull" and s == "NO":
        return True
    if htf_bias == "bear" and s == "YES":
        return True
    return False


def short_horizon_trigger(
    *,
    spot: float,
    strike: float,
    yes_mid_cents: float,
    prior_5m_ret_pct: float | None,
    prior_15m_ret_pct: float | None,
    market_context: MarketContext | None = None,
    min_through_pct: float = MIN_THROUGH_STRIKE_PCT,
    max_entry_cents: float = MAX_ENTRY_CENTS,
) -> TriggerResult:
    """Propose YES/NO from spot-through-strike + aligned short-horizon momentum.

    Does not apply HTF veto — caller checks htf_vetoes after.
    """
    htf = htf_bias_from_context(market_context)
    if spot <= 0 or strike <= 0:
        return TriggerResult(
            side=None,
            reason="invalid spot/strike",
            htf_bias=htf,
            through_strike_pct=None,
            momentum_pct=None,
        )

    through = (spot / strike - 1.0) * 100.0
    # Prefer 5m for impulse/retrace; allow 15m for alignment if 5m missing.
    mom_5 = prior_5m_ret_pct
    mom = prior_5m_ret_pct if prior_5m_ret_pct is not None else prior_15m_ret_pct

    if abs(through) < float(min_through_pct):
        return TriggerResult(
            side=None,
            reason=f"spot vs strike {through:+.4f}% < {min_through_pct}% threshold",
            htf_bias=htf,
            through_strike_pct=through,
            momentum_pct=mom,
        )

    if through > 0:
        side = "YES"
        entry = float(yes_mid_cents)
        need_mom = "up"
    else:
        side = "NO"
        entry = 100.0 - float(yes_mid_cents)
        need_mom = "down"

    if entry > float(max_entry_cents):
        return TriggerResult(
            side=None,
            reason=(
                f"KalshiRules NEVER BUY >{max_entry_cents:.0f}¢: "
                f"{side} mid-entry {entry:.1f}¢"
            ),
            htf_bias=htf,
            through_strike_pct=through,
            momentum_pct=mom,
            vetoed=False,
        )

    if mom is None:
        return TriggerResult(
            side=None,
            reason="missing short-horizon momentum",
            htf_bias=htf,
            through_strike_pct=through,
            momentum_pct=None,
        )

    aligned = (need_mom == "up" and mom > 0) or (need_mom == "down" and mom < 0)
    if not aligned:
        return TriggerResult(
            side=None,
            reason=(
                f"momentum {mom:+.4f}% not aligned with through-strike "
                f"{through:+.4f}% ({side})"
            ),
            htf_bias=htf,
            through_strike_pct=through,
            momentum_pct=mom,
        )

    # KalshiRules: last M5 ≥0.25% likely retraces — never chase that impulse.
    if mom_5 is not None and abs(mom_5) >= M5_RETRACE_PCT:
        return TriggerResult(
            side=None,
            reason=(
                f"KalshiRules M5≥{M5_RETRACE_PCT}% impulse ({mom_5:+.4f}%) — "
                "likely retrace next 5m; never chase"
            ),
            htf_bias=htf,
            through_strike_pct=through,
            momentum_pct=mom,
        )

    limit = intended_limit_cents(side, yes_mid_cents)
    return TriggerResult(
        side=side,
        reason=(
            f"KalshiRules short-horizon: spot {through:+.4f}% through strike + "
            f"momentum {mom:+.4f}% → {side}; intended limit ~{limit:.1f}¢ "
            f"(≤{max_entry_cents:.0f}¢ hard cap)"
        ),
        htf_bias=htf,
        through_strike_pct=through,
        momentum_pct=mom,
    )


def shadow_skip_reasons(
    *,
    side: str | None,
    entry_cents: float | None,
    through_strike_pct: float | None,
    momentum_pct: float | None,
    gate_outcome: str | None,
    htf_bias: str,
    fair_yes_cents: float | None,
    yes_mid_cents: float | None,
    min_edge: float,
    minutes_left: float | None = None,
) -> list[str]:
    """Non-hard filters tagged for later promotion — do not block paper fills."""
    reasons: list[str] = []
    if entry_cents is not None and entry_cents > MAX_ENTRY_CENTS:
        reasons.append("rich_ticket")
    if entry_cents is not None and entry_cents > PREFERRED_MAX_ENTRY_CENTS:
        reasons.append("above_50c_wait")
    if (
        side
        and through_strike_pct is not None
        and momentum_pct is not None
    ):
        if side == "YES" and momentum_pct < 0:
            reasons.append("against_momentum")
        if side == "NO" and momentum_pct > 0:
            reasons.append("against_momentum")
    if (
        fair_yes_cents is not None
        and yes_mid_cents is not None
        and abs(fair_yes_cents - yes_mid_cents) < float(min_edge)
    ):
        reasons.append("coin_flip_gap")
    if gate_outcome in ("fail", "skipped_llm_no_trade"):
        reasons.append("gate_fail")
    if side and htf_vetoes(side, htf_bias):
        reasons.append("htf_conflict")
    if minutes_left is not None and 0 <= minutes_left <= BLOCK_LAST_MINUTES:
        reasons.append("last_3m_block")
    return reasons


def m5_range_pct(bars: Sequence[dict[str, Any]], n_bars: int = 3) -> float | None:
    """Recent M5 high-low range as percent of last close (proxy 15m ATR piece)."""
    window = list(bars[-n_bars:]) if bars else []
    if not window:
        return None
    try:
        hi = max(float(b["high"]) for b in window)
        lo = min(float(b["low"]) for b in window)
        close = float(window[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if close <= 0:
        return None
    return (hi - lo) / close * 100.0


def compose_kalshi_rules_rationale(
    *,
    session: str,
    trigger_reason: str,
    side: str,
    yes_mid_cents: float,
    entry_cents: float,
    limit_cents: float,
    fair_cents: float,
    edge_cents: float,
    gate_outcome: str | None,
    ict_bias: str,
    ict_rationale: str,
    minutes_left: float | None,
    lottery: bool = False,
) -> str:
    """Engine-composed rationale that always cites KalshiRules (plus ICT body)."""
    block = "lottery ticket exception" if lottery else "not in last-3m block"
    if minutes_left is not None and not lottery:
        block = (
            f"minutes_left={minutes_left:.1f} — outside last-{BLOCK_LAST_MINUTES:.0f}m block"
            if minutes_left > BLOCK_LAST_MINUTES
            else f"minutes_left={minutes_left:.1f} — last-{BLOCK_LAST_MINUTES:.0f}m (blocked unless exception)"
        )
    parts = [
        f"KalshiRules session: {session}.",
        f"KalshiRules entry: {side} mid≈{_side_mid(side, yes_mid_cents):.1f}¢ → "
        f"intended limit ~{limit_cents:.1f}¢ (−{LIMIT_IMPROVE_CENTS:.0f}¢ favorable); "
        f"fill/paper {entry_cents:.1f}¢; NEVER BUY >{MAX_ENTRY_CENTS:.0f}¢; "
        f"prefer ≤{PREFERRED_MAX_ENTRY_CENTS:.0f}¢ for ~2× payout.",
        f"KalshiRules block/execute: {block}"
        + (" — KalshiRules: lottery ticket." if lottery else "."),
        f"Model fair YES {fair_cents:.1f}¢ vs mid {yes_mid_cents:.1f}¢ (edge {edge_cents:+.1f}¢); "
        "index may ≠ Coinbase chart (settlement risk).",
        f"Trigger: {trigger_reason}. Gate={gate_outcome or 'n/a'}.",
        f"ICT {ict_bias}: {ict_rationale}",
    ]
    return " ".join(parts)


def _side_mid(side: str, yes_mid: float) -> float:
    if side.upper() == "YES":
        return float(yes_mid)
    return 100.0 - float(yes_mid)
