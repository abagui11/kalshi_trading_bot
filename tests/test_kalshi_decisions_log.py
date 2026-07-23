"""Tests for kalshi_decisions logging and real edge (no circular stamp)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import kalshi_fair
import paper
from models import KalshiSuggestion


class TestKalshiDecisionsLog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self._tmp.name) / "test_ledger.db"
        self._ledger_patch = patch.object(config, "LEDGER_DB", self.db)
        self._ledger_patch.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._ledger_patch.stop()
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def test_log_skip_and_trade(self) -> None:
        skip = KalshiSuggestion.skip(
            series="KXBTC15M",
            market_ticker="KXBTC15M-TEST-SKIP",
            product_id="BTC",
            rationale="skipped: test",
            mid_cents=50.0,
            fair_yes_cents=51.0,
            edge_cents=1.0,
            gate_outcome="fail",
            would_skip_reasons=["gate_fail"],
        )
        did = paper.log_decision(skip)
        self.assertGreater(did, 0)

        trade = KalshiSuggestion(
            series="KXBTC15M",
            market_ticker="KXBTC15M-TEST-TRADE",
            side="YES",
            contracts=2,
            entry_cents=45.0,
            expiry_ts="2026-07-23T20:00:00Z",
            rationale="trigger test",
            product_id="BTC",
            mid_cents=45.0,
            fair_yes_cents=58.0,
            edge_cents=13.0,
            gate_outcome="pass_fib",
            trigger_type="short_horizon",
            opened=True,
            position_id=1,
            chart_path="/tmp/chart.png",
        )
        paper.log_decision(trade)
        rows = paper.get_decisions(limit=10)
        self.assertGreaterEqual(len(rows), 2)
        sides = {r["side"] for r in rows}
        self.assertIn("SKIP", sides)
        self.assertIn("YES", sides)

    def test_open_trade_chart_path_column(self) -> None:
        sug = KalshiSuggestion(
            series="KXETH15M",
            market_ticker="KXETH15M-TEST-OPEN",
            side="NO",
            contracts=1,
            entry_cents=40.0,
            expiry_ts="2026-07-23T20:15:00Z",
            rationale="open test",
            product_id="ETH",
            mid_cents=60.0,
        )
        opened = paper.open_trade(sug)
        self.assertIsNotNone(opened)
        assert opened is not None
        paper.set_position_chart_path(int(opened["id"]), "charts/test.png")
        pos = paper.get_open_positions()[0]
        self.assertEqual(pos.get("chart_path"), "charts/test.png")


class TestNoCircularEdge(unittest.TestCase):
    def test_side_must_agree_helpers(self) -> None:
        self.assertTrue(kalshi_fair.has_min_edge(8.0, 8.0))
        self.assertTrue(kalshi_fair.side_agrees_with_edge("YES", 8.0))
        self.assertTrue(kalshi_fair.side_agrees_with_edge("NO", -8.0))
        self.assertFalse(kalshi_fair.side_agrees_with_edge("YES", -8.0))

    def test_contract_price_math(self) -> None:
        def price(side: str, yes_mid: float) -> float:
            if side.upper() == "YES":
                return max(1.0, min(99.0, yes_mid))
            return max(1.0, min(99.0, 100.0 - yes_mid))

        self.assertEqual(price("YES", 42.0), 42.0)
        self.assertEqual(price("NO", 42.0), 58.0)


if __name__ == "__main__":
    unittest.main()
