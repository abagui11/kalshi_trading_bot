"""Tests for one-time paper funding and proportional user metrics."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import paper


class PaperContributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_path = Path(self._tmpdir.name) / "test_ledger.db"
        self._patches = [
            patch.object(config, "LEDGER_DB", self._db_path),
            patch.object(config, "PAPER_PORTFOLIO_VALUE", 5000.0),
            patch.object(bot_config, "PAPER_CONTRIBUTION_USD", 1000.0),
            patch.object(bot_config, "HOUSE_CONTRIBUTION_TELEGRAM_ID", 0),
        ]
        for item in self._patches:
            item.start()
        paper.init_db()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._tmpdir.cleanup()

    def test_fund_user_once_then_reports_already_funded(self) -> None:
        first = paper.fund_user(12345, "alice")
        self.assertTrue(first["ok"])
        self.assertEqual(first["amount"], 1000.0)
        self.assertEqual(first["cash_usd"], 6000.0)
        self.assertEqual(first["total_contributed_usd"], 6000.0)
        self.assertAlmostEqual(first["share_pct"], 1000 / 6000 * 100)

        second = paper.fund_user(12345, "changed-name")
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "already_funded")
        self.assertEqual(second["amount"], 1000.0)
        self.assertAlmostEqual(second["share_pct"], first["share_pct"])

        contribution = paper.get_contribution(12345)
        assert contribution is not None
        self.assertEqual(contribution["username"], "alice")
        self.assertEqual(len(paper.list_contributions()), 2)  # house + user

    def test_get_user_metrics_uses_share_of_live_book_equity(self) -> None:
        paper.fund_user(12345, "alice")

        # Simulate a $600 gain in the shared book without adding a contribution.
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE paper_state SET cash_usd = 6600 WHERE id = 1"
            )
            conn.commit()

        metrics = paper.get_user_metrics(
            12345,
            spots={"ETH-USD": 3000.0, "BTC-USD": 60000.0},
        )
        self.assertTrue(metrics["ok"])
        self.assertAlmostEqual(metrics["share_pct"], 1000 / 6000 * 100)
        self.assertEqual(metrics["portfolio_equity_usd"], 6600.0)
        self.assertAlmostEqual(metrics["equity_usd"], 1100.0)
        self.assertAlmostEqual(metrics["pnl_usd"], 100.0)
        self.assertAlmostEqual(metrics["pnl_pct"], 10.0)

    def test_get_user_metrics_requires_funding(self) -> None:
        result = paper.get_user_metrics(
            999,
            spots={"ETH-USD": 3000.0, "BTC-USD": 60000.0},
        )
        self.assertEqual(result, {"ok": False, "reason": "not_funded"})


if __name__ == "__main__":
    unittest.main()
