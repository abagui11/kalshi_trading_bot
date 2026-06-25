"""Research analytics orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import charts
import ohlc_cache
from patterns.sfp import SFPEvent, compute_stats, detect_sfps

METHODOLOGY_FOOTNOTE = (
    "Methodology: Coinbase ETH-USD, weekly W-FRI bars. "
    "SFP = L=2 pivot, wick sweeps >=0.1% past level, close back inside. "
    "Reversal (A) = close in SFP direction within N bars without first closing past swept level. "
    "B/C logged separately (>=5% move, structure break). Not financial advice."
)


@dataclass
class ResearchResult:
    chart_path: str
    summary_text: str
    caption: str
    events: list[SFPEvent]
    stats: dict
    years: int


def _format_summary(stats: dict, years: int, events: list[SFPEvent]) -> str:
    lines = [
        f"Weekly SFP reversal study ({years} years)",
        "",
        f"Headline (Outcome A): {stats['reversal_pct']}% reversal",
        f"  {stats['reversals']} reversals / {stats['invalidations']} invalidations",
        f"  ({stats['reversals'] + stats['invalidations']} scored; "
        f"{stats['neutral']} neutral, {stats['pending']} pending)",
        "",
        f"Total SFPs detected: {stats['total_sfps']}",
        f"Outcome B (>=5% move in direction): {stats['outcome_b_pct']}% "
        f"({stats['outcome_b_count']}/{stats['total_sfps'] - stats['pending']})",
        f"Outcome C (structure break): {stats['outcome_c_pct']}% "
        f"({stats['outcome_c_count']}/{stats['total_sfps'] - stats['pending']})",
        "",
        "Recent events:",
    ]
    recent = sorted(events, key=lambda e: e.ts)[-5:]
    for e in recent:
        lines.append(
            f"  {e.ts[:10]} {e.direction} @ {e.swept_level:,.0f} -> {e.outcome_a}"
        )
    lines.extend(["", METHODOLOGY_FOOTNOTE])
    return "\n".join(lines)


def _build_caption(stats: dict, years: int) -> str:
    scored = stats["reversals"] + stats["invalidations"]
    return (
        f"Weekly SFP — {years}y ETH-USD\n"
        f"{stats['reversal_pct']}% reversal ({stats['reversals']}/{scored} scored)\n"
        f"{stats['total_sfps']} SFPs detected"
    )[:1024]


def weekly_sfp_report(years: int = 4) -> ResearchResult:
    """Run weekly SFP study: cache -> detect -> chart -> summary."""
    weekly_bars = ohlc_cache.get_weekly_bars(years=years)
    if not weekly_bars:
        raise RuntimeError("No weekly bars available — run backfill.py first.")

    events = detect_sfps(weekly_bars, timeframe="W1")
    stats = compute_stats(events)

    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    chart_path = charts.render_research_chart(
        weekly_bars,
        events,
        stats,
        timeframe="W1",
        cycle_id=cycle_id,
        years=years,
    )

    summary = _format_summary(stats, years, events)
    caption = _build_caption(stats, years)

    return ResearchResult(
        chart_path=chart_path,
        summary_text=summary,
        caption=caption,
        events=events,
        stats=stats,
        years=years,
    )


if __name__ == "__main__":
    print("Running weekly SFP report...")
    result = weekly_sfp_report(years=4)
    print(result.summary_text)
    print(f"\nChart: {result.chart_path}")
