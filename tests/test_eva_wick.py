"""Unit tests for the zero-Claude EVA wick strategy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import paper
from strategies.context import SharedCycleContext
from strategies.eva_wick import EvaWickStrategy, _window_quarter


def _stances(h4="bearish", h1="bearish", m15="bullish", m15_conf=0.60):
    return {
        "H4": {"stance": h4, "confidence": 0.65, "rationale": "", "cycle_ts": "c"},
        "H1": {"stance": h1, "confidence": 0.66, "rationale": "", "cycle_ts": "c"},
        "M15": {"stance": m15, "confidence": m15_conf, "rationale": "", "cycle_ts": "c"},
    }


def _h1_bars(lo=99.0, hi=101.0, n=25):
    return [
        {"ts": f"t{i}", "open": 100.0, "high": hi, "low": lo, "close": 100.0}
        for i in range(n)
    ]


def _ctx(**overrides) -> SharedCycleContext:
    base = SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": "KXBTC15M-X"},
        market_ticker="KXBTC15M-X",
        product_id="BTC",
        coinbase="BTC-USD",
        cycle_id="T",
        expiry_ts="2099-01-01T00:15:00Z",  # settle :15 → first15 window
        yes_mid_cents=50.0,
        spot=100.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=300.0,
        spot_vs_strike_pct=0.0,
        prior_5m_ret=0.0,
        prior_15m_ret=0.0,
        prior_1h_ret=0.1,
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
        if k in ("yes_mid_cents",):
            base.base_kwargs["mid_cents"] = v
    return base


class EvaWickTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "e.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()
        self.strat = EvaWickStrategy()
        self._patches = [
            patch("kalshi_triggers.in_last_minutes", return_value=False),
            patch(
                "strategies.eva_wick.research.get_ohlc",
                side_effect=lambda tf, limit=0, product_id="": _h1_bars(),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._p.stop()
        self._tmp.cleanup()

    # ---------------------------------------------------------------- gates
    def test_fail_closed_when_stances_missing(self) -> None:
        with patch("strategies.eva_wick.eva_intel.get_stances", return_value=None):
            sug = self.strat.decide(_ctx())
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("eva_stale", sug.skip_codes)

    def test_fade_pop_no_on_pop_into_bearish_h1(self) -> None:
        # Pop: spot above strike, session_pos high (spot 100.9 in 99–101 box).
        ctx = _ctx(spot=100.9, spot_vs_strike_pct=0.9, yes_mid_cents=79.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertEqual(sug.side, "NO")  # NO side mid = 21¢
        self.assertIn("fade_pop", sug.setup_tags)
        self.assertTrue(sug.is_trade())
        # Entered once — second tick same window stays silent after open.
        paper.open_trade(sug)
        self.assertIsNone(self.strat.decide(ctx))

    def test_buy_overshoot_yes_on_flush_with_m15_bullish(self) -> None:
        # H1 neutral (evening chop) + flush below strike at session low.
        ctx = _ctx(spot=99.1, spot_vs_strike_pct=-0.9, yes_mid_cents=30.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances",
            return_value=_stances(h4="bearish", h1="neutral", m15="bullish", m15_conf=0.72),
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertEqual(sug.side, "YES")
        self.assertIn("buy_overshoot", sug.setup_tags)
        self.assertIn("m15_conviction", sug.setup_tags)

    def test_hard_skip_rich_side(self) -> None:
        # Setup present but NO costs 45¢ (> soft max 40¢).
        ctx = _ctx(spot=100.9, spot_vs_strike_pct=0.9, yes_mid_cents=55.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("eva_rich", sug.skip_codes)

    def test_soft_rich_band_reduces_size(self) -> None:
        # NO at 36¢: above 33 hard cap, below 40 soft ceiling → trade, tagged.
        ctx = _ctx(spot=100.9, spot_vs_strike_pct=0.9, yes_mid_cents=64.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertTrue(sug.is_trade())
        self.assertIn("soft_rich", sug.setup_tags)

    def test_btc_move_hard_gate(self) -> None:
        ctx = _ctx(
            spot=100.9, spot_vs_strike_pct=0.9, yes_mid_cents=79.0, prior_1h_ret=0.9
        )
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("eva_btc_move", sug.skip_codes)

    def test_btc_move_soft_band_tags(self) -> None:
        ctx = _ctx(
            spot=100.9, spot_vs_strike_pct=0.9, yes_mid_cents=79.0, prior_1h_ret=0.6
        )
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertTrue(sug.is_trade())
        self.assertIn("soft_btc_move", sug.setup_tags)

    def test_no_setup_mid_range(self) -> None:
        # No excursion, mid-range: nothing to do.
        ctx = _ctx(spot=100.0, spot_vs_strike_pct=0.0, yes_mid_cents=50.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("eva_no_setup", sug.skip_codes)

    def test_mid_hour_window_soft_tag(self) -> None:
        ctx = _ctx(
            spot=100.9,
            spot_vs_strike_pct=0.9,
            yes_mid_cents=79.0,
            expiry_ts="2099-01-01T00:30:00Z",  # settle :30 → mid-hour window
        )
        ctx.base_kwargs["expiry_ts"] = ctx.expiry_ts
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertTrue(sug.is_trade())
        self.assertIn("soft_mid_hour", sug.setup_tags)

    # ---------------------------------------------------------- take-profit
    def test_take_profit_flattens_doubled_position(self) -> None:
        # Open NO at 20¢ via the strategy, then YES mid falls → NO worth 42¢.
        ctx = _ctx(spot=100.9, spot_vs_strike_pct=0.9, yes_mid_cents=80.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ):
            sug = self.strat.decide(ctx)
        assert sug is not None and sug.is_trade()
        opened = paper.open_trade(sug)
        assert opened is not None

        tick = _ctx(spot=99.9, spot_vs_strike_pct=-0.1, yes_mid_cents=58.0)
        with patch(
            "strategies.eva_wick.eva_intel.get_stances", return_value=_stances()
        ), patch("notify.broadcast_plain_text") as bc:
            out = self.strat.decide(tick)
        self.assertIsNone(out)  # flattened, stays quiet
        self.assertFalse(paper.has_open_for_market("KXBTC15M-X", bot_id="eva_wick"))
        bc.assert_called_once()

    # ------------------------------------------------------------- helpers
    def test_window_quarter(self) -> None:
        self.assertEqual(_window_quarter("2099-01-01T00:15:00Z"), "first15")
        self.assertEqual(_window_quarter("2099-01-01T01:00:00Z"), "last15")
        self.assertEqual(_window_quarter("2099-01-01T00:45:00Z"), "mid")

    def test_needs_no_htf_bias(self) -> None:
        self.assertFalse(EvaWickStrategy.needs_htf_bias)


if __name__ == "__main__":
    unittest.main()
