"""Tests for Kalshi fair-value model (no network)."""

from __future__ import annotations

import math
import unittest

import kalshi_fair


class TestPhiFairValue(unittest.TestCase):
    def test_tiny_gap_long_tau_near_coin_flip(self) -> None:
        # 0.03% above strike, 14 minutes, modest vol → ~50¢
        spot = 100.03
        strike = 100.0
        tau = 14 * 60
        # sigma such that sigma*sqrt(tau) ≈ 0.002 (0.2%)
        sigma = 0.002 / math.sqrt(tau)
        fv = kalshi_fair.fair_yes_cents(spot, strike, tau, sigma)
        self.assertGreater(fv.fair_yes_cents, 45.0)
        self.assertLess(fv.fair_yes_cents, 60.0)

    def test_large_gap_short_tau_extreme(self) -> None:
        spot = 102.0  # +2%
        strike = 100.0
        tau = 60.0  # 1 minute
        sigma = 0.0001  # very low vol
        fv = kalshi_fair.fair_yes_cents(spot, strike, tau, sigma)
        self.assertGreaterEqual(fv.fair_yes_cents, 90.0)

    def test_below_strike_favors_no(self) -> None:
        fv = kalshi_fair.fair_yes_cents(99.0, 100.0, 60.0, 0.0001)
        self.assertLessEqual(fv.fair_yes_cents, 10.0)

    def test_edge_vs_mid(self) -> None:
        fv = kalshi_fair.fair_yes_cents(100.5, 100.0, 600.0, 0.00005)
        edge = fv.edge_cents(50.0)
        self.assertAlmostEqual(edge, fv.fair_yes_cents - 50.0, places=6)

    def test_min_edge_and_side_agree(self) -> None:
        self.assertTrue(kalshi_fair.has_min_edge(8.5, 8.0))
        self.assertFalse(kalshi_fair.has_min_edge(3.0, 8.0))
        self.assertTrue(kalshi_fair.side_agrees_with_edge("YES", 10.0))
        self.assertFalse(kalshi_fair.side_agrees_with_edge("YES", -10.0))
        self.assertTrue(kalshi_fair.side_agrees_with_edge("NO", -10.0))
        self.assertFalse(kalshi_fair.side_agrees_with_edge("NO", 10.0))

    def test_m5_sigma_from_bars(self) -> None:
        bars = [{"close": 100.0 + i * 0.1} for i in range(15)]
        sig = kalshi_fair.m5_log_return_sigma(bars, lookback=12)
        self.assertGreater(sig, 0.0)

    def test_prior_return(self) -> None:
        bars = [{"close": 100.0}, {"close": 101.0}, {"close": 102.0}]
        self.assertAlmostEqual(kalshi_fair.prior_return_pct(bars, 1) or 0.0, (102 / 101 - 1) * 100, places=6)
        self.assertAlmostEqual(kalshi_fair.prior_return_pct(bars, 2) or 0.0, (102 / 100 - 1) * 100, places=6)


if __name__ == "__main__":
    unittest.main()
