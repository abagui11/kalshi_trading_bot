"""Unit tests for Telegram beta keyboard and paper-funding copy."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import bot_config
import config
from telegram_ui import (
    CB_FUND,
    format_fund_result,
    format_metrics_message,
    main_keyboard,
)


class TelegramUiTests(unittest.TestCase):
    def test_main_keyboard_has_fund_button(self) -> None:
        with patch.object(config, "DASHBOARD_PUBLIC_URL", "https://dash.example"):
            keyboard = main_keyboard()

        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
        ]
        fund = next(button for button in buttons if button.text == "Fund")
        self.assertEqual(fund.callback_data, CB_FUND)
        portfolio = next(button for button in buttons if button.text == "Portfolio")
        self.assertEqual(portfolio.url, "https://dash.example")

    def test_format_fund_result_for_success_and_repeat(self) -> None:
        success = format_fund_result(
            {
                "ok": True,
                "amount": 1000.0,
                "share_pct": 16.6667,
                "cash_usd": 6000.0,
                "total_contributed_usd": 6000.0,
            }
        )
        self.assertIn("Funded (paper)", success)
        self.assertIn("Added $1,000", success)
        self.assertIn("16.67%", success)
        self.assertIn("nothing left your wallet", success)

        repeat = format_fund_result(
            {
                "ok": False,
                "reason": "already_funded",
                "amount": 1000.0,
                "share_pct": 16.6667,
            }
        )
        self.assertIn("Already funded", repeat)
        self.assertIn("$1,000", repeat)
        self.assertIn("16.67%", repeat)

    def test_format_metrics_message(self) -> None:
        message = format_metrics_message(
            {
                "ok": True,
                "amount_usd": 1000.0,
                "share_pct": 16.6667,
                "equity_usd": 1100.0,
                "pnl_usd": 100.0,
                "pnl_pct": 10.0,
                "portfolio_equity_usd": 6600.0,
                "total_contributed_usd": 6000.0,
            }
        )
        self.assertIn("My Metrics (paper)", message)
        self.assertIn("Ownership: 16.67%", message)
        self.assertIn("Your equity: $1,100.00", message)
        self.assertIn("Your PnL: $+100.00 (+10.00%)", message)

    def test_format_metrics_message_before_funding(self) -> None:
        with patch.object(bot_config, "PAPER_CONTRIBUTION_USD", 1000.0):
            message = format_metrics_message(
                {"ok": False, "reason": "not_funded"}
            )
        self.assertIn("not Funded yet", message)
        self.assertIn("$1,000", message)


if __name__ == "__main__":
    unittest.main()
