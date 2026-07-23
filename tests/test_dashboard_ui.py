"""UI smoke checks for the multi-bot Kalshi paper dashboard."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class DashboardUiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmpdir.name)
        db = root / "ledger.db"
        charts = root / "charts"
        charts.mkdir()

        import config
        import paper

        self._patches = [
            patch.object(config, "LEDGER_DB", db),
            patch.object(config, "CHARTS_DIR", charts),
            patch.object(config, "ROOT_DIR", root),
        ]
        for p in self._patches:
            p.start()
        paper.init_db()

        from dashboard.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        for p in reversed(self._patches):
            p.stop()
        self._tmpdir.cleanup()

    def test_trade_cards_use_button_accordion_collapsed(self) -> None:
        import paper
        from models import KalshiSuggestion

        paper.log_decision(
            KalshiSuggestion.skip(
                series="KXBTC15M",
                market_ticker="KXBTC15M-UI",
                product_id="BTC",
                rationale="ui smoke skip",
                bot_id="control",
            )
        )
        html = self.client.get("/").text
        self.assertNotIn("<details", html)
        self.assertNotIn("<summary", html)
        self.assertIn("Bot leaderboard", html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('class="trade-body" hidden', html)
        self.assertIn("Decision journal", html)
        self.assertIn("ui smoke skip", html)

    def test_bot_tabs_and_leaderboard(self) -> None:
        html = self.client.get("/?bot=lottery").text
        self.assertIn("lottery", html.lower())
        self.assertIn("Control", html)
        self.assertIn("Lottery", html)
        self.assertIn("Adverse", html)


if __name__ == "__main__":
    unittest.main()
