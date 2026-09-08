"""Unit tests for the last-2-min favorite-dip strategy (eva_arb)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import config
import paper
from strategies.context import SharedCycleContext
from strategies.eva_arb import EvaArbStrategy

WINDOW_OPEN = datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)
EXPIRY = "2099-01-01T00:15:00Z"


def _ctx(yes_mid: float, minutes_in: float) -> SharedCycleContext:
    return SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": "KXBTC15M-X"},
        market_ticker="KXBTC15M-X",
        product_id="BTC",
        coinbase="BTC-USD",
        cycle_id="T",
        expiry_ts=EXPIRY,
        yes_mid_cents=yes_mid,
        spot=100.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=(15.0 - minutes_in) * 60.0,
        spot_vs_strike_pct=0.0,
        prior_5m_ret=0.0,
        prior_15m_ret=0.0,
        prior_1h_ret=0.0,
        fair_yes_cents=yes_mid,
        edge_cents=0.0,
        m5_bars=[],
        htf=None,
        near_decision=False,
        now=WINDOW_OPEN + timedelta(minutes=minutes_in),
        base_kwargs={
            "series": "KXBTC15M",
            "market_ticker": "KXBTC15M-X",
            "product_id": "BTC",
            "mid_cents": yes_mid,
            "expiry_ts": EXPIRY,
            "cycle_id": "T",
        },
    )


class EvaArbTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "e.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()
        self.strat = EvaArbStrategy()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_touch_then_dip_buys_favored_yes(self) -> None:
        # Early window: YES at 92 (touch), no trade yet — just tracking.
        self.assertIsNone(self.strat.decide(_ctx(92.0, minutes_in=5.0)))
        # Last 2 min: YES dips to 80 -> buy YES at 80.
        sug = self.strat.decide(_ctx(80.0, minutes_in=13.5))
        assert sug is not None
        self.assertEqual(sug.side, "YES")
        self.assertAlmostEqual(float(sug.entry_cents), 80.0)
        self.assertIn("eva_arb", sug.setup_tags)
        # One entry per window.
        self.assertIsNone(self.strat.decide(_ctx(80.0, minutes_in=13.7)))

    def test_touch_then_dip_buys_favored_no(self) -> None:
        # YES at 7 == NO at 93 (touch for NO).
        self.assertIsNone(self.strat.decide(_ctx(7.0, minutes_in=6.0)))
        # NO dips to 78 (YES 22) inside the final window.
        sug = self.strat.decide(_ctx(22.0, minutes_in=13.5))
        assert sug is not None
        self.assertEqual(sug.side, "NO")
        self.assertAlmostEqual(float(sug.entry_cents), 78.0)

    def test_no_trade_without_a_touch(self) -> None:
        self.assertIsNone(self.strat.decide(_ctx(85.0, minutes_in=5.0)))
        self.assertIsNone(self.strat.decide(_ctx(80.0, minutes_in=13.5)))

    def test_no_trade_outside_dip_zone(self) -> None:
        self.assertIsNone(self.strat.decide(_ctx(92.0, minutes_in=5.0)))
        # Crashed through the zone: 70 is below the 75c floor.
        self.assertIsNone(self.strat.decide(_ctx(70.0, minutes_in=13.5)))
        # Still priced as a favorite: 88 is above the 85c cap.
        self.assertIsNone(self.strat.decide(_ctx(88.0, minutes_in=13.5)))

    def test_no_trade_before_final_window_or_after_freeze(self) -> None:
        self.assertIsNone(self.strat.decide(_ctx(92.0, minutes_in=5.0)))
        # Dip zone but 3 min left — outside the 2-min action window.
        self.assertIsNone(self.strat.decide(_ctx(80.0, minutes_in=12.0)))
        # Dip zone but only ~30s left — Kalshi stops matching ~45s out.
        self.assertIsNone(self.strat.decide(_ctx(80.0, minutes_in=14.5)))

    def test_skips_when_position_open(self) -> None:
        from models import KalshiSuggestion

        self.assertIsNone(self.strat.decide(_ctx(92.0, minutes_in=5.0)))
        opened = paper.open_trade(
            KalshiSuggestion(
                series="KXBTC15M",
                market_ticker="KXBTC15M-X",
                side="YES",
                contracts=1,
                entry_cents=80.0,
                expiry_ts=EXPIRY,
                rationale="t",
                product_id="BTC",
                bot_id="eva_arb",
            )
        )
        assert opened is not None
        self.assertIsNone(self.strat.decide(_ctx(80.0, minutes_in=13.5)))


if __name__ == "__main__":
    unittest.main()
