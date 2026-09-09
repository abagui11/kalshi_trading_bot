"""Per-bot live/paper routing + boss double-down rule (2026-09-08)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import kalshi_client
import paper
from models import KalshiSuggestion
from strategies.context import SharedCycleContext
from strategies.eva_wick import EvaWickStrategy

EXPIRY = "2099-01-01T00:15:00Z"


def _open_wick_pos(entry: float = 31.0, contracts: int = 1, ticker: str = "KXBTC15M-X"):
    sug = KalshiSuggestion(
        series="KXBTC15M",
        market_ticker=ticker,
        side="YES",
        contracts=contracts,
        entry_cents=entry,
        expiry_ts=EXPIRY,
        rationale="t",
        product_id="BTC",
        bot_id="eva_wick",
    )
    opened = paper.open_trade(sug)
    assert opened is not None
    return opened


def _ctx(yes_mid: float, ticker: str = "KXBTC15M-X") -> SharedCycleContext:
    return SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": ticker},
        market_ticker=ticker,
        product_id="BTC",
        coinbase="BTC-USD",
        cycle_id="T",
        expiry_ts=EXPIRY,
        yes_mid_cents=yes_mid,
        spot=100.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=600.0,
        spot_vs_strike_pct=0.0,
        prior_5m_ret=0.0,
        prior_15m_ret=0.0,
        prior_1h_ret=0.0,
        fair_yes_cents=50.0,
        edge_cents=0.0,
        m5_bars=[],
        htf=None,
        near_decision=True,
        now=datetime(2099, 1, 1, 0, 2, tzinfo=timezone.utc),
        base_kwargs={
            "series": "KXBTC15M",
            "market_ticker": ticker,
            "product_id": "BTC",
            "mid_cents": yes_mid,
            "expiry_ts": EXPIRY,
            "cycle_id": "T",
        },
    )


class BotIsLiveTests(unittest.TestCase):
    def test_paper_only_blankets_everything(self) -> None:
        with patch.object(config, "KALSHI_PAPER_ONLY", True):
            with patch.object(config, "KALSHI_LIVE_BOTS", ("eva_streak",)):
                self.assertFalse(bot_config.bot_is_live("eva_streak"))

    def test_empty_whitelist_keeps_legacy_all_live(self) -> None:
        with patch.object(config, "KALSHI_PAPER_ONLY", False):
            with patch.object(config, "KALSHI_LIVE_BOTS", ()):
                self.assertTrue(bot_config.bot_is_live("eva_wick"))
                self.assertTrue(bot_config.bot_is_live(None))  # -> control

    def test_whitelist_routes_per_bot(self) -> None:
        with patch.object(config, "KALSHI_PAPER_ONLY", False):
            with patch.object(config, "KALSHI_LIVE_BOTS", ("eva_streak",)):
                self.assertTrue(bot_config.bot_is_live("eva_streak"))
                self.assertFalse(bot_config.bot_is_live("eva_wick"))
                self.assertFalse(bot_config.bot_is_live("control"))

    def test_place_order_paper_flag_forces_stub(self) -> None:
        with patch.object(config, "KALSHI_PAPER_ONLY", False):
            resp = kalshi_client.place_order(
                "KXBTC15M-X", "YES", 1, yes_price_cents=50, paper=True
            )
        self.assertEqual(resp.get("status"), "paper_only")


class ScaleHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "e.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_scale_in_blends_entry_and_debits_cash(self) -> None:
        opened = _open_wick_pos(entry=31.0, contracts=1)
        cash_before = paper.available_cash("eva_wick")
        updated = paper.scale_in_position(
            int(opened["id"]), add_contracts=1, price_cents=11.0
        )
        assert updated is not None
        self.assertEqual(int(updated["contracts"]), 2)
        self.assertAlmostEqual(float(updated["entry_cents"]), 21.0)
        self.assertAlmostEqual(
            paper.available_cash("eva_wick"), cash_before - 0.11, places=6
        )

    def test_scale_out_realizes_pnl_and_keeps_remainder(self) -> None:
        opened = _open_wick_pos(entry=31.0, contracts=1)
        paper.scale_in_position(int(opened["id"]), add_contracts=1, price_cents=11.0)
        updated = paper.scale_out_position(
            int(opened["id"]), sell_contracts=1, price_cents=29.0
        )
        assert updated is not None
        self.assertEqual(int(updated["contracts"]), 1)
        self.assertEqual(str(updated["status"]), "open")
        stats = paper.get_stats(bot_id="eva_wick")
        # sold 1 ct at 29c vs 21c avg -> +$0.08 realized
        self.assertAlmostEqual(stats["realized_pnl_usd"], 0.08, places=6)

    def test_scale_out_all_contracts_closes_flat(self) -> None:
        opened = _open_wick_pos(entry=31.0, contracts=2)
        updated = paper.scale_out_position(
            int(opened["id"]), sell_contracts=2, price_cents=40.0
        )
        assert updated is not None
        self.assertEqual(str(updated["status"]), "closed")
        self.assertEqual(str(updated["result"]), "flat")


class DoubleDownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "e.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()
        self.strat = EvaWickStrategy()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def _dd(self, ctx):
        with patch("notify.broadcast_plain_text"):
            return self.strat._maybe_double_down(ctx)

    def test_adds_when_band_entry_crushed(self) -> None:
        opened = _open_wick_pos(entry=31.0, contracts=1)
        paper.set_window_arm(
            bot_id="eva_wick",
            market_ticker="KXBTC15M-X",
            armed_side="YES",
            arm_yes_mid=31.0,
            arm_side_mid=31.0,
            arm_spot=100.0,
            arm_strike=100.0,
            ict_bias="bull",
            htf_bias="bull",
            meta={"eva_wick_done": 1},
        )
        with patch.object(bot_config, "EVA_WICK_DD_ENABLED", True):
            self.assertTrue(self._dd(_ctx(yes_mid=11.0)))
            pos = paper.get_open_positions(bot_id="eva_wick")[0]
            self.assertEqual(int(pos["contracts"]), 2)
            self.assertAlmostEqual(float(pos["entry_cents"]), 21.0)
            # Recovery to 29 -> trim the add, original rides.
            self.assertTrue(self._dd(_ctx(yes_mid=29.0)))
            pos = paper.get_open_positions(bot_id="eva_wick")[0]
            self.assertEqual(int(pos["contracts"]), 1)
            # No second round trip this window.
            self.assertFalse(self._dd(_ctx(yes_mid=11.0)))
        _ = opened

    def test_dd_disabled_by_default(self) -> None:
        _open_wick_pos(entry=31.0, contracts=1)
        paper.set_window_arm(
            bot_id="eva_wick",
            market_ticker="KXBTC15M-X",
            armed_side="YES",
            arm_yes_mid=31.0,
            arm_side_mid=31.0,
            arm_spot=100.0,
            arm_strike=100.0,
            ict_bias="bull",
            htf_bias="bull",
            meta={"eva_wick_done": 1},
        )
        self.assertFalse(bot_config.EVA_WICK_DD_ENABLED)
        self.assertFalse(self._dd(_ctx(yes_mid=11.0)))
        self.assertEqual(int(paper.get_open_positions(bot_id="eva_wick")[0]["contracts"]), 1)

    def test_no_add_outside_entry_band(self) -> None:
        _open_wick_pos(entry=20.0, contracts=1)  # below the 29-33 band
        paper.set_window_arm(
            bot_id="eva_wick",
            market_ticker="KXBTC15M-X",
            armed_side="YES",
            arm_yes_mid=20.0,
            arm_side_mid=20.0,
            arm_spot=100.0,
            arm_strike=100.0,
            ict_bias="bull",
            htf_bias="bull",
            meta={"eva_wick_done": 1},
        )
        with patch.object(bot_config, "EVA_WICK_DD_ENABLED", True):
            self.assertFalse(self._dd(_ctx(yes_mid=11.0)))

    def test_never_runs_when_bot_is_live(self) -> None:
        _open_wick_pos(entry=31.0, contracts=1)
        with patch.object(bot_config, "EVA_WICK_DD_ENABLED", True):
            with patch.object(config, "KALSHI_PAPER_ONLY", False):
                with patch.object(config, "KALSHI_LIVE_BOTS", ()):
                    self.assertFalse(self._dd(_ctx(yes_mid=11.0)))

    def test_tp_target_anchors_to_original_entry_after_dd(self) -> None:
        opened = _open_wick_pos(entry=31.0, contracts=1)
        paper.set_window_arm(
            bot_id="eva_wick",
            market_ticker="KXBTC15M-X",
            armed_side="YES",
            arm_yes_mid=31.0,
            arm_side_mid=31.0,
            arm_spot=100.0,
            arm_strike=100.0,
            ict_bias="bull",
            htf_bias="bull",
            meta={"eva_wick_done": 1},
        )
        with patch.object(bot_config, "EVA_WICK_DD_ENABLED", True):
            self.assertTrue(self._dd(_ctx(yes_mid=11.0)))  # avg now 21
            # 2x the blended 21 = 42, but TP must wait for 2x the original 31 = 62.
            with patch("notify.broadcast_plain_text"):
                self.assertFalse(self.strat._maybe_take_profit(_ctx(yes_mid=45.0)))
                self.assertTrue(self.strat._maybe_take_profit(_ctx(yes_mid=62.0)))
        _ = opened


if __name__ == "__main__":
    unittest.main()
