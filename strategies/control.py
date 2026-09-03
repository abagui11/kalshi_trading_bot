"""Control (conviction) bot — every 15m near-decision enter from vision + sizing."""

from __future__ import annotations

import kalshi_conviction
import kalshi_finalize
import kalshi_triggers
import paper
from models import KalshiSuggestion
from patterns import market_structure_state as mss
from strategies.adverse_boost import adverse_size_boost
from strategies.context import SharedCycleContext


class ControlStrategy:
    bot_id = "control"
    display_name = "Control (conviction ICT)"
    needs_htf_bias = True

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        # Vision checkpoint only near decision offset.
        if not ctx.near_decision:
            return None

        base = ctx.with_bot(self.bot_id)
        if ctx.yes_mid_cents is None:
            return kalshi_finalize.make_skip(
                rationale="no mid available",
                base=base,
                skip_codes=["no_mid"],
            )

        if paper.has_open_for_market(ctx.market_ticker, bot_id=self.bot_id):
            return kalshi_finalize.make_skip(
                rationale="already have open paper position",
                base=base,
                skip_codes=["already_open"],
            )

        htf = ctx.htf
        setup_tags: list[str] = list(htf.setup_tags) if htf else []
        audit: dict = {
            "ict_action": htf.ict_action if htf else "no_trade",
            "ict_bias": htf.ict_bias if htf else "unknown",
            "gate_outcome": htf.gate_outcome if htf else None,
            "ob_low": htf.ob_low if htf else None,
            "ob_high": htf.ob_high if htf else None,
            "h1_bias_tag": htf.htf_bias if htf else "unknown",
            "critic_passes": htf.critic_passes if htf else 0,
            "critic_findings": htf.critic_findings if htf else [],
            "critic_downgraded": bool(htf.critic_downgraded) if htf else False,
            "chart_read_score": htf.chart_read_score if htf else None,
        }

        ict_side = htf.side if htf else None
        htf_bias = htf.htf_bias if htf else "unknown"
        side, side_source = kalshi_conviction.resolve_side_with_fallback(
            ict_side=ict_side,
            htf_bias=htf_bias,
            yes_mid_cents=float(ctx.yes_mid_cents),
        )

        if htf and htf.critic_downgraded:
            setup_tags = setup_tags + ["critic_downgrade"]
        if side_source != "ict":
            setup_tags = setup_tags + [f"fallback_{side_source}"]
        if htf is None:
            setup_tags = setup_tags + ["no_htf_pack"]

        conviction = kalshi_conviction.score_conviction(
            side_source=side_source,
            critic_downgraded=bool(audit["critic_downgraded"]),
            chart_read_score=audit.get("chart_read_score"),
            ict_side_present=ict_side in ("YES", "NO"),
        )
        boost, _boost_audit = adverse_size_boost(
            side=side,
            yes_mid_cents=float(ctx.yes_mid_cents),
            fair_yes_cents=ctx.fair_yes_cents,
            edge_cents=ctx.edge_cents,
        )
        sizing = kalshi_conviction.compute_sizing(
            side=side,
            yes_mid_cents=float(ctx.yes_mid_cents),
            conviction=conviction,
            adverse_boost=boost,
        )
        audit["conviction"] = sizing["conviction"]
        audit["market_agree"] = sizing["market_agree"]
        audit["deploy_pct"] = sizing["deploy_pct"]
        audit["adverse_boost"] = sizing["adverse_boost"]
        audit["side_source"] = side_source
        # Keep boost detail out of suggestion setattr path.

        agree_tag = "market_agree" if sizing["market_agree"] else "market_contra"
        setup_tags = setup_tags + [
            f"conviction_{conviction}",
            agree_tag,
            f"deploy_{sizing['deploy_pct'] * 100:.1f}pct",
        ]

        align = mss.alignment_tag(side, htf_bias)
        if align == "counter_htf" and "counter_htf" not in setup_tags:
            setup_tags.append("counter_htf")

        sfp_tags = " ".join(setup_tags).lower()
        gate = htf.gate_outcome if htf else None
        lottery = "sfp" in sfp_tags or gate == "pass_sfp"

        ict_action = htf.ict_action if htf else "no_trade"
        ict_bias = htf.ict_bias if htf else htf_bias
        ict_rationale = htf.ict_rationale if htf else "no HTF pack; market lean fallback"
        trigger_reason = (
            f"conviction {conviction} via {side_source} → {side} "
            f"({'agree' if sizing['market_agree'] else 'contra'} market) "
            f"deploy {sizing['deploy_pct'] * 100:.1f}% "
            f"(boost {sizing['adverse_boost']:.2f}×)"
        )

        sug = kalshi_finalize.finalize_directional(
            side=side,
            trigger_reason=trigger_reason,
            trigger_type="vision",
            base=base,
            mid=float(ctx.yes_mid_cents),
            fair_cents=ctx.fair_yes_cents,
            edge=ctx.edge_cents,
            expiry_s=ctx.expiry_ts,
            htf_bias=htf_bias,
            ict_action=ict_action,
            ict_bias=ict_bias,
            ict_rationale=ict_rationale,
            gate_outcome=gate,
            setup_tags=setup_tags,
            audit=audit,
            lottery=lottery and kalshi_triggers.in_last_minutes(ctx.expiry_ts),
            structure_chart_path=(htf.structure_chart_path if htf else None),
            entry_chart_path=(htf.entry_chart_path if htf else None),
            trigger_name="vision_conviction",
            require_edge=False,
            allow_rich=True,
            deploy_pct=float(sizing["deploy_pct"]),
        )
        sug.bot_id = self.bot_id
        sug.conviction = str(sizing["conviction"])
        sug.market_agree = bool(sizing["market_agree"])
        sug.deploy_pct = float(sizing["deploy_pct"])
        sug.adverse_boost = float(sizing["adverse_boost"])
        sug.side_source = side_source
        return sug
