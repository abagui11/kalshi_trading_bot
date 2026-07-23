"""Tests for Kalshi rationale critic checks."""

from __future__ import annotations

import unittest

import kalshi_critic
from models import Suggestion


class TestKalshiCritic(unittest.TestCase):
    def test_coin_flip_on_trade(self) -> None:
        s = Suggestion(
            action="spot_buy",
            size=0,
            entry=100.0,
            stop_loss=99.0,
            take_profits=[101.0],
            rationale="Nearly a coin flip; mid looks rich.",
            product_id="BTC-USD",
        )
        findings = kalshi_critic.check_kalshi_rationale(s.rationale, s)
        codes = {f.code for f in findings}
        self.assertIn("KALSHI_COIN_FLIP_RATIONALE", codes)

    def test_no_coin_flip_on_no_trade(self) -> None:
        s = Suggestion.no_trade("Nearly a coin flip — skip.", product_id="BTC-USD")
        findings = kalshi_critic.check_kalshi_rationale(s.rationale, s)
        self.assertEqual(findings, [])

    def test_fabricated_edge(self) -> None:
        s = Suggestion(
            action="spot_sell",
            size=0,
            entry=100.0,
            stop_loss=101.0,
            take_profits=[99.0],
            rationale="Market overpriced by 7 cents versus fair value.",
            product_id="ETH-USD",
        )
        findings = kalshi_critic.check_kalshi_rationale(
            s.rationale,
            s,
            model_fair_yes_cents=50.0,
            yes_mid_cents=49.0,
        )
        codes = {f.code for f in findings}
        self.assertIn("KALSHI_FABRICATED_EDGE", codes)

    def test_tiny_move_coin_flip(self) -> None:
        s = Suggestion(
            action="spot_buy",
            size=0,
            entry=1.0,
            stop_loss=0.9,
            take_profits=[1.1],
            rationale="Needs to rally ~0.03% in the remaining window.",
            product_id="BTC-USD",
        )
        findings = kalshi_critic.check_kalshi_rationale(s.rationale, s)
        self.assertTrue(any(f.code == "KALSHI_COIN_FLIP_RATIONALE" for f in findings))


if __name__ == "__main__":
    unittest.main()
