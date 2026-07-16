"""Tests for Telegram notify formatting and broadcast policy helpers."""

from __future__ import annotations

from critic import AuditFinding, AuditVerdict, compose_rationale, build_market_context_block
from models import Suggestion
from notify import build_rationale_message, format_hourly_monitor_report


def test_hourly_monitor_report_no_trade_skipped_broadcast():
    verdict = AuditVerdict(
        source="hourly",
        cycle_id="20260701T120000Z",
        action="no_trade",
        text_excerpt="HTF bearish, no valid entry.",
        deterministic=[],
        llm_hallucinations=[],
        llm_verified=["Bearish H4 structure cited correctly"],
    )
    text = format_hourly_monitor_report(verdict, broadcast_sent=False)
    assert "NO_TRADE" in text
    assert "Subscriber broadcast: skipped (no_trade)" in text
    assert "All deterministic fact-checks passed" in text
    assert "VERIFIED CLAIMS" in text


def test_hourly_monitor_report_trade_with_issues():
    verdict = AuditVerdict(
        source="hourly",
        cycle_id="20260701T130000Z",
        action="deriv_sell",
        text_excerpt="Short at M5 OB retest.",
        deterministic=[
            AuditFinding(code="M5_OB_MISLABEL", message="bounds wrong"),
        ],
        llm_hallucinations=[
            AuditFinding(code="LLM_HALLUCINATION", message="fake SFP"),
        ],
        sanitized=True,
    )
    text = format_hourly_monitor_report(verdict, broadcast_sent=True)
    assert "Subscriber broadcast: sent" in text
    assert "M5_OB_MISLABEL" in text
    assert "LLM_HALLUCINATION" in text
    assert "sanitized" in text.lower()


def test_hourly_monitor_report_shows_refine_metadata():
    verdict = AuditVerdict(
        source="hourly",
        cycle_id="20260701T140000Z",
        action="no_trade",
        text_excerpt="Audit downgrade.",
        deterministic=[],
        llm_hallucinations=[],
        sanitized=True,
        downgraded=True,
        passes_used=2,
    )
    text = format_hourly_monitor_report(verdict, broadcast_sent=False)
    assert "downgraded to no_trade" in text
    assert "Refine passes used: 2" in text


def test_rationale_message_why_then_market_context():
    context = build_market_context_block(
        ["Price inside bullish M5 OB (1,915.70-1,920.32) — wait for fib retest"]
    )
    thesis = (
        "Despite bullish M5 OB nearby, shorting bearish M5 OB fib — "
        "HTF is advisory only."
    )
    suggestion = Suggestion.from_dict(
        {
            "action": "spot_sell",
            "size": 0.5,
            "entry": 1918.0,
            "stop_loss": 1925.0,
            "take_profits": [1900.0],
            "rationale": compose_rationale(thesis, context),
        }
    )
    text = build_rationale_message(suggestion, "Paper PnL: n/a")
    assert "SPOT_SELL" in text
    assert "Why this trade:" in text
    assert "Market context:" in text
    assert text.index("Why this trade:") < text.index("Market context:")
    assert "Rationale:" not in text
    assert "Paper PnL: n/a" in text