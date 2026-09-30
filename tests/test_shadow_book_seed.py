"""The shadow books' ledger seed is per-book, and is not the sizing bankroll.

Two things are pinned here. First, the eight wick-derivative books start
(and re-start, after a reset) on their own seed rather than the $225
baseline — a reset quietly dropping one back would restate its
percent-of-seed column. Second, that seed is a *ledger* figure only: it must
never reach kalshi_sizing, because the sizing bankroll is shared with the
live book and moving it would resize real orders.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import kalshi_sizing
import paper


class ShadowBookSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patches = [
            patch.object(config, "LEDGER_DB", Path(self._tmp.name) / "e.db"),
            patch.object(bot_config, "KALSHI_BANKROLL_USD", 225.0),
            patch.object(bot_config, "SHADOW_BOOK_SEED_USD", 1000.0),
            patch.object(bot_config, "KALSHI_DEPLOY_PCT", 0.06),
            patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 34.88),
            patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 16),
            patch.object(bot_config, "KALSHI_BOT_MAX_CONTRACTS", {}),
            patch.object(bot_config, "KALSHI_USE_LIVE_BALANCE", False),
        ]
        for p in self._patches:
            p.start()
        paper.init_db()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def test_all_eight_derivative_books_carry_the_shadow_seed(self) -> None:
        expected = set(bot_config.HOURLY_WICK_BOTS) | set(
            bot_config.CROSS_WICK_BOTS
        )
        self.assertEqual(set(bot_config.SHADOW_SEED_BOTS), expected)
        self.assertEqual(len(expected), 8)
        for bot_id in expected:
            self.assertAlmostEqual(bot_config.book_seed_usd(bot_id), 1000.0)

    def test_other_books_keep_the_baseline_seed(self) -> None:
        for bot_id in ("eva_wick", "eva_streak", "eva_arb", "eva_wick_sol"):
            self.assertAlmostEqual(bot_config.book_seed_usd(bot_id), 225.0)
        self.assertAlmostEqual(bot_config.book_seed_usd(None), 225.0)

    def test_a_new_shadow_book_row_opens_at_the_shadow_seed(self) -> None:
        self.assertAlmostEqual(
            paper.available_cash("eva_wick_btc_sol"), 1000.0
        )
        self.assertAlmostEqual(paper.available_cash("eva_wick"), 225.0)

    def test_reset_book_does_not_drop_a_shadow_book_to_the_baseline(self) -> None:
        paper.reset_book(bot_id="eva_wick_1h_ladder")
        self.assertAlmostEqual(
            paper.available_cash("eva_wick_1h_ladder"), 1000.0
        )

    def test_reset_all_books_seeds_each_one_on_its_own_figure(self) -> None:
        with patch.object(
            bot_config, "ENABLED_BOTS", ("eva_wick", "eva_wick_eth_hype")
        ):
            paper.reset_book()
        self.assertAlmostEqual(paper.available_cash("eva_wick"), 225.0)
        self.assertAlmostEqual(
            paper.available_cash("eva_wick_eth_hype"), 1000.0
        )

    def test_an_explicit_starting_usd_still_wins(self) -> None:
        paper.reset_book(500.0, bot_id="eva_wick_btc_xrp")
        self.assertAlmostEqual(paper.available_cash("eva_wick_btc_xrp"), 500.0)

    def test_the_shadow_seed_never_reaches_sizing(self) -> None:
        """Per-trade size is the wick family's, not a function of the seed."""
        self.assertAlmostEqual(kalshi_sizing.sizing_bankroll_usd(), 225.0)
        base, _ = kalshi_sizing.contracts_for_entry(70.0, bot_id="eva_wick")
        shadow, _ = kalshi_sizing.contracts_for_entry(
            70.0, bot_id="eva_wick_btc_xrp"
        )
        self.assertEqual(base, shadow)


if __name__ == "__main__":
    unittest.main()
