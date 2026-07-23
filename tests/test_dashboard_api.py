"""Tests for Kalshi ICT dashboard API (structure + journal)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmpdir.name)
        self._db = root / "ledger.db"
        self._charts = root / "charts"
        self._charts.mkdir()

        import config
        import paper

        self._config_db = patch.object(config, "LEDGER_DB", self._db)
        self._config_charts = patch.object(config, "CHARTS_DIR", self._charts)
        self._config_root = patch.object(config, "ROOT_DIR", root)
        self._config_db.start()
        self._config_charts.start()
        self._config_root.start()
        paper.init_db()

        from dashboard.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self._config_root.stop()
        self._config_charts.stop()
        self._config_db.stop()
        self.client.close()
        self._tmpdir.cleanup()

    def test_index_ok(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Market structure", resp.text)
        self.assertIn("Decision journal", resp.text)
        self.assertIn("Bot leaderboard", resp.text)

    def test_api_status(self) -> None:
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data["bot"], ("kalshi_15m", "kalshi_15m_multi"))
        self.assertIn("watchdog_enabled", data)
        self.assertIn("enabled_bots", data)

    def test_api_bots(self) -> None:
        resp = self.client.get("/api/bots")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("bots", data)
        self.assertGreaterEqual(len(data["bots"]), 1)

    def test_api_structure(self) -> None:
        resp = self.client.get("/api/structure")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("assets", data)
        self.assertGreaterEqual(len(data["assets"]), 1)
        self.assertIn("htf_bias", data["assets"][0])

    def test_api_journal(self) -> None:
        resp = self.client.get("/api/journal?filter=skips")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("decisions", resp.json())

    def test_index_skips_filter(self) -> None:
        resp = self.client.get("/?filter=skips")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Skips only", resp.text)


if __name__ == "__main__":
    unittest.main()
