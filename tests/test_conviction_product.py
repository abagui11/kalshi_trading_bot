"""Conviction sizing + control always-enter + macro flatten + pagination."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import kalshi_conviction
import paper
from macro.pulse import bias_contradicts_side, maybe_flatten_contradicted
from models import KalshiSuggestion
from strategies.adverse_boost import adverse_size_boost
from strategies.context import SharedCycleContext, SharedHtfBias
from strategies.control import ControlStrategy


def _ctx(**overrides) -> SharedCycleContext:
    base = SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": "KXBTC15M-X"},
        market_ticker="KXBTC15M-X",
        product_id="BTC",
        coinbase="BTC-USD",
        cycle_id="T",
        expiry_ts="2099-01-01T00:15:00Z",
        yes_mid_cents=40.0,
        spot=100.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=600.0,
        spot_vs_strike_pct=0.0,
        prior_5m_ret=0.0,
        prior_15m_ret=0.0,
        prior_1h_ret=0.0,
        fair_yes_cents=55.0,
        edge_cents=15.0,
        m5_bars=[],
        htf=SharedHtfBias(
            ict_action="spot_buy",
            ict_bias="bull",
            ict_rationale="test",
            gate_outcome="pass_fib",
            htf_bias="bull",
            setup_tags=["ict"],
            critic_downgraded=False,
            critic_passes=1,
            critic_findings=[],
            chart_read_score=0.9,
            side="YES",
        ),
        near_decision=True,
        base_kwargs={
            "series": "KXBTC15M",
            "market_ticker": "KXBTC15M-X",
            "product_id": "BTC",
            "mid_cents": 40.0,
            "fair_yes_cents": 55.0,
            "edge_cents": 15.0,
            "expiry_ts": "2099-01-01T00:15:00Z",
            "cycle_id": "T",
        },
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class ConvictionSizingTests(unittest.TestCase):
    def test_fallback_side_order(self) -> None:
        side, src = kalshi_conviction.resolve_side_with_fallback(
            ict_side=None, htf_bias="bear", yes_mid_cents=60.0
        )
        self.assertEqual((side, src), ("NO", "htf"))
        side, src = kalshi_conviction.resolve_side_with_fallback(
            ict_side=None, htf_bias="unknown", yes_mid_cents=60.0
        )
        self.assertEqual((side, src), ("YES", "market"))

    def test_matrix_and_15pct_cap(self) -> None:
        plan = kalshi_conviction.compute_sizing(
            side="NO",
            yes_mid_cents=70.0,  # market leans YES → contra for NO
            conviction="high",
            adverse_boost=1.25,
        )
        self.assertFalse(plan["market_agree"])
        self.assertAlmostEqual(plan["base_deploy_pct"], 0.12)
        # 0.12 * 1.25 = 0.15 exactly at cap
        self.assertLessEqual(plan["deploy_pct"], 0.15 + 1e-9)
        self.assertAlmostEqual(plan["deploy_pct"], 0.15)

    def test_low_agree_negligible(self) -> None:
        plan = kalshi_conviction.compute_sizing(
            side="YES", yes_mid_cents=60.0, conviction="low", adverse_boost=1.0
        )
        self.assertTrue(plan["market_agree"])
        self.assertAlmostEqual(plan["deploy_pct"], 0.005)

    def test_adverse_boost_only_multiplies(self) -> None:
        boost, audit = adverse_size_boost(
            side="YES", yes_mid_cents=30.0, fair_yes_cents=50.0, edge_cents=20.0
        )
        self.assertGreaterEqual(boost, 1.0)
        self.assertLessEqual(boost, float(bot_config.ADVERSE_SIZE_BOOST_MAX))
        self.assertIn("cheap_frac", audit)


class ControlAlwaysEnterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "c.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_no_trade_falls_back_and_trades(self) -> None:
        htf = SharedHtfBias(
            ict_action="no_trade",
            ict_bias="unknown",
            ict_rationale="flat",
            gate_outcome=None,
            htf_bias="bull",
            setup_tags=[],
            critic_downgraded=False,
            critic_passes=0,
            critic_findings=[],
            chart_read_score=0.2,
            side=None,
        )
        ctx = _ctx(htf=htf, yes_mid_cents=45.0, edge_cents=2.0, fair_yes_cents=47.0)
        ctx.base_kwargs["mid_cents"] = 45.0
        ctx.base_kwargs["edge_cents"] = 2.0
        with patch("kalshi_triggers.minutes_to_expiry", return_value=10.0):
            with patch("kalshi_triggers.in_last_minutes", return_value=False):
                sug = ControlStrategy().decide(ctx)
        self.assertIsNotNone(sug)
        assert sug is not None
        self.assertEqual(sug.side, "YES")  # htf bull fallback
        self.assertEqual(sug.conviction, "low")
        self.assertEqual(sug.side_source, "htf")
        self.assertTrue(sug.is_trade() or sug.side == "YES")

    def test_extreme_mid_still_skips(self) -> None:
        ctx = _ctx(yes_mid_cents=2.0)
        ctx.base_kwargs["mid_cents"] = 2.0
        with patch("kalshi_triggers.minutes_to_expiry", return_value=10.0):
            with patch("kalshi_triggers.in_last_minutes", return_value=False):
                sug = ControlStrategy().decide(ctx)
        self.assertIsNotNone(sug)
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("extreme_mid", sug.skip_codes)


class MacroFlattenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "m.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_bias_contradicts(self) -> None:
        self.assertTrue(bias_contradicts_side("bearish", "YES"))
        self.assertFalse(bias_contradicts_side("bearish", "NO"))
        self.assertTrue(bias_contradicts_side("bullish", "NO"))

    def test_flatten_on_contradict(self) -> None:
        sug = KalshiSuggestion(
            series="KXBTC15M",
            market_ticker="KXBTC15M-X",
            side="YES",
            contracts=2,
            entry_cents=40.0,
            expiry_ts="2099-01-01T00:15:00Z",
            rationale="t",
            product_id="BTC",
            bot_id="control",
        )
        paper.open_trade(sug)
        opens = paper.get_open_positions(bot_id="control")
        self.assertEqual(len(opens), 1)
        with patch("macro.pulse._side_exit_cents", return_value=35.0):
            with patch("macro.pulse._try_live_close", return_value=None):
                flat = maybe_flatten_contradicted(
                    {"severity": 5, "eth_bias": "bearish", "id": 1},
                    opens,
                    {"recommendation": "hold", "per_position": []},
                )
        self.assertEqual(len(flat), 1)
        self.assertEqual(flat[0]["result"], "flat")
        self.assertEqual(len(paper.get_open_positions(bot_id="control")), 0)

    def test_no_flatten_when_agree(self) -> None:
        sug = KalshiSuggestion(
            series="KXBTC15M",
            market_ticker="KXBTC15M-Y",
            side="NO",
            contracts=1,
            entry_cents=40.0,
            expiry_ts="2099-01-01T00:15:00Z",
            rationale="t",
            product_id="BTC",
            bot_id="control",
        )
        paper.open_trade(sug)
        opens = paper.get_open_positions(bot_id="control")
        flat = maybe_flatten_contradicted(
            {"severity": 5, "eth_bias": "bearish", "id": 1},
            opens,
            {"recommendation": "hold", "per_position": []},
        )
        self.assertEqual(flat, [])
        self.assertEqual(len(paper.get_open_positions(bot_id="control")), 1)


class PaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "p.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_offset_and_trades_filter(self) -> None:
        for i, side in enumerate(["YES", "SKIP", "NO", "SKIP", "YES"]):
            sug = KalshiSuggestion(
                series="KXBTC15M",
                market_ticker=f"T-{i}",
                side=side,
                contracts=1 if side != "SKIP" else 0,
                entry_cents=40.0 if side != "SKIP" else None,
                expiry_ts="2099-01-01T00:15:00Z",
                rationale="r",
                product_id="BTC",
                bot_id="control",
                conviction="med" if side != "SKIP" else None,
                market_agree=True if side == "YES" else False,
                deploy_pct=0.03 if side != "SKIP" else None,
            )
            if side != "SKIP":
                sug.opened = True
            paper.log_decision(sug)
        self.assertEqual(paper.count_decisions(bot_id="control", filter_mode="trades"), 3)
        self.assertEqual(paper.count_decisions(bot_id="control", filter_mode="skips"), 2)
        page = paper.get_decisions(
            limit=2, offset=0, bot_id="control", filter_mode="trades"
        )
        self.assertEqual(len(page), 2)
        self.assertTrue(all(r["side"] in ("YES", "NO") for r in page))
        page2 = paper.get_decisions(
            limit=2, offset=2, bot_id="control", filter_mode="trades"
        )
        self.assertEqual(len(page2), 1)


if __name__ == "__main__":
    unittest.main()
