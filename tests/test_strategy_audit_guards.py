"""Tests for audit-era strategy guards: pulse SL ratchet, macro note, execute flag."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import bot_config
import config
import paper
from models import Suggestion
from patterns.market_context import MarketContext
from patterns.zone_resolver import ZoneSnapshot


class WatchdogExecuteFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = self._tmpdir.name + "/ledger.db"
        self._patch = patch.object(config, "LEDGER_DB", self._db)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_runtime_override(self) -> None:
        with patch.object(bot_config, "WATCHDOG_EXECUTE_ENABLED", False):
            self.assertFalse(bot_config.watchdog_execute_enabled())
            bot_config.set_watchdog_execute_enabled(True)
            self.assertTrue(bot_config.watchdog_execute_enabled())
            bot_config.set_watchdog_execute_enabled(False)
            self.assertFalse(bot_config.watchdog_execute_enabled())


class MacroNoteAuditTests(unittest.TestCase):
    def test_missing_macro_note_flagged(self) -> None:
        from critic import _check_macro_note

        ctx = MarketContext(
            range_24h=None,
            is_ranging=True,
            range_break=None,
            spot=2000.0,
            zone_snapshot=ZoneSnapshot(
                spot=2000.0,
                zones_containing_price=[],
                primary_bullish=None,
                primary_bearish=None,
                nearest_bearish_above=None,
                nearest_bullish_below=None,
                bearish_retest_low=None,
                bearish_retest_high=None,
            ),
            setup_state=None,
            alerts=[],
            h4_sfps=[],
            m5_sfps=[],
            live_invalidated_sfps=[],
            order_blocks=[],
            htf_zones=[],
            key_levels_near=[],
            setup_tags=[],
            summary_text="=== Macro context (advisory — chart structure is primary) ===\nActive",
        )
        suggestion = Suggestion(
            action="spot_buy",
            size=100,
            entry=2000.0,
            stop_loss=1980.0,
            take_profits=[2040.0],
            rationale="Long on M5 OB fib.",
            macro_note=None,
        )
        finding = _check_macro_note(ctx, suggestion)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.code, "MACRO_NOTE_MISSING")

        suggestion.macro_note = "Iran headlines not material for this M5 long."
        self.assertIsNone(_check_macro_note(ctx, suggestion))


class PulseTightenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = self._tmpdir.name + "/ledger.db"
        self._patch = patch.object(config, "LEDGER_DB", self._db)
        self._patch.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_tighten_sl_ratchets_toward_entry(self) -> None:
        suggestion = Suggestion(
            action="spot_buy",
            size=500.0,
            entry=2000.0,
            stop_loss=1960.0,
            take_profits=[2060.0, 2100.0],
            risk_reward=1.5,
            rationale="test long",
            product_id="ETH-USD",
            order_block={"low": 1980, "high": 2010, "start_ts": "t", "end_ts": "t"},
        )
        with patch.object(paper, "_open_eth_qty", return_value=0.25):
            paper.update(suggestion, 2000.0, cycle_id="TEST_PULSE", spots={"ETH-USD": 2000.0})
        positions = paper.get_open_positions(spots={"ETH-USD": 2000.0})
        self.assertEqual(len(positions), 1)
        old_sl = float(positions[0]["stop_loss"])
        applied = paper.tighten_stops_from_pulse(
            recommendation="tighten_sl",
            spots={"ETH-USD": 2000.0},
            event_id=1,
        )
        self.assertEqual(len(applied), 1)
        self.assertGreater(applied[0]["new_sl"], old_sl)
        self.assertLess(applied[0]["new_sl"], 2000.0)


if __name__ == "__main__":
    unittest.main()
