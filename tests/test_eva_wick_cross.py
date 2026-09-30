"""Cross-asset wick books: the EVA alignment gate and the signal→target map.

Six books, one per (BTC/ETH signal, XRP/SOL/HYPE target). The load-bearing
behaviour: only the fired signal's three books act, only when the EVA
board's M15 stance points with the fire at ≥ the confidence line, the alt
side is bought at the real ask, and a missing/stale board trades nothing
(fail closed) rather than trading unaligned.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import eva_intel
import eva_wick_cross
import kalshi_client
import paper
from models import KalshiSuggestion

CLOSE = "2099-01-01T00:15:00Z"


def _alt_mkt(series: str, *, yes_bid=28.0, yes_ask=30.0) -> dict:
    d = {
        "ticker": f"{series}-99JAN0100-T1",
        "event_ticker": f"{series}-99JAN0100",
        "close_time": CLOSE,
    }
    if yes_bid is not None:
        d["yes_bid_dollars"] = f"{yes_bid / 100.0:.4f}"
        d["no_ask_dollars"] = f"{(100.0 - yes_bid) / 100.0:.4f}"
    if yes_ask is not None:
        d["yes_ask_dollars"] = f"{yes_ask / 100.0:.4f}"
        d["no_bid_dollars"] = f"{(100.0 - yes_ask) / 100.0:.4f}"
    return d


def _fire(*, side="NO", product="BTC", ticker="KXBTC15M-X") -> KalshiSuggestion:
    return KalshiSuggestion(
        series=f"KX{product}15M",
        market_ticker=ticker,
        side=side,
        contracts=2,
        entry_cents=73.5,
        expiry_ts=CLOSE,
        rationale="wick fire",
        product_id=product,
        spot=84340.0,
        cycle_id="T",
        bot_id="eva_wick",
    )


def _stances(stance: str, conf: float) -> dict:
    row = {"stance": stance, "confidence": conf, "rationale": "", "cycle_ts": ""}
    return {"H4": dict(row), "H1": dict(row), "M15": dict(row)}


class CrossWickTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.chains = {
            s: [_alt_mkt(s)] for s in ("KXXRP15M", "KXSOL15M", "KXHYPE15M")
        }
        self.board = {"BTC-USD": _stances("bearish", 0.7),
                      "ETH-USD": _stances("bearish", 0.7)}
        self._patches = [
            patch.object(config, "LEDGER_DB", Path(self._tmp.name) / "e.db"),
            patch.object(
                bot_config, "ENABLED_BOTS",
                ("eva_wick",) + bot_config.CROSS_WICK_BOTS,
            ),
            patch.object(bot_config, "KALSHI_BANKROLL_USD", 225.0),
            patch.object(bot_config, "KALSHI_DEPLOY_PCT", 0.05),
            patch.object(bot_config, "KALSHI_MAX_NOTIONAL_USD", 34.88),
            patch.object(bot_config, "KALSHI_MAX_CONTRACTS", 2),
            patch.object(bot_config, "KALSHI_BOT_MAX_CONTRACTS", {}),
            patch.object(bot_config, "KALSHI_USE_LIVE_BALANCE", False),
            patch.object(
                kalshi_client, "get_open_markets",
                lambda series: list(self.chains.get(series, [])),
            ),
            patch.object(
                eva_intel, "get_stances",
                lambda product_id, **kw: self.board.get(product_id),
            ),
        ]
        for p in self._patches:
            p.start()
        paper.init_db()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    @staticmethod
    def _open(bot_id: str) -> list[dict]:
        return paper.get_open_positions(bot_id=bot_id)

    # ---------------------------------------------------------- the rule
    def test_aligned_btc_no_fire_buys_no_on_all_three_alts(self) -> None:
        opened = eva_wick_cross.process_fires([_fire()])
        self.assertEqual(len(opened), 3)
        for bot_id, series in (
            ("eva_wick_btc_xrp", "KXXRP15M"),
            ("eva_wick_btc_sol", "KXSOL15M"),
            ("eva_wick_btc_hype", "KXHYPE15M"),
        ):
            rows = self._open(bot_id)
            self.assertEqual(len(rows), 1, bot_id)
            self.assertEqual(rows[0]["side"], "NO")
            self.assertEqual(rows[0]["series"], series)
            # NO ask = 100 − 28 yes bid = 72¢; sized 2 ct under the test caps.
            self.assertAlmostEqual(float(rows[0]["entry_cents"]), 72.0)
            self.assertEqual(int(rows[0]["contracts"]), 2)
        # ETH-keyed books must not act on a BTC fire.
        for bot_id in ("eva_wick_eth_xrp", "eva_wick_eth_sol",
                       "eva_wick_eth_hype"):
            self.assertEqual(self._open(bot_id), [])

    def test_aligned_eth_yes_fire_buys_yes_on_eth_books(self) -> None:
        self.board["ETH-USD"] = _stances("bullish", 0.8)
        opened = eva_wick_cross.process_fires(
            [_fire(side="YES", product="ETH", ticker="KXETH15M-X")]
        )
        self.assertEqual(len(opened), 3)
        rows = self._open("eva_wick_eth_sol")
        self.assertEqual(rows[0]["side"], "YES")
        self.assertAlmostEqual(float(rows[0]["entry_cents"]), 30.0)
        self.assertEqual(self._open("eva_wick_btc_sol"), [])

    # ---------------------------------------------------------- the gate
    def test_stance_mismatch_trades_nothing(self) -> None:
        self.board["BTC-USD"] = _stances("bullish", 0.9)  # fire is NO
        self.assertEqual(eva_wick_cross.process_fires([_fire()]), [])

    def test_neutral_stance_trades_nothing(self) -> None:
        self.board["BTC-USD"] = _stances("neutral", 0.9)
        self.assertEqual(eva_wick_cross.process_fires([_fire()]), [])

    def test_low_confidence_trades_nothing(self) -> None:
        self.board["BTC-USD"] = _stances("bearish", 0.5)  # line is 0.55
        self.assertEqual(eva_wick_cross.process_fires([_fire()]), [])

    def test_unavailable_board_fails_closed(self) -> None:
        self.board["BTC-USD"] = None
        self.assertEqual(eva_wick_cross.process_fires([_fire()]), [])

    # ---------------------------------------------------------- honesty
    def test_degenerate_ask_is_a_skip(self) -> None:
        # Empty SOL book: 0¢ yes bid derives a 100¢ NO "ask" — never a fill.
        self.chains["KXSOL15M"] = [
            _alt_mkt("KXSOL15M", yes_bid=0.0, yes_ask=None)
        ]
        opened = eva_wick_cross.process_fires([_fire()])
        self.assertEqual(len(opened), 2)  # XRP + HYPE still fill
        self.assertEqual(self._open("eva_wick_btc_sol"), [])

    def test_no_duplicate_position_on_the_same_market(self) -> None:
        eva_wick_cross.process_fires([_fire()])
        opened = eva_wick_cross.process_fires([_fire(ticker="KXBTC15M-Y")])
        self.assertEqual(opened, [])
        self.assertEqual(len(self._open("eva_wick_btc_xrp")), 1)

    def test_ignores_non_wick_suggestions(self) -> None:
        other = _fire()
        other.bot_id = "eva_streak"
        self.assertEqual(eva_wick_cross.process_fires([other]), [])

    def test_noop_when_books_not_enabled(self) -> None:
        with patch.object(bot_config, "ENABLED_BOTS", ("eva_wick",)):
            self.assertEqual(eva_wick_cross.process_fires([_fire()]), [])

    def test_cross_books_can_never_go_live(self) -> None:
        for bot_id in bot_config.CROSS_WICK_BOTS:
            self.assertFalse(bot_config.bot_is_live(bot_id))


if __name__ == "__main__":
    unittest.main()
