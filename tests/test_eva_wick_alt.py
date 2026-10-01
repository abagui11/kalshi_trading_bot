"""Altcoin clones of the mid-window favourite rule (XRP / SOL / HYPE, paper).

The rule itself is already covered by test_eva_wick.py and is inherited, not
reimplemented, so what matters here is everything around it: that a clone
prices a market exactly the way the live book would, that it can only ever see
its own series, that it can never route a real order, and that it trims a clip
to the depth actually quoted instead of inventing liquidity.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import kalshi_client
import paper
from strategies.context import SharedCycleContext
from strategies.eva_wick import EvaWickStrategy, _arm_meta
from strategies.eva_wick_alt import EvaWickAltStrategy, alt_wick_strategies
from strategies.registry import handles_series

EXPIRY = "2099-01-01T00:15:00Z"


def _ctx(
    yes_mid: float,
    *,
    series: str = "KXXRP15M",
    ticker: str = "KXXRP15M-X",
    minute: int = 7,
    yes_bid: float | None = None,
    yes_ask: float | None = None,
    ask_size: float | None = 5000.0,
    bid_size: float | None = 5000.0,
) -> SharedCycleContext:
    """Context at `minute` past the window open, with a sized book.

    `ask_size`/`bid_size` are the YES-book quantities Kalshi publishes at the
    touch; None drops the field entirely, which is the older-payload case.
    """
    bid = yes_mid - 0.5 if yes_bid is None else yes_bid
    ask = yes_mid + 0.5 if yes_ask is None else yes_ask
    market = {
        "ticker": ticker,
        "yes_bid_dollars": f"{bid / 100.0:.4f}",
        "yes_ask_dollars": f"{ask / 100.0:.4f}",
        "no_bid_dollars": f"{(100.0 - ask) / 100.0:.4f}",
        "no_ask_dollars": f"{(100.0 - bid) / 100.0:.4f}",
    }
    if ask_size is not None:
        market["yes_ask_size_fp"] = f"{ask_size:.2f}"
    if bid_size is not None:
        market["yes_bid_size_fp"] = f"{bid_size:.2f}"
    product = bot_config.SERIES_TO_PRODUCT[series]
    return SharedCycleContext(
        series=series,
        market=market,
        market_ticker=ticker,
        product_id=product,
        coinbase=bot_config.PRODUCT_TO_COINBASE[product],
        cycle_id="T",
        expiry_ts=EXPIRY,
        yes_mid_cents=yes_mid,
        spot=100.0,
        strike=100.0,
        sigma=0.5,
        tau_sec=480.0,
        spot_vs_strike_pct=0.0,
        prior_5m_ret=0.0,
        prior_15m_ret=0.0,
        prior_1h_ret=0.0,
        fair_yes_cents=50.0,
        edge_cents=0.0,
        m5_bars=[],
        htf=None,
        near_decision=True,
        now=datetime(2099, 1, 1, 0, minute, 0, tzinfo=timezone.utc),
        base_kwargs={
            "series": series,
            "market_ticker": ticker,
            "product_id": product,
            "mid_cents": yes_mid,
            "expiry_ts": EXPIRY,
            "cycle_id": "T",
        },
    )


@contextlib.contextmanager
def _ladder(levels):
    """Serve `levels` as the live ask ladder; None means the fetch failed.

    Yields the list of tickers the ladder was requested for, so a test can
    assert that the live book never reaches for one.
    """
    calls: list[str] = []

    def fake(side, ticker):
        calls.append(ticker)
        return None if levels is None else list(levels)

    with patch.object(kalshi_client, "get_ask_ladder", fake):
        yield calls


class AltWickTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patches = [
            patch.object(config, "LEDGER_DB", Path(self._tmp.name) / "e.db"),
            patch.object(bot_config, "KALSHI_BANKROLL_USD", 225.0),
            patch.object(bot_config, "KALSHI_DEPLOY_PCT", 0.06),
            patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 34.88),
            patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 16),
            patch.object(bot_config, "KALSHI_BOT_MAX_CONTRACTS", {}),
            # No network in the default path; tests that exercise the fill
            # model install their own ladder with _ladder().
            patch.object(kalshi_client, "get_ask_ladder", lambda s, t: None),
            # The ladder walk is OFF in production (see bot_config) because
            # the published arrays could not be reconciled with the quoted
            # touch. The cases below still pin its behaviour for whenever it
            # is re-enabled, so they turn it on explicitly.
            patch.object(bot_config, "EVA_FAV_ALT_TRIM_TO_DEPTH", True),
        ]
        for p in self._patches:
            p.start()
        paper.init_db()
        self.xrp = EvaWickAltStrategy("eva_wick_xrp", "KXXRP15M")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    # ------------------------------------------------ same rule as the live book
    def test_prices_a_market_exactly_as_the_live_book_would(self) -> None:
        """Parity is the point: a divergence here means the clone has drifted."""
        live = EvaWickStrategy().decide(
            _ctx(73.0, series="KXBTC15M", ticker="KXBTC15M-X")
        )
        alt = self.xrp.decide(_ctx(73.0))
        assert live is not None and alt is not None
        self.assertEqual(alt.side, live.side)
        self.assertEqual(float(alt.entry_cents), float(live.entry_cents))
        self.assertEqual(int(alt.contracts), int(live.contracts))
        self.assertEqual(alt.trigger_name, live.trigger_name)
        self.assertEqual(alt.bot_id, "eva_wick_xrp")

    def test_inherits_the_band_and_clock(self) -> None:
        self.assertIsNone(self.xrp.decide(_ctx(60.0)))          # under the band
        self.assertIsNone(self.xrp.decide(_ctx(85.0)))          # over the band
        self.assertIsNone(self.xrp.decide(_ctx(73.0, minute=2)))  # too early

    def test_skips_a_book_too_wide_to_pay_for(self) -> None:
        """The pay cap is the spread guard, inherited unchanged."""
        self.assertIsNone(
            self.xrp.decide(_ctx(79.0, yes_bid=73.0, yes_ask=85.0))
        )

    # ------------------------------------------------------------ fill realism
    def test_a_deep_ladder_fills_the_whole_clip_at_the_touch(self) -> None:
        with _ladder([(73.5, 500.0)]):
            sug = self.xrp.decide(_ctx(73.0))
        assert sug is not None
        self.assertEqual(int(sug.contracts), 16)
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)

    def test_walks_past_a_thin_touch_and_records_the_average_price(self) -> None:
        """A 5-lot touch with size behind it is a full fill, not a 5-lot clip."""
        with _ladder([(73.5, 5.0), (74.5, 400.0)]):
            sug = self.xrp.decide(_ctx(73.0))
        assert sug is not None
        self.assertEqual(int(sug.contracts), 16)
        # 5 @ 73.5 + 11 @ 74.5
        self.assertAlmostEqual(float(sug.entry_cents), (5 * 73.5 + 11 * 74.5) / 16)
        meta = _arm_meta(paper.get_window_arm("eva_wick_xrp", "KXXRP15M-X"))
        self.assertAlmostEqual(meta["touch_ask"], 73.5)
        self.assertGreater(meta["slippage_vs_touch"], 0)

    def test_stops_at_the_limit_the_live_order_would_carry(self) -> None:
        """Ask 73.5 + 2¢ aggression = 75.5; the 80¢ level is out of reach."""
        with _ladder([(73.5, 6.0), (80.0, 500.0)]):
            sug = self.xrp.decide(_ctx(73.0))
        assert sug is not None
        self.assertEqual(int(sug.contracts), 6)
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)

    def test_an_unreachable_book_means_no_trade_and_no_burnt_window(self) -> None:
        with _ladder([(90.0, 500.0)]):
            self.assertIsNone(self.xrp.decide(_ctx(73.0)))
        self.assertIsNone(paper.get_window_arm("eva_wick_xrp", "KXXRP15M-X"))
        # Liquidity returned later in the same window — the entry is still live.
        with _ladder([(73.5, 500.0)]):
            self.assertIsNotNone(self.xrp.decide(_ctx(73.0)))

    def test_an_unreadable_book_is_not_treated_as_an_empty_one(self) -> None:
        with _ladder(None):
            sug = self.xrp.decide(_ctx(73.0))
        assert sug is not None
        self.assertEqual(int(sug.contracts), 16)
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)
        meta = _arm_meta(paper.get_window_arm("eva_wick_xrp", "KXXRP15M-X"))
        self.assertEqual(meta["fill_model"], "unavailable")

    def test_a_fill_can_never_beat_the_quoted_touch(self) -> None:
        """The regression that took the walk out of production.

        On 2026-09-30 a SOL entry booked at 61c against a quoted 69c ask,
        because the ladder carried levels below the touch and the walk takes
        the cheapest first. Those levels are not liquidity we could have hit;
        a simulated fill that improves on the best offer is always a bug.
        """
        with _ladder([(61.0, 500.0), (73.5, 500.0)]):
            sug = self.xrp.decide(_ctx(73.0))
        assert sug is not None
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)
        self.assertEqual(int(sug.contracts), 16)
        meta = _arm_meta(paper.get_window_arm("eva_wick_xrp", "KXXRP15M-X"))
        self.assertGreaterEqual(meta["slippage_vs_touch"], 0)

    def test_production_default_is_to_fill_at_the_ask(self) -> None:
        """Shipped state: parity with the live book, no ladder consulted."""
        with patch.object(bot_config, "EVA_FAV_ALT_TRIM_TO_DEPTH", False):
            with _ladder([(61.0, 500.0)]) as calls:
                sug = self.xrp.decide(_ctx(73.0))
        assert sug is not None
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)
        self.assertEqual(int(sug.contracts), 16)
        self.assertEqual(calls, [])
        meta = _arm_meta(paper.get_window_arm("eva_wick_xrp", "KXXRP15M-X"))
        self.assertEqual(meta["fill_model"], "off")

    def test_the_live_book_never_simulates_a_fill(self) -> None:
        """The hook is identity on eva_wick: BTC/ETH behaviour is untouched."""
        live = EvaWickStrategy()
        with _ladder([(73.5, 1.0)]) as calls:
            sug = live.decide(
                _ctx(73.0, series="KXBTC15M", ticker="KXBTC15M-X")
            )
        assert sug is not None
        self.assertEqual(int(sug.contracts), 16)
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)
        self.assertEqual(calls, [], "live book must not fetch an orderbook")

    # --------------------------------------------------------------- routing
    def test_each_clone_sees_only_its_own_series(self) -> None:
        for strat in alt_wick_strategies():
            own = strat.series_filter[0]
            for series in ("KXBTC15M", "KXETH15M", "KXXRP15M", "KXSOL15M",
                           "KXHYPE15M"):
                self.assertEqual(
                    handles_series(strat, series), series == own,
                    f"{strat.bot_id} vs {series}",
                )

    def test_the_shipped_books_never_see_an_altcoin_series(self) -> None:
        live = EvaWickStrategy()
        for series in ("KXXRP15M", "KXSOL15M", "KXHYPE15M"):
            self.assertFalse(handles_series(live, series))

    def test_altcoin_series_are_polled_only_when_their_bot_is_enabled(self) -> None:
        with patch.object(bot_config, "ENABLED_BOTS", ("eva_wick",)):
            self.assertEqual(
                bot_config.active_series(), tuple(bot_config.KALSHI_SERIES)
            )
        with patch.object(bot_config, "ENABLED_BOTS", ("eva_wick", "eva_wick_sol")):
            self.assertEqual(
                bot_config.active_series(),
                tuple(bot_config.KALSHI_SERIES) + ("KXSOL15M",),
            )

    # ---------------------------------------------------------------- safety
    def test_unreleased_clones_can_never_route_a_live_order(self) -> None:
        """Even explicitly whitelisted as live, and with paper-only off."""
        with patch.object(config, "KALSHI_PAPER_ONLY", False):
            with patch.object(
                config, "KALSHI_LIVE_BOTS",
                ("eva_wick", "eva_wick_xrp", "eva_wick_sol", "eva_wick_hype"),
            ):
                self.assertTrue(bot_config.bot_is_live("eva_wick"))
                for bot_id in bot_config.ALT_WICK_VARIANTS:
                    if bot_id in bot_config.ALT_WICK_LIVE_RELEASED:
                        continue
                    self.assertFalse(bot_config.bot_is_live(bot_id))

    def test_released_clone_needs_the_env_whitelist_too(self) -> None:
        with patch.object(config, "KALSHI_PAPER_ONLY", False):
            with patch.object(config, "KALSHI_LIVE_BOTS", ("eva_wick",)):
                self.assertFalse(bot_config.bot_is_live("eva_wick_sol"))
            with patch.object(
                config, "KALSHI_LIVE_BOTS", ("eva_wick", "eva_wick_sol")
            ):
                self.assertTrue(bot_config.bot_is_live("eva_wick_sol"))

    def test_live_clone_skips_the_fill_model(self) -> None:
        strat = next(
            s for s in alt_wick_strategies() if s.bot_id == "eva_wick_sol"
        )
        with patch.object(config, "KALSHI_PAPER_ONLY", False), patch.object(
            config, "KALSHI_LIVE_BOTS", ("eva_wick", "eva_wick_sol")
        ):
            ct, px, meta = strat.size_for_fill(None, "YES", 74.0, 25)
        self.assertEqual((ct, px, meta["fill_model"]), (25, 74.0, "live"))

    def test_never_requests_the_claude_htf_refresh(self) -> None:
        for strat in alt_wick_strategies():
            self.assertFalse(strat.needs_htf_bias)


class AskLadderTests(unittest.TestCase):
    """Kalshi publishes two bid ladders; an ask is the other side's bid."""

    # Shape of a real KXSOL15M orderbook, truncated.
    BOOK = {
        "no_dollars": [["0.0900", "1.00"], ["0.0910", "50.00"],
                       ["0.0930", "21.00"]],
        "yes_dollars": [["0.8900", "269.60"], ["0.9000", "4.00"],
                        ["0.9060", "3.00"]],
    }

    def test_yes_asks_come_from_the_no_bids_cheapest_first(self) -> None:
        ladder = kalshi_client.ask_ladder_from_orderbook("YES", self.BOOK)
        self.assertEqual(ladder, [(90.7, 21.0), (90.9, 50.0), (91.0, 1.0)])

    def test_no_asks_come_from_the_yes_bids(self) -> None:
        ladder = kalshi_client.ask_ladder_from_orderbook("NO", self.BOOK)
        self.assertEqual(ladder, [(9.4, 3.0), (10.0, 4.0), (11.0, 269.6)])

    def test_the_best_ask_matches_the_quoted_touch(self) -> None:
        """Sanity-check against the market payload's own bid/ask fields."""
        best_yes_ask = kalshi_client.ask_ladder_from_orderbook(
            "YES", self.BOOK)[0][0]
        best_yes_bid = 100.0 - kalshi_client.ask_ladder_from_orderbook(
            "NO", self.BOOK)[0][0]
        self.assertAlmostEqual(best_yes_ask, 90.7)
        self.assertAlmostEqual(best_yes_bid, 90.6)

    def test_empty_and_malformed_ladders_are_survivable(self) -> None:
        self.assertEqual(kalshi_client.ask_ladder_from_orderbook("YES", {}), [])
        self.assertEqual(
            kalshi_client.ask_ladder_from_orderbook("YES", {"no_dollars": [
                ["0.10", "0"], ["bad", "5"], ["0.20", "7"]]}),
            [(80.0, 7.0)],
        )

    def test_a_walk_consumes_levels_cheapest_first(self) -> None:
        filled, vwap = kalshi_client.simulate_ioc_fill(
            [(70.0, 4.0), (71.0, 4.0), (72.0, 100.0)], 10, 75.0)
        self.assertEqual(filled, 10)
        self.assertAlmostEqual(vwap, (4 * 70 + 4 * 71 + 2 * 72) / 10)

    def test_a_walk_stops_at_the_limit(self) -> None:
        filled, vwap = kalshi_client.simulate_ioc_fill(
            [(70.0, 4.0), (76.0, 100.0)], 10, 75.0)
        self.assertEqual((filled, vwap), (4, 70.0))

    def test_a_walk_with_nothing_in_reach_fills_nothing(self) -> None:
        self.assertEqual(
            kalshi_client.simulate_ioc_fill([(90.0, 100.0)], 10, 75.0),
            (0, None),
        )


if __name__ == "__main__":
    unittest.main()
