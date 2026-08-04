"""Unit tests for lottery / adverse strategy helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import paper
from strategies.context import SharedCycleContext, SharedHtfBias
from strategies.lottery import LotteryStrategy
from strategies.adverse import AdverseStrategy


def _ctx(**overrides) -> SharedCycleContext:
    base = SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": "KXBTC15M-X"},
        market_ticker="KXBTC15M-X",
        product_id="BTC",
        coinbase="BTC-USD",
        cycle_id="T",
        expiry_ts="2099-01-01T00:15:00Z",
        yes_mid_cents=50.0,
        spot=100.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=300.0,
        spot_vs_strike_pct=0.0,
        prior_5m_ret=0.0,
        prior_15m_ret=0.0,
        prior_1h_ret=0.0,
        fair_yes_cents=55.0,
        edge_cents=5.0,
        m5_bars=[],
        htf=None,
        near_decision=True,
        base_kwargs={
            "series": "KXBTC15M",
            "market_ticker": "KXBTC15M-X",
            "product_id": "BTC",
            "mid_cents": 50.0,
            "fair_yes_cents": 55.0,
            "edge_cents": 5.0,
            "expiry_ts": "2099-01-01T00:15:00Z",
            "cycle_id": "T",
        },
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class LotteryStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "l.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_hail_mary_pending_limit(self) -> None:
        bars = [
            {"high": 100, "low": 90, "close": 95, "open": 94},
            {"high": 101, "low": 91, "close": 96, "open": 95},
            {"high": 102, "low": 92, "close": 97, "open": 96},
            {"high": 103, "low": 93, "close": 98, "open": 97},
            {"high": 110, "low": 95, "close": 99, "open": 98},
            {"high": 100, "low": 96, "close": 98, "open": 99},
        ]
        # Force last-5m window via mock minutes_to_expiry
        with patch("kalshi_triggers.minutes_to_expiry", return_value=4.0):
            with patch(
                "kalshi_triggers.lottery_cancel_at_iso",
                return_value="2099-01-01T00:13:30Z",
            ):
                ctx = _ctx(yes_mid_cents=8.0, m5_bars=bars)
                ctx.base_kwargs["mid_cents"] = 8.0
                sug = LotteryStrategy().decide(ctx)
        self.assertIsNotNone(sug)
        assert sug is not None
        self.assertEqual(sug.side, "YES")
        self.assertTrue(sug.pending_limit)
        self.assertEqual(sug.trigger_name, "hail_mary")


class AdverseStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "a.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_arms_then_enters_after_adverse(self) -> None:
        htf = SharedHtfBias(
            ict_action="spot_sell",
            ict_bias="short",
            ict_rationale="bearish",
            gate_outcome="pass_fib",
            htf_bias="bear",
            setup_tags=["htf_bear"],
            side="NO",
        )
        with patch("kalshi_triggers.in_last_minutes", return_value=False):
            # YES 45 → NO side mid 55¢ (above min-arm 35¢).
            ctx = _ctx(htf=htf, yes_mid_cents=45.0, spot=100.0, strike=100.0)
            ctx.base_kwargs["mid_cents"] = 45.0
            arm = AdverseStrategy().decide(ctx)
            self.assertIsNotNone(arm)
            assert arm is not None
            self.assertEqual(arm.side, "SKIP")
            self.assertIn("adverse_armed", arm.skip_codes)

            # Mild adverse: YES 60 → NO mid 40¢ (mid_imp=15, at max); spot spike.
            ctx2 = _ctx(
                htf=htf,
                yes_mid_cents=60.0,
                spot=100.2,
                strike=100.0,
                near_decision=False,
            )
            ctx2.base_kwargs["mid_cents"] = 60.0
            fill = AdverseStrategy().decide(ctx2)
        self.assertIsNotNone(fill)
        assert fill is not None
        # May be trade or skip depending on edge filter — at least not silent arm again
        self.assertNotIn("adverse_armed", fill.skip_codes or [])
        self.assertNotIn("adverse_too_cheap", fill.skip_codes or [])
        self.assertNotIn("adverse_mid_blowoff", fill.skip_codes or [])

    def test_skips_arm_when_side_already_cheap(self) -> None:
        htf = SharedHtfBias(
            ict_action="spot_sell",
            ict_bias="short",
            ict_rationale="bearish",
            gate_outcome="pass_fib",
            htf_bias="bear",
            setup_tags=["htf_bear"],
            side="NO",
        )
        with patch("kalshi_triggers.in_last_minutes", return_value=False):
            # YES 70 → NO mid 30¢ < 35¢ min arm.
            ctx = _ctx(htf=htf, yes_mid_cents=70.0, spot=100.0, strike=100.0)
            ctx.base_kwargs["mid_cents"] = 70.0
            sug = AdverseStrategy().decide(ctx)
        self.assertIsNotNone(sug)
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("adverse_arm_too_cheap", sug.skip_codes)
        self.assertIsNone(paper.get_window_arm("adverse", "KXBTC15M-X"))

    def test_skips_entry_below_min_cents(self) -> None:
        htf = SharedHtfBias(
            ict_action="spot_sell",
            ict_bias="short",
            ict_rationale="bearish",
            gate_outcome="pass_fib",
            htf_bias="bear",
            setup_tags=["htf_bear"],
            side="NO",
        )
        with patch("kalshi_triggers.in_last_minutes", return_value=False):
            ctx = _ctx(htf=htf, yes_mid_cents=45.0, spot=100.0, strike=100.0)
            ctx.base_kwargs["mid_cents"] = 45.0
            AdverseStrategy().decide(ctx)
            # YES 90 → NO mid 10¢ (below 15¢ floor); mid_imp capped separately.
            ctx2 = _ctx(
                htf=htf,
                yes_mid_cents=90.0,
                spot=100.2,
                strike=100.0,
                near_decision=False,
            )
            ctx2.base_kwargs["mid_cents"] = 90.0
            fill = AdverseStrategy().decide(ctx2)
        self.assertIsNotNone(fill)
        assert fill is not None
        self.assertEqual(fill.side, "SKIP")
        self.assertIn("adverse_too_cheap", fill.skip_codes)

    def test_skips_mid_blowoff(self) -> None:
        htf = SharedHtfBias(
            ict_action="spot_sell",
            ict_bias="short",
            ict_rationale="bearish",
            gate_outcome="pass_fib",
            htf_bias="bear",
            setup_tags=["htf_bear"],
            side="NO",
        )
        with patch("kalshi_triggers.in_last_minutes", return_value=False):
            ctx = _ctx(htf=htf, yes_mid_cents=45.0, spot=100.0, strike=100.0)
            ctx.base_kwargs["mid_cents"] = 45.0
            AdverseStrategy().decide(ctx)
            # YES 80 → NO mid 20¢ (>=15 floor) but mid_imp=35 > 15 blowoff cap.
            ctx2 = _ctx(
                htf=htf,
                yes_mid_cents=80.0,
                spot=100.2,
                strike=100.0,
                near_decision=False,
            )
            ctx2.base_kwargs["mid_cents"] = 80.0
            fill = AdverseStrategy().decide(ctx2)
        self.assertIsNotNone(fill)
        assert fill is not None
        self.assertEqual(fill.side, "SKIP")
        self.assertIn("adverse_mid_blowoff", fill.skip_codes)


if __name__ == "__main__":
    unittest.main()
