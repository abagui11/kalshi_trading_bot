"""Unit tests for the Dan streak-reversal strategy (eva_streak)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import config
import paper
from strategies.context import SharedCycleContext
from strategies.eva_streak import EvaStreakStrategy, detect_streak, resample_15m

WINDOW_OPEN = datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)
EXPIRY = "2099-01-01T00:15:00Z"


def _candle(offset_min: int, o: float, c: float, lo: float | None = None, hi: float | None = None):
    return {
        "ts": WINDOW_OPEN + timedelta(minutes=offset_min),
        "open": o,
        "close": c,
        "low": lo if lo is not None else min(o, c),
        "high": hi if hi is not None else max(o, c),
    }


def _m5_for_candles(candles):
    """Expand synthetic 15m candles into 3 flat M5 bars each."""
    bars = []
    for c in candles:
        for j in range(3):
            ts = c["ts"] + timedelta(minutes=5 * j)
            bars.append(
                {
                    "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "open": c["open"] if j == 0 else c["close"],
                    "close": c["close"],
                    "low": c["low"],
                    "high": c["high"],
                }
            )
    return bars


def _down_run_candles(runs: int = 3, swept: bool = True):
    """`runs` down candles ending right before WINDOW_OPEN, preceded by an up candle."""
    out = [_candle(-15 * (runs + 1), 100.0, 101.0)]  # up candle before the run
    px = 101.0
    for i in range(runs):
        lo = px - 1.0
        # sweep: last run candle pierces below the prior candle's low
        if swept and i == runs - 1:
            lo = px - 1.6
        out.append(_candle(-15 * (runs - i), px, px - 1.0, lo=lo))
        px -= 1.0
    return out


def _ctx(**overrides) -> SharedCycleContext:
    base = SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": "KXBTC15M-X"},
        market_ticker="KXBTC15M-X",
        product_id="BTC",
        coinbase="BTC-USD",
        cycle_id="T",
        expiry_ts=EXPIRY,
        yes_mid_cents=50.0,
        spot=98.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=600.0,
        spot_vs_strike_pct=-2.0,
        prior_5m_ret=0.0,
        prior_15m_ret=-1.0,
        prior_1h_ret=-3.0,
        fair_yes_cents=45.0,
        edge_cents=0.0,
        m5_bars=[],
        htf=None,
        near_decision=True,
        now=WINDOW_OPEN + timedelta(minutes=2),
        base_kwargs={
            "series": "KXBTC15M",
            "market_ticker": "KXBTC15M-X",
            "product_id": "BTC",
            "mid_cents": 50.0,
            "expiry_ts": EXPIRY,
            "cycle_id": "T",
        },
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class DetectStreakTests(unittest.TestCase):
    def test_down_run_with_sweep(self) -> None:
        run, d, swept = detect_streak(_down_run_candles(3, swept=True), WINDOW_OPEN)
        self.assertEqual((run, d, swept), (3, -1, True))

    def test_down_run_without_sweep(self) -> None:
        candles = _down_run_candles(3, swept=False)
        # keep the last candle's low above the prior candle's low
        candles[-1]["low"] = candles[-2]["low"] + 0.5
        run, d, swept = detect_streak(candles, WINDOW_OPEN)
        self.assertEqual((run, d), (3, -1))
        self.assertFalse(swept)

    def test_no_run_when_gap_before_open(self) -> None:
        candles = _down_run_candles(3)
        candles = candles[:-1]  # last candle missing -> not contiguous with open
        run, d, swept = detect_streak(candles, WINDOW_OPEN)
        self.assertEqual(run, 0)

    def test_resample_skips_incomplete_buckets(self) -> None:
        m5 = _m5_for_candles(_down_run_candles(2))
        m5.append(  # lone bar in a new bucket -> dropped
            {"ts": "2099-01-01T00:00:00Z", "open": 98, "close": 97, "low": 97, "high": 98}
        )
        candles = resample_15m(m5)
        self.assertTrue(all(c["ts"] < WINDOW_OPEN for c in candles))


class EvaStreakDecideTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "e.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()
        self.strat = EvaStreakStrategy()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def _decide(self, ctx, candles):
        with patch(
            "strategies.eva_streak.research.get_ohlc",
            return_value=_m5_for_candles(candles),
        ):
            return self.strat.decide(ctx)

    def test_takes_yes_at_mid_after_down_run_with_sweep(self) -> None:
        sug = self._decide(_ctx(), _down_run_candles(3, swept=True))
        assert sug is not None
        self.assertEqual(sug.side, "YES")
        self.assertFalse(sug.pending_limit)
        # Mid entry: YES side of a 50 mid = 50c.
        self.assertAlmostEqual(float(sug.entry_cents), 50.0)
        self.assertIn("sweep", sug.setup_tags)
        # Second tick same window: armed -> silent.
        self.assertIsNone(self._decide(_ctx(), _down_run_candles(3, swept=True)))

    def test_no_entry_off_decision_offset(self) -> None:
        ctx = _ctx(near_decision=False)
        self.assertIsNone(self._decide(ctx, _down_run_candles(3, swept=True)))

    def test_skips_without_sweep(self) -> None:
        candles = _down_run_candles(3, swept=False)
        candles[-1]["low"] = candles[-2]["low"] + 0.5
        sug = self._decide(_ctx(), candles)
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("streak_no_sweep", sug.skip_codes)

    def test_skips_when_reversal_side_too_cheap(self) -> None:
        # 3 down candles -> buy YES, but YES mid is only 15c (continuation priced).
        ctx = _ctx(yes_mid_cents=15.0)
        sug = self._decide(ctx, _down_run_candles(3, swept=True))
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("streak_too_cheap", sug.skip_codes)

    def test_skips_when_reversal_side_too_rich(self) -> None:
        # YES mid 75 > MAX_SIDE_MID 65 — reversal already priced in.
        ctx = _ctx(yes_mid_cents=75.0)
        sug = self._decide(ctx, _down_run_candles(3, swept=True))
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("streak_too_rich", sug.skip_codes)

    def test_no_entry_on_short_run(self) -> None:
        self.assertIsNone(self._decide(_ctx(), _down_run_candles(2, swept=True)))

    def test_tp_flattens_open_position(self) -> None:
        from models import KalshiSuggestion

        sug = KalshiSuggestion(
            series="KXBTC15M",
            market_ticker="KXBTC15M-X",
            side="YES",
            contracts=1,
            entry_cents=30.0,
            expiry_ts=EXPIRY,
            rationale="t",
            product_id="BTC",
            bot_id="eva_streak",
        )
        opened = paper.open_trade(sug)
        assert opened is not None
        # YES mid 65 >= 2x entry -> TP
        ctx = _ctx(yes_mid_cents=65.0)
        with patch("notify.broadcast_plain_text"):
            handled = self.strat._manage_open(ctx)
        self.assertTrue(handled)
        closed = paper.get_closed_positions(bot_id="eva_streak")
        self.assertEqual(len(closed), 1)
        self.assertIn("eva_streak_tp", str(closed[0]["rationale"]))

    def test_sl_cuts_at_half(self) -> None:
        from models import KalshiSuggestion

        sug = KalshiSuggestion(
            series="KXBTC15M",
            market_ticker="KXBTC15M-X",
            side="YES",
            contracts=1,
            entry_cents=30.0,
            expiry_ts=EXPIRY,
            rationale="t",
            product_id="BTC",
            bot_id="eva_streak",
        )
        opened = paper.open_trade(sug)
        assert opened is not None
        ctx = _ctx(yes_mid_cents=14.0)  # side 14 <= 15 = half of 30
        with patch("notify.broadcast_plain_text"):
            handled = self.strat._manage_open(ctx)
        self.assertTrue(handled)
        closed = paper.get_closed_positions(bot_id="eva_streak")
        self.assertIn("eva_streak_sl", str(closed[0]["rationale"]))

    def test_cooldown_after_consecutive_stops(self) -> None:
        from models import KalshiSuggestion

        for i, ticker in enumerate(("KXBTC15M-A", "KXBTC15M-B")):
            sug = KalshiSuggestion(
                series="KXBTC15M",
                market_ticker=ticker,
                side="YES",
                contracts=1,
                entry_cents=30.0,
                expiry_ts=EXPIRY,
                rationale="t",
                product_id="BTC",
                bot_id="eva_streak",
            )
            opened = paper.open_trade(sug)
            assert opened is not None
            paper.flatten_position_early(
                int(opened["id"]), exit_side_cents=15.0, reason="eva_streak_sl"
            )
        # closed_at is "now" (wall clock); use a ctx clock near now.
        ctx = _ctx(now=datetime.now(timezone.utc))
        sug = self._decide(ctx, _down_run_candles(3, swept=True))
        assert sug is not None
        self.assertEqual(sug.side, "SKIP")
        self.assertIn("streak_cooldown", sug.skip_codes)


if __name__ == "__main__":
    unittest.main()
