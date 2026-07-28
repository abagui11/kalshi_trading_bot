"""HTF refresh gate + ENABLED_BOTS consumer tests."""

from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import bot_config
import config
import kalshi_cycle
import paper
from strategies.context import SharedHtfBias


def _fake_htf(**overrides) -> SharedHtfBias:
    base = SharedHtfBias(
        ict_action="spot_sell",
        ict_bias="short",
        ict_rationale="test",
        gate_outcome="pass_fib",
        htf_bias="bear",
        setup_tags=["htf_bear"],
        side="NO",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class HtfRefreshGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "htf.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def test_once_per_window_reuses_after_first(self) -> None:
        ticker = "KXBTC15M-TEST"
        with patch.object(bot_config, "HTF_REFRESH_MODE", "once_per_window"):
            should, reason = kalshi_cycle._should_refresh_htf(
                ticker=ticker, product_id="BTC", spot=100.0
            )
            self.assertTrue(should)
            self.assertEqual(reason, "window_first")

            paper.set_shared_htf_bias(
                ticker,
                {
                    "side": "NO",
                    "htf_bias": "bear",
                    "ict_action": "spot_sell",
                    "ict_bias": "short",
                    "ict_rationale": "x",
                },
            )
            should2, reason2 = kalshi_cycle._should_refresh_htf(
                ticker=ticker, product_id="BTC", spot=100.0
            )
            self.assertFalse(should2)
            self.assertEqual(reason2, "reuse")

    def test_ttl_event_reuses_inside_ttl(self) -> None:
        now = datetime(2026, 7, 28, 15, 30, tzinfo=timezone.utc)
        paper.set_product_htf_bias(
            "BTC",
            {
                "side": "NO",
                "htf_bias": "bear",
                "spot_at_refresh": 100.0,
                "refreshed_at": (now - timedelta(minutes=10)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
        )
        with patch.object(bot_config, "HTF_REFRESH_MODE", "ttl_event"):
            with patch.object(bot_config, "HTF_BIAS_TTL_SEC", 3600):
                with patch.object(bot_config, "HTF_M5_MOVE_PCT", 0.20):
                    with patch.object(bot_config, "HTF_REFRESH_ON_H1_CLOSE", False):
                        should, reason = kalshi_cycle._should_refresh_htf(
                            ticker="KXBTC15M-NEW",
                            product_id="BTC",
                            spot=100.05,
                            now=now,
                        )
                        self.assertFalse(should)
                        self.assertEqual(reason, "reuse")

    def test_ttl_event_refreshes_on_ttl_expiry(self) -> None:
        now = datetime(2026, 7, 28, 15, 30, tzinfo=timezone.utc)
        paper.set_product_htf_bias(
            "BTC",
            {
                "side": "NO",
                "htf_bias": "bear",
                "spot_at_refresh": 100.0,
                "refreshed_at": (now - timedelta(hours=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
        )
        with patch.object(bot_config, "HTF_REFRESH_MODE", "ttl_event"):
            with patch.object(bot_config, "HTF_BIAS_TTL_SEC", 3600):
                with patch.object(bot_config, "HTF_REFRESH_ON_H1_CLOSE", False):
                    should, reason = kalshi_cycle._should_refresh_htf(
                        ticker="KXBTC15M-NEW",
                        product_id="BTC",
                        spot=100.0,
                        now=now,
                    )
                    self.assertTrue(should)
                    self.assertEqual(reason, "ttl")

    def test_ttl_event_refreshes_on_m5_move(self) -> None:
        now = datetime(2026, 7, 28, 15, 10, tzinfo=timezone.utc)
        paper.set_product_htf_bias(
            "BTC",
            {
                "side": "NO",
                "htf_bias": "bear",
                "spot_at_refresh": 100.0,
                "refreshed_at": (now - timedelta(minutes=5)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
        )
        with patch.object(bot_config, "HTF_REFRESH_MODE", "ttl_event"):
            with patch.object(bot_config, "HTF_BIAS_TTL_SEC", 3600):
                with patch.object(bot_config, "HTF_M5_MOVE_PCT", 0.20):
                    with patch.object(bot_config, "HTF_REFRESH_ON_H1_CLOSE", False):
                        should, reason = kalshi_cycle._should_refresh_htf(
                            ticker="KXBTC15M-NEW",
                            product_id="BTC",
                            spot=100.25,  # 0.25% move
                            now=now,
                        )
                        self.assertTrue(should)
                        self.assertEqual(reason, "m5_move")


class HtfBuildContextCallCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db = Path(self._tmp.name) / "htf2.db"
        self._p = patch.object(config, "LEDGER_DB", self._db)
        self._p.start()
        paper.init_db()

    def tearDown(self) -> None:
        self._p.stop()
        self._tmp.cleanup()

    def _market(self) -> dict:
        return {
            "ticker": "KXBTC15M-ONCE",
            "close_time": "2099-01-01T00:15:00Z",
            "yes_bid": 45,
            "yes_ask": 55,
            "floor_strike": 100.0,
        }

    def test_three_near_ticks_one_claude_call(self) -> None:
        compute = MagicMock(return_value=_fake_htf())
        with patch.object(bot_config, "HTF_REFRESH_MODE", "once_per_window"):
            with patch.object(bot_config, "ENABLED_BOTS", ("adverse",)):
                with patch.object(
                    kalshi_cycle, "any_needs_htf_bias", return_value=True
                ):
                    with patch.object(
                        kalshi_cycle, "_compute_htf_bias", compute
                    ):
                        with patch.object(
                            kalshi_cycle,
                            "_build_features",
                            return_value={
                                "spot": 100.0,
                                "strike": 100.0,
                                "sigma": 0.5,
                                "tau_sec": 300.0,
                                "spot_vs_strike_pct": 0.0,
                                "prior_5m_ret": 0.0,
                                "prior_15m_ret": 0.0,
                                "prior_1h_ret": 0.0,
                                "m5_bars": [],
                                "fair": None,
                                "cycle_id": "T",
                            },
                        ):
                            with patch(
                                "kalshi_client.mid_cents_from_market",
                                return_value=50.0,
                            ):
                                for _ in range(3):
                                    ctx = kalshi_cycle.build_shared_context(
                                        "KXBTC15M",
                                        self._market(),
                                        near_decision=True,
                                        force_htf=True,
                                    )
                                    self.assertIsNotNone(ctx)
                                    # Real _compute writes the store; mock does not —
                                    # mirror after first successful refresh decision.
                                    if (
                                        compute.call_count >= 1
                                        and not paper.get_shared_htf_bias(
                                            "KXBTC15M-ONCE"
                                        )
                                    ):
                                        paper.set_shared_htf_bias(
                                            "KXBTC15M-ONCE",
                                            {
                                                "side": "NO",
                                                "htf_bias": "bear",
                                                "ict_action": "spot_sell",
                                                "ict_bias": "short",
                                                "ict_rationale": "x",
                                            },
                                        )
        self.assertEqual(compute.call_count, 1)

    def test_lottery_only_skips_claude(self) -> None:
        compute = MagicMock(return_value=_fake_htf())
        with patch.object(bot_config, "ENABLED_BOTS", ("lottery",)):
            with patch.object(
                kalshi_cycle, "any_needs_htf_bias", return_value=False
            ):
                with patch.object(kalshi_cycle, "_compute_htf_bias", compute):
                    with patch.object(
                        kalshi_cycle,
                        "_build_features",
                        return_value={
                            "spot": 100.0,
                            "strike": 100.0,
                            "sigma": 0.5,
                            "tau_sec": 300.0,
                            "spot_vs_strike_pct": 0.0,
                            "prior_5m_ret": 0.0,
                            "prior_15m_ret": 0.0,
                            "prior_1h_ret": 0.0,
                            "m5_bars": [],
                            "fair": None,
                            "cycle_id": "T",
                        },
                    ):
                        with patch(
                            "kalshi_client.mid_cents_from_market",
                            return_value=50.0,
                        ):
                            kalshi_cycle.build_shared_context(
                                "KXBTC15M",
                                self._market(),
                                near_decision=True,
                                force_htf=True,
                            )
        self.assertEqual(compute.call_count, 0)


    def test_deferred_refresh_does_not_call_claude(self) -> None:
        compute = MagicMock(return_value=_fake_htf())
        with patch.object(bot_config, "HTF_REFRESH_MODE", "once_per_window"):
            with patch.object(bot_config, "ENABLED_BOTS", ("adverse",)):
                with patch.object(
                    kalshi_cycle, "any_needs_htf_bias", return_value=True
                ):
                    with patch.object(
                        kalshi_cycle, "_compute_htf_bias", compute
                    ):
                        with patch.object(
                            kalshi_cycle,
                            "_build_features",
                            return_value={
                                "spot": 100.0,
                                "strike": 100.0,
                                "sigma": 0.5,
                                "tau_sec": 300.0,
                                "spot_vs_strike_pct": 0.0,
                                "prior_5m_ret": 0.0,
                                "prior_15m_ret": 0.0,
                                "prior_1h_ret": 0.0,
                                "m5_bars": [],
                                "fair": None,
                                "cycle_id": "T",
                            },
                        ):
                            with patch(
                                "kalshi_client.mid_cents_from_market",
                                return_value=50.0,
                            ):
                                kalshi_cycle.build_shared_context(
                                    "KXBTC15M",
                                    self._market(),
                                    near_decision=True,
                                    force_htf=True,
                                    allow_htf_refresh=False,
                                )
        self.assertEqual(compute.call_count, 0)

    def test_ensure_htf_refresh_starts_background_once(self) -> None:
        import time

        started_evt = threading.Event()
        release_evt = threading.Event()
        calls: list[str] = []

        def fake_needed() -> bool:
            return True

        def fake_worker() -> None:
            calls.append("ran")
            started_evt.set()
            release_evt.wait(timeout=2.0)

        # Drain lock if a prior test left it held (should not happen).
        if kalshi_cycle._htf_refresh_lock.locked():
            try:
                kalshi_cycle._htf_refresh_lock.release()
            except RuntimeError:
                pass

        with patch.object(
            kalshi_cycle, "_htf_refresh_needed_for_open_markets", fake_needed
        ):
            with patch.object(kalshi_cycle, "_htf_refresh_worker", fake_worker):
                started = kalshi_cycle.ensure_htf_refresh_started()
                self.assertTrue(started)
                self.assertTrue(started_evt.wait(timeout=1.0))
                # Second kick while first holds lock should no-op.
                started2 = kalshi_cycle.ensure_htf_refresh_started()
                self.assertFalse(started2)
                release_evt.set()
                for _ in range(50):
                    if not kalshi_cycle._htf_refresh_lock.locked():
                        break
                    time.sleep(0.02)
                self.assertEqual(calls, ["ran"])
                self.assertTrue(kalshi_cycle._htf_refresh_lock.acquire(blocking=False))
                kalshi_cycle._htf_refresh_lock.release()


if __name__ == "__main__":
    unittest.main()
