"""Live sizing safety caps."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import bot_config
import kalshi_sizing


class KalshiSizingTests(unittest.TestCase):
    def test_clamp_respects_max_contracts(self) -> None:
        with patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 5):
            with patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 50.0):
                self.assertEqual(kalshi_sizing.clamp_contracts(100, 50.0), 5)

    def test_clamp_respects_max_notional(self) -> None:
        with patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 100):
            with patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 5.0):
                # 50¢ → $0.50/ct → max 10 ct by notional
                self.assertEqual(kalshi_sizing.clamp_contracts(100, 50.0), 10)

    def test_sizing_bankroll_never_exceeds_configured(self) -> None:
        with patch.object(bot_config, "KALSHI_BANKROLL_USD", 76.47):
            with patch.object(bot_config, "KALSHI_USE_LIVE_BALANCE", True):
                with patch("config.KALSHI_PAPER_ONLY", False):
                    with patch("kalshi_client.get_balance", return_value={"balance_dollars": "5000"}):
                        self.assertAlmostEqual(
                            kalshi_sizing.sizing_bankroll_usd(), 76.47, places=2
                        )

    def test_assert_rejects_oversized(self) -> None:
        with patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 5):
            with patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 5.0):
                with self.assertRaises(ValueError):
                    kalshi_sizing.assert_order_allowed(50, 50.0)


if __name__ == "__main__":
    unittest.main()
