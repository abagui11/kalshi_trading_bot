"""Unit tests for Kalshi ICT mapping / fib gate (no live Claude)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import kalshi_ict
from models import Suggestion
from patterns.market_context import MarketContext


class TestKalshiIctMapping(unittest.TestCase):
    def test_long_maps_to_yes(self) -> None:
        self.assertEqual(kalshi_ict.ict_action_to_side("spot_buy"), "YES")
        self.assertEqual(kalshi_ict.ict_bias_label("spot_buy"), "long")

    def test_short_maps_to_no(self) -> None:
        self.assertEqual(kalshi_ict.ict_action_to_side("spot_sell"), "NO")
        self.assertEqual(kalshi_ict.ict_bias_label("deriv_sell"), "short")

    def test_no_trade_maps_none(self) -> None:
        self.assertIsNone(kalshi_ict.ict_action_to_side("no_trade"))
        self.assertEqual(kalshi_ict.ict_bias_label("no_trade"), "none")


class TestKalshiIctGate(unittest.TestCase):
    def _ob(self, low: float = 100.0, high: float = 110.0) -> dict:
        return {
            "low": low,
            "high": high,
            "start_ts": "2026-01-01T00:00:00Z",
            "end_ts": "2026-01-01T00:05:00Z",
        }

    def test_no_trade_passes(self) -> None:
        s = Suggestion.no_trade("flat", product_id="BTC-USD")
        out = kalshi_ict.validate_kalshi_ict_suggestion(s, None)
        self.assertEqual(out.action, "no_trade")

    def test_rejects_spot_outside_fib_without_sfp(self) -> None:
        # Bullish fib band for 100-110: 102.5–105.0
        suggestion = Suggestion(
            action="spot_buy",
            size=0,
            entry=103.0,
            stop_loss=99.0,
            take_profits=[120.0],
            order_block=self._ob(),
            product_id="BTC-USD",
            decision_charts=["H4", "M5"],
            structure_chart="H4",
            entry_chart="M5",
        )
        ctx = MarketContext(
            range_24h=None,
            is_ranging=False,
            range_break=None,
            spot=108.0,  # outside fib
            zone_snapshot=None,
            setup_state=None,
            order_blocks=[],
            m5_sfps=[],
        )
        # Soft mode keeps action (gate logged separately).
        soft = kalshi_ict.validate_kalshi_ict_suggestion(suggestion, ctx, soft=True)
        self.assertEqual(soft.action, "spot_buy")
        self.assertEqual(kalshi_ict.evaluate_gate_outcome(suggestion, ctx), "fail")
        # Hard mode still raises.
        with self.assertRaises(ValueError):
            kalshi_ict.validate_kalshi_ict_suggestion(suggestion, ctx, soft=False)


if __name__ == "__main__":
    unittest.main()
