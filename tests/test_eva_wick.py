"""EVA wick, redefined 2026-09-17: buy the mid-window favourite.

Covers the two halves of the rule (price band and clock), the one-entry-per-
window guard, and that no directional view is applied — the sleeve buys
whichever side the book itself has above 50¢.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import kalshi_regime
import paper
from models import KalshiSuggestion
from strategies.context import SharedCycleContext
from strategies.eva_wick import EvaWickStrategy

EXPIRY = "2099-01-01T00:15:00Z"
TICKER = "KXBTC15M-X"


def _ctx(
    yes_mid: float,
    *,
    minute: int = 7,
    second: int = 0,
    yes_bid: float | None = None,
    yes_ask: float | None = None,
) -> SharedCycleContext:
    """Context at `minute:second` past the window open (expiry at 00:15).

    `yes_bid`/`yes_ask` are in cents and default to a 1¢ book around the mid.
    """
    if yes_mid is not None:
        bid = yes_mid - 0.5 if yes_bid is None else yes_bid
        ask = yes_mid + 0.5 if yes_ask is None else yes_ask
        book = {
            "yes_bid_dollars": f"{bid / 100.0:.4f}",
            "yes_ask_dollars": f"{ask / 100.0:.4f}",
            "no_bid_dollars": f"{(100.0 - ask) / 100.0:.4f}",
            "no_ask_dollars": f"{(100.0 - bid) / 100.0:.4f}",
        }
    else:
        book = {}
    return SharedCycleContext(
        series="KXBTC15M",
        market={"ticker": TICKER, **book},
        market_ticker=TICKER,
        product_id="BTC",
        coinbase="BTC-USD",
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
        now=datetime(2099, 1, 1, 0, minute, second, tzinfo=timezone.utc),
        base_kwargs={
            "series": "KXBTC15M",
            "market_ticker": TICKER,
            "product_id": "BTC",
            "mid_cents": yes_mid,
            "expiry_ts": EXPIRY,
            "cycle_id": "T",
        },
    )


class EvaFavouriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patches = [
            patch.object(config, "LEDGER_DB", Path(self._tmp.name) / "e.db"),
            patch.object(bot_config, "KALSHI_BANKROLL_USD", 225.0),
            patch.object(bot_config, "KALSHI_DEPLOY_PCT", 0.05),
            patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 34.88),
            patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 2),
            patch.object(bot_config, "KALSHI_BOT_MAX_CONTRACTS", {}),
            # The gate is exercised on its own below. Stubbing it here keeps
            # the rule's tests off the network — chop_shadow fetches candles.
            patch.object(
                kalshi_regime, "entry_gate", lambda *a, **k: (None, {"chop": None})
            ),
        ]
        for p in self._patches:
            p.start()
        paper.init_db()
        self.strat = EvaWickStrategy()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    # ------------------------------------------------------------ the rule
    def test_buys_the_yes_favourite_and_pays_the_ask(self) -> None:
        sug = self.strat.decide(_ctx(yes_mid=73.0))
        assert sug is not None
        self.assertTrue(sug.is_trade())
        self.assertEqual(sug.side, "YES")
        # Qualifies on the 73¢ mid, prices at the 73.5¢ ask.
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)
        self.assertAlmostEqual(float(sug.mid_cents), 73.0)
        self.assertEqual(sug.bot_id, "eva_wick")
        self.assertEqual(sug.trigger_name, "midwindow_favourite")

    def test_buys_the_no_favourite_at_the_no_ask(self) -> None:
        # YES 26.5/27.5 means NO is the favourite, bid 72.5 / ask 73.5.
        sug = self.strat.decide(_ctx(yes_mid=27.0))
        assert sug is not None
        self.assertEqual(sug.side, "NO")
        self.assertAlmostEqual(float(sug.entry_cents), 73.5)

    def test_pays_up_through_a_wide_book(self) -> None:
        # 73¢ mid on a 6¢-wide book: still inside the 82¢ pay cap.
        sug = self.strat.decide(_ctx(yes_mid=73.0, yes_bid=70.0, yes_ask=76.0))
        assert sug is not None
        self.assertAlmostEqual(float(sug.entry_cents), 76.0)

    def test_skips_when_the_ask_is_past_the_pay_cap(self) -> None:
        # 79¢ mid but the ask is 85¢ — in band on the mid, unaffordable in fact.
        self.assertIsNone(
            self.strat.decide(_ctx(yes_mid=79.0, yes_bid=73.0, yes_ask=85.0))
        )

    def test_falls_back_to_the_mid_when_the_book_is_missing(self) -> None:
        ctx = _ctx(yes_mid=73.0)
        ctx.market = {"ticker": TICKER}
        sug = self.strat.decide(ctx)
        assert sug is not None
        self.assertAlmostEqual(float(sug.entry_cents), 73.0)

    def test_no_directional_view_is_applied(self) -> None:
        """Opposite tapes both trade; only the book's own pricing decides."""
        up = self.strat.decide(_ctx(yes_mid=72.0))
        assert up is not None
        self.assertEqual(up.side, "YES")
        paper.set_window_arm(
            bot_id="eva_wick", market_ticker=TICKER, armed_side="X",
            arm_yes_mid=None, arm_side_mid=None, arm_spot=None, arm_strike=None,
            ict_bias=None, htf_bias=None, meta={},
        )
        down = self.strat.decide(_ctx(yes_mid=28.0))
        assert down is not None
        self.assertEqual(down.side, "NO")

    # ------------------------------------------------------- price band
    def test_skips_when_favourite_too_cheap(self) -> None:
        # 60¢ favourite: the band starts at 67¢.
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=60.0)))

    def test_skips_when_favourite_too_rich(self) -> None:
        # 85¢ favourite: above 80¢ there is too little payout left.
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=85.0)))

    def test_skips_a_coinflip_market(self) -> None:
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=50.0)))

    def test_trades_both_band_edges(self) -> None:
        self.assertIsNotNone(self.strat.decide(_ctx(yes_mid=67.0)))
        paper.set_window_arm(
            bot_id="eva_wick", market_ticker=TICKER, armed_side="X",
            arm_yes_mid=None, arm_side_mid=None, arm_spot=None, arm_strike=None,
            ict_bias=None, htf_bias=None, meta={},
        )
        self.assertIsNotNone(self.strat.decide(_ctx(yes_mid=80.0)))

    # ------------------------------------------------------------ clock
    def test_skips_too_early_in_the_window(self) -> None:
        # 2 min in => 13 min left, well above the 10 min ceiling. This is the
        # bucket the old bot traded, and it prices fairly.
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=73.0, minute=2)))

    def test_skips_the_last_two_minutes(self) -> None:
        # 13:30 in => 90s left: the arb zone, where this trade is negative.
        self.assertIsNone(
            self.strat.decide(_ctx(yes_mid=73.0, minute=13, second=30))
        )

    def test_trades_both_clock_edges(self) -> None:
        self.assertIsNotNone(self.strat.decide(_ctx(yes_mid=73.0, minute=5)))
        paper.set_window_arm(
            bot_id="eva_wick", market_ticker=TICKER, armed_side="X",
            arm_yes_mid=None, arm_side_mid=None, arm_spot=None, arm_strike=None,
            ict_bias=None, htf_bias=None, meta={},
        )
        self.assertIsNotNone(self.strat.decide(_ctx(yes_mid=73.0, minute=11)))

    # ------------------------------------------------- one entry per window
    def test_only_one_entry_per_window(self) -> None:
        first = self.strat.decide(_ctx(yes_mid=73.0))
        assert first is not None
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=75.0)))

    def test_skips_when_a_position_is_already_open(self) -> None:
        paper.open_trade(
            KalshiSuggestion(
                series="KXBTC15M", market_ticker=TICKER, side="YES", contracts=1,
                entry_cents=73.0, expiry_ts=EXPIRY, rationale="t",
                product_id="BTC", bot_id="eva_wick",
            )
        )
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=75.0)))

    # ----------------------------------------------------------- plumbing
    def test_respects_the_per_bot_contract_cap(self) -> None:
        with patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 8):
            with patch.object(bot_config, "KALSHI_BOT_MAX_CONTRACTS", {"eva_wick": 2}):
                sug = self.strat.decide(_ctx(yes_mid=73.0))
        assert sug is not None
        self.assertEqual(int(sug.contracts), 2)

    def test_never_requests_the_claude_htf_refresh(self) -> None:
        self.assertFalse(EvaWickStrategy.needs_htf_bias)

    def test_returns_none_without_a_mid(self) -> None:
        self.assertIsNone(self.strat.decide(_ctx(yes_mid=None)))  # type: ignore[arg-type]


