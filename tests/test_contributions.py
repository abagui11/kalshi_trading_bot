"""Tests for shared paper-book user contributions (Fund / My Metrics)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import paper


class ContributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_path = Path(self._tmpdir.name) / "test_ledger.db"
        self._config_patch = patch.object(config, "LEDGER_DB", self._db_path)
        self._config_patch.start()
        self._portfolio_patch = patch.object(config, "PAPER_PORTFOLIO_VALUE", 5000.0)
        self._portfolio_patch.start()
        self._contrib_patch = patch.object(bot_config, "PAPER_CONTRIBUTION_USD", 1000.0)
        self._contrib_patch.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._contrib_patch.stop()
        self._portfolio_patch.stop()
        self._config_patch.stop()
        self._tmpdir.cleanup()

    def test_house_seed_counts_as_contribution(self) -> None:
        # init_db seeds house row equal to starting book.
        self.assertAlmostEqual(paper.total_contributed(), 5000.0, places=2)

    def test_fund_user_adds_deposit_once(self) -> None:
        result = paper.fund_user(111, username="alice")
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["amount_usd"], 1000.0, places=2)
        self.assertAlmostEqual(result["total_contributed_usd"], 6000.0, places=2)

        state = paper.get_state()
        self.assertAlmostEqual(float(state["cash_usd"]), 6000.0, places=2)
        self.assertAlmostEqual(float(state["starting_usd"]), 6000.0, places=2)

    def test_fund_user_is_idempotent(self) -> None:
        paper.fund_user(111, username="alice")
        again = paper.fund_user(111, username="alice")
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason"], "already_funded")
        # No double deposit.
        self.assertAlmostEqual(paper.total_contributed(), 6000.0, places=2)

    def test_user_metrics_share_and_pnl_flat_book(self) -> None:
        paper.fund_user(111, username="alice")
        metrics = paper.get_user_metrics(111, spots={"ETH-USD": 2000.0, "BTC-USD": 40000.0})
        self.assertTrue(metrics["ok"])
        # $1000 of a $6000 book with no open trades => ~16.67% share, equity ~= deposit.
        self.assertAlmostEqual(metrics["share_pct"], 1000.0 / 6000.0 * 100, places=2)
        self.assertAlmostEqual(metrics["equity_usd"], 1000.0, places=2)
        self.assertAlmostEqual(metrics["pnl_usd"], 0.0, places=2)

    def test_user_metrics_not_funded(self) -> None:
        metrics = paper.get_user_metrics(999)
        self.assertFalse(metrics["ok"])


if __name__ == "__main__":
    unittest.main()
