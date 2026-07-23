"""Shared Kalshi fill/skip finalization for vision and watchdog paths."""

from __future__ import annotations

import logging
from typing import Any

import bot_config
import kalshi_fair
import kalshi_triggers
from models import KalshiSuggestion
from patterns.market_structure_state import alignment_tag, htf_paragraph

logger = logging.getLogger(__name__)


def _side_mid(side: str, yes_mid: float) -> float:
    if side.upper() == "YES":
        return float(yes_mid)
    return 100.0 - float(yes_mid)


def mid_too_extreme(side: str, mid: float) -> str | None:
    extreme = float(getattr(bot_config, "KALSHI_EXTREME_MID_CENTS", 5.0))
    if mid < extreme or mid > (100.0 - extreme):
        return f"mid {mid:.1f}¢ too extreme (<{extreme} or >{100 - extreme})"
    return None


def size_at_entry(entry_cents: float) -> tuple[int, float]:
    """Return (contracts, budget_usd) for intended entry price."""
    bankroll = float(bot_config.KALSHI_BANKROLL_USD)
    try:
        import kalshi_cycle

        bankroll = kalshi_cycle._bankroll_usd()
    except Exception:
        pass
    budget = max(0.0, bankroll * float(bot_config.KALSHI_DEPLOY_PCT))
    price = float(entry_cents) / 100.0
    cap = max(1, int(bot_config.KALSHI_MAX_CONTRACTS))
    if price <= 0:
        return 0, budget
    contracts = max(0, min(cap, int(budget // price)))
    if contracts < 1 and budget >= price:
        contracts = 1
    return contracts, budget


def has_actionable_edge(
    side: str,
    edge_cents: float | None,
    yes_mid_cents: float,
    fair_yes_cents: float | None,
    *,
    min_edge: float | None = None,
) -> tuple[bool, str]:
    """Edge filter after structure: min |fair−mid| and side agrees with edge.

    Also treats adverse mid vs fair (more upside for the side) as actionable.
    """
    min_e = float(min_edge if min_edge is not None else bot_config.KALSHI_MIN_EDGE_CENTS)
    if edge_cents is None or fair_yes_cents is None:
        return False, "fair value unavailable"
    if not kalshi_fair.has_min_edge(edge_cents, min_e):
        return (
            False,
            f"|fair−mid|={abs(edge_cents):.1f}¢ < {min_e:.0f}¢ "
            f"(fair {fair_yes_cents:.1f}¢ mid {yes_mid_cents:.1f}¢)",
        )
    if not kalshi_fair.side_agrees_with_edge(side, edge_cents):
        return (
            False,
            f"{side} disagrees with fair edge {edge_cents:+.1f}¢ "
            f"(fair {fair_yes_cents:.1f} vs mid {yes_mid_cents:.1f})",
        )
    return True, f"edge {edge_cents:+.1f}¢ favors {side}"


def attach_htf_tags(
    suggestion: KalshiSuggestion,
    *,
    htf_bias: str,
    side: str | None = None,
) -> KalshiSuggestion:
    """Ensure setup_tags include HTF + alignment; prepend HTF paragraph if missing."""
    use_side = side or (suggestion.side if suggestion.side != "SKIP" else None)
    tags = list(suggestion.setup_tags or [])
    for t in kalshi_triggers.soft_htf_tags(use_side, htf_bias):
        if t not in tags:
            tags.append(t)
    suggestion.setup_tags = tags
    suggestion.h1_bias_tag = htf_bias
    align = alignment_tag(use_side, htf_bias)
    para = htf_paragraph(htf_bias, use_side, align)
    body = (suggestion.rationale or "").strip()
    if body and not body.lower().startswith("htf bias"):
        suggestion.rationale = f"{para} {body}"
    elif not body:
        suggestion.rationale = para
    return suggestion


def finalize_directional(
    *,
    side: str,
    trigger_reason: str,
    trigger_type: str,
    base: dict[str, Any],
    mid: float,
    fair_cents: float | None,
    edge: float | None,
    expiry_s: str | None,
    htf_bias: str,
    ict_action: str | None = None,
    ict_bias: str | None = None,
    ict_rationale: str = "",
    gate_outcome: str | None = None,
    setup_tags: list[str] | None = None,
    audit: dict[str, Any] | None = None,
    lottery: bool = False,
    structure_chart_path: str | None = None,
    entry_chart_path: str | None = None,
    trigger_name: str | None = None,
    shadow_only: bool = False,
    skip_codes: list[str] | None = None,
) -> KalshiSuggestion:
    """Apply shared KalshiRules gates and size a YES/NO suggestion (or skip)."""
    min_edge = float(bot_config.KALSHI_MIN_EDGE_CENTS)
    tags = list(setup_tags or [])
    codes = list(skip_codes or [])
    audit = dict(audit or {})

    ok_edge, edge_reason = has_actionable_edge(
        side, edge, float(mid), fair_cents, min_edge=min_edge
    )
    if not ok_edge:
        sug = KalshiSuggestion.skip(
            rationale=f"skipped (edge filter): {edge_reason}. {trigger_reason}. {ict_rationale}",
            ict_action=ict_action,
            ict_bias=ict_bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        sug.trigger_name = trigger_name
        sug.setup_tags = tags
        sug.skip_codes = codes + ["edge_filter"]
        sug.would_skip_reasons = ["edge_filter"]
        sug.structure_chart_path = structure_chart_path
        sug.entry_chart_path = entry_chart_path
        return attach_htf_tags(sug, htf_bias=htf_bias, side=None)

    extreme = mid_too_extreme(side, float(mid))
    if extreme:
        sug = KalshiSuggestion.skip(
            rationale=f"skipped: {extreme}. {trigger_reason}. {ict_rationale}",
            ict_action=ict_action,
            ict_bias=ict_bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        sug.trigger_name = trigger_name
        sug.setup_tags = tags
        sug.skip_codes = codes + ["extreme_mid"]
        sug.structure_chart_path = structure_chart_path
        sug.entry_chart_path = entry_chart_path
        return attach_htf_tags(sug, htf_bias=htf_bias, side=None)

    minutes_left = kalshi_triggers.minutes_to_expiry(expiry_s)
    seconds_left = (minutes_left * 60.0) if minutes_left is not None else None
    mid_side = _side_mid(side, float(mid))
    use_lottery = lottery
    if kalshi_triggers.in_last_minutes(expiry_s) and not use_lottery:
        if kalshi_triggers.is_lottery_ticket(mid_side) and gate_outcome == "pass_sfp":
            use_lottery = True
            trigger_type = "lottery_ticket"
        else:
            sug = KalshiSuggestion.skip(
                rationale=(
                    f"skipped (KalshiRules last-{kalshi_triggers.BLOCK_LAST_MINUTES:.0f}m block): "
                    f"minutes_left={minutes_left}. {trigger_reason}. {ict_rationale}"
                ),
                ict_action=ict_action,
                ict_bias=ict_bias,
                **base,
            )
            for k, v in audit.items():
                setattr(sug, k, v)
            sug.trigger_type = "none"
            sug.trigger_name = trigger_name
            sug.setup_tags = tags
            sug.skip_codes = codes + ["last_3m_block"]
            sug.would_skip_reasons = ["last_3m_block"]
            sug.seconds_to_expiry = seconds_left
            sug.structure_chart_path = structure_chart_path
            sug.entry_chart_path = entry_chart_path
            return attach_htf_tags(sug, htf_bias=htf_bias, side=None)

    if use_lottery:
        entry_cents = mid_side
    else:
        entry_cents = kalshi_triggers.intended_limit_cents(side, float(mid))

    if entry_cents > kalshi_triggers.MAX_ENTRY_CENTS:
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped (KalshiRules NEVER BUY >{kalshi_triggers.MAX_ENTRY_CENTS:.0f}¢): "
                f"limit {entry_cents:.1f}¢. {trigger_reason}. {ict_rationale}"
            ),
            ict_action=ict_action,
            ict_bias=ict_bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        sug.trigger_name = trigger_name
        sug.setup_tags = tags
        sug.skip_codes = codes + ["rich_ticket"]
        sug.seconds_to_expiry = seconds_left
        sug.structure_chart_path = structure_chart_path
        sug.entry_chart_path = entry_chart_path
        return attach_htf_tags(sug, htf_bias=htf_bias, side=None)

    contracts, budget = size_at_entry(entry_cents)
    if contracts < 1:
        sug = KalshiSuggestion.skip(
            rationale=(
                f"skipped: bankroll too small for 1 contract at {entry_cents:.1f}¢ "
                f"(budget ${budget:.2f}). {trigger_reason}"
            ),
            ict_action=ict_action,
            ict_bias=ict_bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        sug.trigger_name = trigger_name
        sug.setup_tags = tags
        sug.skip_codes = codes + ["undersized"]
        sug.seconds_to_expiry = seconds_left
        sug.structure_chart_path = structure_chart_path
        sug.entry_chart_path = entry_chart_path
        return attach_htf_tags(sug, htf_bias=htf_bias, side=None)

    if shadow_only:
        codes = codes + ["watchdog_shadow"]
        tags = tags + ["watchdog_shadow"]
        sug = KalshiSuggestion.skip(
            rationale=(
                f"shadow (execute off): would {side} @ {entry_cents:.1f}¢ x{contracts}. "
                f"{trigger_reason}. {ict_rationale}"
            ),
            ict_action=ict_action,
            ict_bias=ict_bias,
            **base,
        )
        for k, v in audit.items():
            setattr(sug, k, v)
        sug.trigger_type = trigger_type
        sug.trigger_name = trigger_name
        sug.setup_tags = tags
        sug.skip_codes = codes
        sug.would_skip_reasons = ["watchdog_shadow"]
        sug.seconds_to_expiry = seconds_left
        sug.structure_chart_path = structure_chart_path
        sug.entry_chart_path = entry_chart_path
        sug.entry_cents = float(entry_cents)
        return attach_htf_tags(sug, htf_bias=htf_bias, side=side)

    session = kalshi_triggers.session_label_et()
    align = alignment_tag(side, htf_bias)
    if align == "counter_htf" and "counter_htf" not in tags:
        tags.append("counter_htf")

    rationale = kalshi_triggers.compose_kalshi_rules_rationale(
        session=session,
        trigger_reason=trigger_reason,
        side=side,
        yes_mid_cents=float(mid),
        entry_cents=float(entry_cents),
        limit_cents=float(entry_cents),
        fair_cents=float(fair_cents or mid),
        edge_cents=float(edge or 0),
        gate_outcome=gate_outcome,
        ict_bias=ict_bias or htf_bias,
        ict_rationale=ict_rationale or trigger_reason,
        minutes_left=minutes_left,
        lottery=use_lottery,
    )

    shadow = kalshi_triggers.shadow_skip_reasons(
        side=side,
        entry_cents=entry_cents,
        through_strike_pct=None,
        momentum_pct=None,
        gate_outcome=gate_outcome,
        htf_bias=htf_bias,
        fair_yes_cents=fair_cents,
        yes_mid_cents=float(mid),
        min_edge=min_edge,
        minutes_left=minutes_left,
    )

    sug = KalshiSuggestion(
        series=base["series"],
        market_ticker=base["market_ticker"],
        side=side,
        contracts=contracts,
        entry_cents=float(entry_cents),
        expiry_ts=base.get("expiry_ts"),
        rationale=rationale,
        product_id=base["product_id"],
        fair_yes_cents=base.get("fair_yes_cents"),
        mid_cents=base.get("mid_cents"),
        edge_cents=base.get("edge_cents"),
        ict_action=ict_action,
        ict_bias=ict_bias,
        spot=base.get("spot"),
        strike=base.get("strike"),
        spot_vs_strike_pct=base.get("spot_vs_strike_pct"),
        tau_sec=base.get("tau_sec"),
        sigma=base.get("sigma"),
        prior_5m_ret=base.get("prior_5m_ret"),
        prior_15m_ret=base.get("prior_15m_ret"),
        prior_1h_ret=base.get("prior_1h_ret"),
        gate_outcome=gate_outcome,
        trigger_type=trigger_type,
        trigger_name=trigger_name,
        ob_low=audit.get("ob_low"),
        ob_high=audit.get("ob_high"),
        h1_bias_tag=htf_bias,
        critic_passes=int(audit.get("critic_passes") or 0),
        critic_findings=list(audit.get("critic_findings") or []),
        critic_downgraded=bool(audit.get("critic_downgraded") or False),
        would_skip_reasons=shadow,
        setup_tags=tags,
        skip_codes=codes,
        seconds_to_expiry=seconds_left,
        structure_chart_path=structure_chart_path,
        entry_chart_path=entry_chart_path,
        chart_path=entry_chart_path or structure_chart_path,
        chart_read_score=audit.get("chart_read_score"),
        cycle_id=base.get("cycle_id"),
    )
    if base.get("bot_id"):
        sug.bot_id = str(base["bot_id"])
    return attach_htf_tags(sug, htf_bias=htf_bias, side=side)


def make_skip(
    *,
    rationale: str,
    base: dict[str, Any],
    htf_bias: str = "unknown",
    setup_tags: list[str] | None = None,
    skip_codes: list[str] | None = None,
    audit: dict[str, Any] | None = None,
    structure_chart_path: str | None = None,
    entry_chart_path: str | None = None,
    trigger_type: str = "none",
    trigger_name: str | None = None,
    ict_action: str | None = None,
    ict_bias: str | None = None,
) -> KalshiSuggestion:
    sug = KalshiSuggestion.skip(
        rationale=rationale,
        ict_action=ict_action,
        ict_bias=ict_bias,
        **base,
    )
    for k, v in (audit or {}).items():
        setattr(sug, k, v)
    sug.trigger_type = trigger_type
    sug.trigger_name = trigger_name
    sug.setup_tags = list(setup_tags or [])
    sug.skip_codes = list(skip_codes or [])
    sug.would_skip_reasons = list(skip_codes or [])
    sug.structure_chart_path = structure_chart_path
    sug.entry_chart_path = entry_chart_path
    sug.chart_path = entry_chart_path or structure_chart_path
    if base.get("bot_id"):
        sug.bot_id = str(base["bot_id"])
    minutes_left = kalshi_triggers.minutes_to_expiry(base.get("expiry_ts"))
    if minutes_left is not None:
        sug.seconds_to_expiry = minutes_left * 60.0
    return attach_htf_tags(sug, htf_bias=htf_bias, side=None)
