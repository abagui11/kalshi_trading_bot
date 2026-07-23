"""Tests for soft HTF, MarketStructureState, finalize, and Kalshi watchdog shadow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import bot_config
import kalshi_finalize
import kalshi_triggers
import paper
from models import KalshiSuggestion
from patterns.market_structure_state import (
    alignment_tag,
    htf_paragraph,
    refresh_from_context,
    window_thesis_for_bias,
)


class SoftHtfTests(unittest.TestCase):
    def test_alignment_tags(self) -> None:
        self.assertEqual(alignment_tag("YES", "bull"), "aligned_htf")
        self.assertEqual(alignment_tag("NO", "bull"), "counter_htf")
        self.assertEqual(alignment_tag("NO", "bear"), "aligned_htf")
        self.assertEqual(alignment_tag("YES", "bear"), "counter_htf")
        self.assertEqual(alignment_tag("YES", "mixed"), "htf_mixed")

    def test_htf_vetoes_still_detects_conflict_but_soft_tags_used(self) -> None:
        self.assertTrue(kalshi_triggers.htf_vetoes("YES", "bear"))
        tags = kalshi_triggers.soft_htf_tags("YES", "bear")
        self.assertIn("counter_htf", tags)
        self.assertIn("htf_bear", tags)

    def test_htf_paragraph(self) -> None:
        text = htf_paragraph("bull", "YES", "aligned_htf")
        self.assertTrue(text.lower().startswith("htf bias"))
        self.assertIn("YES", text)

    def test_window_thesis(self) -> None:
        t = window_thesis_for_bias("bear", "KXETH15M-123")
        self.assertIn("NO", t)
        self.assertIn("YES", t)


class FinalizeEdgeTests(unittest.TestCase):
    def test_edge_filter_skips_without_min_edge(self) -> None:
        base = {
            "series": "KXETH15M",
            "market_ticker": "KXETH15M-T",
            "product_id": "ETH",
            "mid_cents": 50.0,
            "fair_yes_cents": 52.0,
            "edge_cents": 2.0,
            "expiry_ts": None,
            "cycle_id": "t1",
        }
        with patch.object(bot_config, "KALSHI_MIN_EDGE_CENTS", 8.0):
            sug = kalshi_finalize.finalize_directional(
                side="YES",
                trigger_reason="test",
                trigger_type="vision",
                base=base,
                mid=50.0,
                fair_cents=52.0,
                edge=2.0,
                expiry_s=None,
                htf_bias="bull",
            )
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("edge_filter", sug.skip_codes)

    def test_shadow_mode_no_fill(self) -> None:
        base = {
            "series": "KXETH15M",
            "market_ticker": "KXETH15M-T",
            "product_id": "ETH",
            "mid_cents": 40.0,
            "fair_yes_cents": 55.0,
            "edge_cents": 15.0,
            "expiry_ts": None,
            "cycle_id": "t2",
            "spot": 3000.0,
            "strike": 2990.0,
        }
        with patch.object(bot_config, "KALSHI_MIN_EDGE_CENTS", 8.0):
            with patch.object(bot_config, "KALSHI_BANKROLL_USD", 1000.0):
                with patch.object(bot_config, "KALSHI_DEPLOY_PCT", 0.1):
                    with patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 10):
                        sug = kalshi_finalize.finalize_directional(
                            side="YES",
                            trigger_reason="fib entry",
                            trigger_type="watchdog",
                            base=base,
                            mid=40.0,
                            fair_cents=55.0,
                            edge=15.0,
                            expiry_s=None,
                            htf_bias="bull",
                            shadow_only=True,
                            trigger_name="m5_ob_fib_long",
                        )
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("watchdog_shadow", sug.setup_tags)
        self.assertFalse(sug.is_trade())


class DecisionLogPersistTests(unittest.TestCase):
    def test_log_decision_stores_tags_and_charts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "ledger.db"
            with patch.object(paper.config, "LEDGER_DB", str(db)):
                paper.init_db()
                sug = KalshiSuggestion.skip(
                    series="KXETH15M",
                    market_ticker="T",
                    product_id="ETH",
                    rationale="HTF bias is bullish. skipped test",
                    mid_cents=48.0,
                    fair_yes_cents=60.0,
                    edge_cents=12.0,
                )
                sug.setup_tags = ["htf_bull", "aligned_htf"]
                sug.skip_codes = ["ict_no_trade"]
                sug.structure_chart_path = "/tmp/h4.png"
                sug.entry_chart_path = "/tmp/m5.png"
                sug.h1_bias_tag = "bull"
                did = paper.log_decision(sug)
                self.assertGreater(did, 0)
                rows = paper.get_decisions(limit=1)
                self.assertEqual(len(rows), 1)
                self.assertIn("htf_bull", rows[0]["setup_tags"] or "")
                self.assertEqual(rows[0]["structure_chart_path"], "/tmp/h4.png")


class StructureStateTests(unittest.TestCase):
    def test_refresh_persists(self) -> None:
        ctx = MagicMock()
        ctx.setup_tags = ["htf_bull", "m5_ob_bullish_in_fib"]
        ctx.alerts = ["watching demand"]
        ctx.spot = 3000.0
        ctx.range_24h = None
        ctx.is_ranging = False
        ctx.range_break = None
        ctx.setup_state = None
        ctx.order_blocks = []
        ctx.zone_snapshot = None

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            with patch("patterns.signal_state.config.LEDGER_DB", str(db)):
                with patch("patterns.market_structure_state.get_state") as gs:
                    with patch("patterns.market_structure_state.set_state") as ss:
                        gs.return_value = None
                        state = refresh_from_context(
                            ctx,
                            product_id="ETH-USD",
                            market_ticker="KXETH15M-1",
                            marked_paths={"H4": "/charts/h4.png", "M5": "/charts/m5.png"},
                            htf_bias="bull",
                        )
                        self.assertEqual(state.htf_bias, "bull")
                        self.assertTrue(state.window_thesis)
                        ss.assert_called_once()


class KalshiCriticSoftHtfTests(unittest.TestCase):
    def test_counter_htf_unacknowledged(self) -> None:
        import kalshi_critic
        from models import Suggestion

        sug = Suggestion(
            action="spot_buy",
            size=0,
            entry=100.0,
            stop_loss=99.0,
            take_profits=[],
            rationale="KalshiRules session: us_rth. On H4 supply; on M5 fib. Buy YES.",
        )
        findings = kalshi_critic.check_kalshi_rationale(
            sug.rationale,
            sug,
            htf_bias="bear",
        )
        codes = {f.code for f in findings}
        self.assertIn("KALSHI_COUNTER_HTF_UNACKNOWLEDGED", codes)

    def test_counter_htf_acknowledged_ok(self) -> None:
        import kalshi_critic
        from models import Suggestion

        sug = Suggestion(
            action="spot_buy",
            size=0,
            entry=100.0,
            stop_loss=99.0,
            take_profits=[],
            rationale=(
                "KalshiRules session: us_rth. On H4 bearish supply — despite HTF "
                "bear conflict, M5 bullish SFP reclaim takes precedence. On H1 mixed; "
                "on M5 fib entry. Still favor YES."
            ),
        )
        findings = kalshi_critic.check_kalshi_rationale(
            sug.rationale,
            sug,
            htf_bias="bear",
        )
        codes = {f.code for f in findings}
        self.assertNotIn("KALSHI_COUNTER_HTF_UNACKNOWLEDGED", codes)


if __name__ == "__main__":
    unittest.main()
