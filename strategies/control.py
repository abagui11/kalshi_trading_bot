"""Control (vanilla) bot — current ICT vision + finalize path."""

from __future__ import annotations

import kalshi_finalize
import kalshi_triggers
import paper
from models import KalshiSuggestion
from patterns import market_structure_state as mss
from strategies.context import SharedCycleContext


class ControlStrategy:
    bot_id = "control"
    display_name = "Control (vanilla ICT)"
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
        if htf is None:
            return kalshi_finalize.make_skip(
                rationale="skipped: shared HTF bias unavailable",
                base=base,
                skip_codes=["no_htf_bias"],
            )

        setup_tags = list(htf.setup_tags)
        audit = {
            "ict_action": htf.ict_action,
            "ict_bias": htf.ict_bias,
            "gate_outcome": htf.gate_outcome,
            "ob_low": htf.ob_low,
            "ob_high": htf.ob_high,
            "h1_bias_tag": htf.htf_bias,
            "critic_passes": htf.critic_passes,
            "critic_findings": htf.critic_findings,
            "critic_downgraded": htf.critic_downgraded,
            "chart_read_score": htf.chart_read_score,
        }

        if htf.critic_downgraded:
            findings_txt = "; ".join(
                f.get("message") or f.get("code") or ""
                for f in (htf.critic_findings or [])[:4]
            )
            return kalshi_finalize.make_skip(
                rationale=(
                    f"skipped (critic downgrade): {htf.ict_rationale}"
                    + (f" Downgraded because: {findings_txt}." if findings_txt else "")
                ),
                base=base,
                htf_bias=htf.htf_bias,
                setup_tags=setup_tags + ["critic_downgrade"],
                skip_codes=["critic_downgrade"],
                audit=audit,
                structure_chart_path=htf.structure_chart_path,
                entry_chart_path=htf.entry_chart_path,
                ict_action=htf.ict_action,
                ict_bias=htf.ict_bias,
            )

        side = htf.side
        if side is None:
            return kalshi_finalize.make_skip(
                rationale=f"skipped (ICT no_trade): {htf.ict_rationale}",
                base=base,
                htf_bias=htf.htf_bias,
                setup_tags=setup_tags,
                skip_codes=["ict_no_trade"],
                audit=audit,
                structure_chart_path=htf.structure_chart_path,
                entry_chart_path=htf.entry_chart_path,
                ict_action=htf.ict_action,
                ict_bias=htf.ict_bias,
                trigger_type="vision",
            )

        align = mss.alignment_tag(side, htf.htf_bias)
        if align == "counter_htf" and "counter_htf" not in setup_tags:
            setup_tags.append("counter_htf")

        sfp_tags = " ".join(setup_tags).lower()
        lottery = "sfp" in sfp_tags or htf.gate_outcome == "pass_sfp"

        sug = kalshi_finalize.finalize_directional(
            side=side,
            trigger_reason=f"vision ICT {htf.ict_action} → {side}",
            trigger_type="vision",
            base=base,
            mid=float(ctx.yes_mid_cents),
            fair_cents=ctx.fair_yes_cents,
            edge=ctx.edge_cents,
            expiry_s=ctx.expiry_ts,
            htf_bias=htf.htf_bias,
            ict_action=htf.ict_action,
            ict_bias=htf.ict_bias,
            ict_rationale=htf.ict_rationale,
            gate_outcome=htf.gate_outcome,
            setup_tags=setup_tags,
            audit=audit,
            lottery=lottery and kalshi_triggers.in_last_minutes(ctx.expiry_ts),
            structure_chart_path=htf.structure_chart_path,
            entry_chart_path=htf.entry_chart_path,
            trigger_name="vision_ict",
        )
        sug.bot_id = self.bot_id
        return sug
