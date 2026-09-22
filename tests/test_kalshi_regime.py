"""Regime gates: weekend pause, streak-only cooldown, chop stays shadow."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import kalshi_regime
import paper

SAT = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
MON = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)


class TestWeekendPause(unittest.TestCase):
    def test_directional_books_pause_on_saturday(self) -> None:
        self.assertTrue(kalshi_regime.weekend_pause("eva_streak", now=SAT))
        self.assertTrue(kalshi_regime.weekend_pause("eva_wick", now=SAT))

    def test_weekday_trades(self) -> None:
        self.assertFalse(kalshi_regime.weekend_pause("eva_streak", now=MON))

    def test_arb_is_never_paused(self) -> None:
        """Arb is positive in both regimes and is deliberately left alone."""
        self.assertFalse(kalshi_regime.weekend_pause("eva_arb", now=SAT))

    def test_clock_failure_fails_open(self) -> None:
        with mock.patch("kalshi_regime.datetime") as dt:
            dt.now.side_effect = RuntimeError("no clock")
            self.assertFalse(kalshi_regime.weekend_pause("eva_streak"))


class TestStreakCooldown(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paper.set_db_path(Path(self._tmp.name) / "kalshi.db")
        paper.init_db()

    def tearDown(self) -> None:
        paper.set_db_path(None)
        try:
            self._tmp.cleanup()
        except PermissionError:
            pass

    _seq = 0

    def _settle(self, pnl: float, closed_at: datetime, product="BTC-USD") -> None:
        # One market per 15m window in reality, and the table enforces it:
        # UNIQUE(bot_id, market_ticker). Each settled fixture gets its own.
        TestStreakCooldown._seq += 1
        ticker = f"T-{TestStreakCooldown._seq}"
        with paper._connect() as conn:
            conn.execute(
                "INSERT INTO paper_positions (bot_id, opened_at, series, "
                "market_ticker, product_id, side, contracts, entry_cents, "
                "expiry_ts, rationale, status, pnl_usd, closed_at) "
                "VALUES ('eva_streak', ?, 'KXBTC15M', ?, ?, 'YES', 1, 50, "
                "?, '', 'settled', ?, ?)",
                (closed_at.isoformat(), ticker, product,
                 closed_at.isoformat(), pnl, closed_at.isoformat()),
            )
            conn.commit()

    def test_three_straight_losses_pause_the_product(self) -> None:
        now = MON
        for i in range(3):
            self._settle(-1.0, now - timedelta(minutes=30 - i * 5))
        reason = kalshi_regime.streak_loss_cooldown("BTC-USD", now=now)
        self.assertIsNotNone(reason)
        self.assertIn("loss_cooldown", reason)

    def test_a_win_inside_the_run_resets_it(self) -> None:
        now = MON
        self._settle(-1.0, now - timedelta(minutes=40))
        self._settle(+2.0, now - timedelta(minutes=30))
        self._settle(-1.0, now - timedelta(minutes=20))
        self._settle(-1.0, now - timedelta(minutes=10))
        self.assertIsNone(kalshi_regime.streak_loss_cooldown("BTC-USD", now=now))

    def test_cooldown_expires(self) -> None:
        now = MON
        for i in range(3):
            self._settle(-1.0, now - timedelta(hours=5) + timedelta(minutes=i))
        self.assertIsNone(kalshi_regime.streak_loss_cooldown("BTC-USD", now=now))

    def test_other_product_is_not_gated(self) -> None:
        now = MON
        for i in range(3):
            self._settle(-1.0, now - timedelta(minutes=30 - i), product="BTC-USD")
        self.assertIsNone(kalshi_regime.streak_loss_cooldown("ETH-USD", now=now))

    def test_query_failure_fails_open(self) -> None:
        with mock.patch.object(paper, "_connect",
                               side_effect=RuntimeError("locked")):
            self.assertIsNone(
                kalshi_regime.streak_loss_cooldown("BTC-USD", now=MON))


class TestDailyLossStop(unittest.TestCase):
    """25% of a $200 day bankroll = a $50 limit, live books only."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paper.set_db_path(Path(self._tmp.name) / "kalshi.db")
        paper.init_db()
        kalshi_regime._day_bankroll.clear()
        self._patches = [
            mock.patch.object(kalshi_regime, "DAILY_STOP_PCT", 0.25),
            mock.patch("kalshi_regime.bot_config.bot_is_live",
                       side_effect=lambda b: b == "eva_wick"),
            mock.patch("kalshi_sizing.sizing_bankroll_usd", return_value=200.0),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        kalshi_regime._day_bankroll.clear()
        paper.set_db_path(None)
        try:
            self._tmp.cleanup()
        except PermissionError:
            pass

    _seq = 0

    def _settle(self, pnl: float, closed_at: datetime, bot="eva_wick") -> None:
        TestDailyLossStop._seq += 1
        with paper._connect() as conn:
            conn.execute(
                "INSERT INTO paper_positions (bot_id, opened_at, series, "
                "market_ticker, product_id, side, contracts, entry_cents, "
                "expiry_ts, rationale, status, pnl_usd, closed_at) "
                "VALUES (?, ?, 'KXBTC15M', ?, 'BTC-USD', 'YES', 8, 73, "
                "?, '', 'settled', ?, ?)",
                (bot, closed_at.isoformat(), f"D-{TestDailyLossStop._seq}",
                 closed_at.isoformat(), pnl, closed_at.isoformat()),
            )
            conn.commit()

    def test_losing_the_limit_stops_the_live_bot(self) -> None:
        for i in range(8):
            self._settle(-6.4, MON - timedelta(minutes=10 * (i + 1)))
        reason = kalshi_regime.daily_loss_stop("eva_wick", now=MON)
        self.assertIsNotNone(reason)
        self.assertIn("daily_loss_stop", reason)
        self.assertIn("-$50.00", reason)

    def test_below_the_limit_trades(self) -> None:
        for i in range(7):
            self._settle(-6.4, MON - timedelta(minutes=10 * (i + 1)))
        self.assertIsNone(kalshi_regime.daily_loss_stop("eva_wick", now=MON))

    def test_wins_offset_losses(self) -> None:
        for i in range(8):
            self._settle(-6.4, MON - timedelta(minutes=10 * (i + 1)))
        self._settle(+10.0, MON - timedelta(minutes=5))
        self.assertIsNone(kalshi_regime.daily_loss_stop("eva_wick", now=MON))

    def test_yesterdays_losses_do_not_count(self) -> None:
        for i in range(10):
            self._settle(-6.4, MON - timedelta(hours=15, minutes=i))
        self.assertIsNone(kalshi_regime.daily_loss_stop("eva_wick", now=MON))

    def test_paper_bots_are_never_stopped(self) -> None:
        for i in range(10):
            self._settle(-6.4, MON - timedelta(minutes=i + 1), bot="eva_streak")
        self.assertIsNone(kalshi_regime.daily_loss_stop("eva_streak", now=MON))

    def test_bankroll_is_snapshotted_once_per_day(self) -> None:
        import kalshi_sizing

        for i in range(8):
            self._settle(-6.4, MON - timedelta(minutes=10 * (i + 1)))
        kalshi_regime.daily_loss_stop("eva_wick", now=MON)
        kalshi_sizing.sizing_bankroll_usd.return_value = 400.0
        self.assertIsNotNone(
            kalshi_regime.daily_loss_stop("eva_wick", now=MON + timedelta(hours=1)))
        self.assertIsNone(
            kalshi_regime.daily_loss_stop("eva_wick", now=MON + timedelta(days=1)))

    def test_zero_pct_disables(self) -> None:
        for i in range(10):
            self._settle(-6.4, MON - timedelta(minutes=i + 1))
        with mock.patch.object(kalshi_regime, "DAILY_STOP_PCT", 0.0):
            self.assertIsNone(kalshi_regime.daily_loss_stop("eva_wick", now=MON))

    def test_query_failure_fails_open(self) -> None:
        with mock.patch.object(paper, "_connect",
                               side_effect=RuntimeError("locked")):
            self.assertIsNone(kalshi_regime.daily_loss_stop("eva_wick", now=MON))

    def test_entry_gate_applies_it_to_wick(self) -> None:
        for i in range(8):
            self._settle(-6.4, MON - timedelta(minutes=10 * (i + 1)))
        with mock.patch.object(kalshi_regime, "chop_shadow",
                               return_value={"chop": 1.0}):
            reason, _ = kalshi_regime.entry_gate("eva_wick", "BTC-USD", now=MON)
        self.assertIn("daily_loss_stop", reason)


class TestEntryGate(unittest.TestCase):
    def test_wick_gets_weekend_but_never_cooldown(self) -> None:
        with mock.patch.object(kalshi_regime, "chop_shadow",
                               return_value={"chop": 3.0}), \
                mock.patch.object(kalshi_regime, "daily_loss_stop",
                                  return_value=None), \
                mock.patch.object(kalshi_regime, "streak_loss_cooldown") as cd:
            reason, shadow = kalshi_regime.entry_gate("eva_wick", "BTC-USD",
                                                      now=SAT)
            self.assertEqual(reason, "weekend_pause")
            reason, _ = kalshi_regime.entry_gate("eva_wick", "BTC-USD", now=MON)
            self.assertIsNone(reason)
        cd.assert_not_called()   # the sweep says a wick cooldown costs money

    def test_chop_is_shadow_only(self) -> None:
        """An extreme chop reading must not gate anything by itself."""
        with mock.patch.object(kalshi_regime, "chop_shadow",
                               return_value={"chop": 99.0}), \
                mock.patch.object(kalshi_regime, "daily_loss_stop",
                                  return_value=None), \
                mock.patch.object(kalshi_regime, "streak_loss_cooldown",
                                  return_value=None):
            reason, shadow = kalshi_regime.entry_gate("eva_streak", "BTC-USD",
                                                      now=MON)
        self.assertIsNone(reason)
        self.assertEqual(shadow["chop"], 99.0)


if __name__ == "__main__":
    unittest.main()