class RegimeGateTests(unittest.TestCase):
    """The live book's daily stop and weekend watch reach it through decide().

    Worth its own class: the gate is a single call and losing it is silent —
    the book keeps trading and looks healthier, not broken.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patches = [
            patch.object(config, "LEDGER_DB", Path(self._tmp.name) / "g.db"),
            patch.object(bot_config, "KALSHI_BANKROLL_USD", 225.0),
            patch.object(bot_config, "KALSHI_DEPLOY_PCT", 0.05),
            patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 2),
            patch.object(bot_config, "KALSHI_BOT_MAX_CONTRACTS", {}),
        ]
        for p in self._patches:
            p.start()
        paper.init_db()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def test_live_book_stands_down_when_the_gate_fires(self) -> None:
        calls: list[str] = []

        def gate(bot_id, product_id, now=None):
            calls.append(bot_id)
            return "daily_stop", {"chop": None}

        with patch.object(kalshi_regime, "entry_gate", gate):
            self.assertIsNone(EvaWickStrategy().decide(_ctx(yes_mid=73.0)))
        self.assertEqual(calls, ["eva_wick"])

    def test_paper_clones_are_not_gated(self) -> None:
        """A shadow book must not inherit the live book's breakers.

        The weekend watch is calibrated on eva_wick's own weekday P&L and the
        daily stop exempts paper, so gating these would censor the sample.
        """
        from strategies.eva_wick_alt import EvaWickAltStrategy

        def boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("paper clone consulted the regime gate")

        strat = EvaWickAltStrategy("eva_wick_xrp", "KXXRP15M")
        with patch.object(kalshi_regime, "entry_gate", boom):
            with patch.object(
                strat, "size_for_fill", lambda c, s, e, n: (n, e, {})
            ):
                sug = strat.decide(_ctx(yes_mid=73.0))
        assert sug is not None
        self.assertEqual(sug.bot_id, "eva_wick_xrp")


if __name__ == "__main__":
    unittest.main()
