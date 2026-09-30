"""Hourly piggyback books: rung selection, quantities, and the once-per-hour
guard, pinned to the operator's own example — wick fires NO with BTC at
84,340, so the ladder buys NO past 84,299.99 / 84,199.99 / 84,099.99 at
4/2/1 contracts and the flat book takes 1/1 on the first two rungs.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_config
import config
import eva_wick_hourly
import paper
from models import KalshiSuggestion

BTC_EVENT = "KXBTCD-99JAN0101"
ETH_EVENT = "KXETHD-99JAN0101"
CLOSE = "2099-01-01T01:00:00Z"


def _mkt(
    event: str,
    strike: float,
    *,
    yes_bid: float | None = 60.0,
    yes_ask: float | None = 62.0,
    close: str = CLOSE,
    strike_type: str = "greater",
) -> dict:
    d = {
        "ticker": f"{event}-T{strike}",
        "event_ticker": event,
        "floor_strike": strike,
        "strike_type": strike_type,
        "close_time": close,
    }
    if yes_bid is not None:
        d["yes_bid_dollars"] = f"{yes_bid / 100.0:.4f}"
        d["no_ask_dollars"] = f"{(100.0 - yes_bid) / 100.0:.4f}"
    if yes_ask is not None:
        d["yes_ask_dollars"] = f"{yes_ask / 100.0:.4f}"
        d["no_bid_dollars"] = f"{(100.0 - yes_ask) / 100.0:.4f}"
    return d


def _btc_chain() -> list[dict]:
    # $100 grid around the example spot of 84,340, listed shuffled to prove
    # ordering comes from the module, not the API.
    strikes = [83999.99, 84099.99, 84199.99, 84299.99, 84399.99,
               84499.99, 84599.99, 84699.99]
    chain = [_mkt(BTC_EVENT, s) for s in strikes]
    return chain[::-1]


def _eth_chain() -> list[dict]:
    strikes = [3389.99, 3394.99, 3399.99, 3404.99, 3409.99, 3414.99]
    return [_mkt(ETH_EVENT, s) for s in strikes]


def _fire(
    *,
    side: str = "NO",
    spot: float | None = 84340.0,
    product: str = "BTC",
    ticker: str = "KXBTC15M-X",
) -> KalshiSuggestion:
    return KalshiSuggestion(
        series="KXBTC15M",
        market_ticker=ticker,
        side=side,
        contracts=2,
        entry_cents=73.5,
        expiry_ts="2099-01-01T00:15:00Z",
        rationale="wick fire",
        product_id=product,
        spot=spot,
        cycle_id="T",
        bot_id="eva_wick",
    )


class HourlyPiggybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.chains = {"KXBTCD": _btc_chain(), "KXETHD": _eth_chain()}
        self._patches = [
            patch.object(config, "LEDGER_DB", Path(self._tmp.name) / "e.db"),
            patch.object(
                bot_config,
                "ENABLED_BOTS",
                ("eva_wick", "eva_wick_1h_ladder", "eva_wick_1h_flat"),
            ),
            patch.object(
                eva_wick_hourly,
                "_fetch_markets",
                lambda series: list(self.chains.get(series, [])),
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
    def _positions(bot_id: str) -> list[dict]:
        rows = paper.get_open_positions(bot_id=bot_id)
        return sorted(rows, key=lambda r: str(r["market_ticker"]))

    # ------------------------------------------------- the spec's example
    def test_no_fire_places_the_spec_example(self) -> None:
        opened = eva_wick_hourly.process_fires([_fire()])
        self.assertEqual(len(opened), 5)

        ladder = self._positions("eva_wick_1h_ladder")
        got = {r["market_ticker"]: int(r["contracts"]) for r in ladder}
        self.assertEqual(
            got,
            {
                f"{BTC_EVENT}-T84299.99": 4,
                f"{BTC_EVENT}-T84199.99": 2,
                f"{BTC_EVENT}-T84099.99": 1,
            },
        )
        # Entry is the NO ask (100 − 60 yes bid = 40¢), the side is NO.
        for r in ladder:
            self.assertEqual(r["side"], "NO")
            self.assertAlmostEqual(float(r["entry_cents"]), 40.0)

        flat = self._positions("eva_wick_1h_flat")
        got = {r["market_ticker"]: int(r["contracts"]) for r in flat}
        self.assertEqual(
            got,
            {f"{BTC_EVENT}-T84299.99": 1, f"{BTC_EVENT}-T84199.99": 1},
        )

    def test_yes_fire_mirrors_above_spot(self) -> None:
        eva_wick_hourly.process_fires([_fire(side="YES")])
        ladder = self._positions("eva_wick_1h_ladder")
        got = {r["market_ticker"]: int(r["contracts"]) for r in ladder}
        self.assertEqual(
            got,
            {
                f"{BTC_EVENT}-T84399.99": 4,
                f"{BTC_EVENT}-T84499.99": 2,
                f"{BTC_EVENT}-T84599.99": 1,
            },
        )
        for r in ladder:
            self.assertEqual(r["side"], "YES")
            self.assertAlmostEqual(float(r["entry_cents"]), 62.0)

    def test_eth_fire_uses_the_eth_hourly_series(self) -> None:
        eva_wick_hourly.process_fires(
            [_fire(product="ETH", spot=3402.0, ticker="KXETH15M-X")]
        )
        ladder = self._positions("eva_wick_1h_ladder")
        got = {r["market_ticker"]: int(r["contracts"]) for r in ladder}
        self.assertEqual(
            got,
            {
                f"{ETH_EVENT}-T3399.99": 4,
                f"{ETH_EVENT}-T3394.99": 2,
                f"{ETH_EVENT}-T3389.99": 1,
            },
        )

    # ------------------------------------------------- once per hour vs every fire
    def test_ladder_fires_once_per_event_flat_fires_every_time(self) -> None:
        eva_wick_hourly.process_fires([_fire()])
        # Second fire in the same hour, spot 100 lower: ladder must not act,
        # the flat book takes the one strike it does not already hold.
        opened = eva_wick_hourly.process_fires(
            [_fire(spot=84240.0, ticker="KXBTC15M-Y")]
        )
        self.assertEqual(len(self._positions("eva_wick_1h_ladder")), 3)
        flat = {r["market_ticker"] for r in self._positions("eva_wick_1h_flat")}
        self.assertEqual(
            flat,
            {
                f"{BTC_EVENT}-T84299.99",
                f"{BTC_EVENT}-T84199.99",
                f"{BTC_EVENT}-T84099.99",
            },
        )
        self.assertEqual(len(opened), 1)

    def test_ladder_once_guard_counts_settled_rungs(self) -> None:
        eva_wick_hourly.process_fires([_fire()])
        for r in self._positions("eva_wick_1h_ladder"):
            paper.settle_position(
                str(r["market_ticker"]), "no",
                bot_id="eva_wick_1h_ladder", position_id=int(r["id"]),
            )
        self.assertEqual(self._positions("eva_wick_1h_ladder"), [])
        eva_wick_hourly.process_fires([_fire(ticker="KXBTC15M-Y")])
        self.assertEqual(self._positions("eva_wick_1h_ladder"), [])

    # ------------------------------------------------- guards
    def test_ignores_non_wick_suggestions_and_skips(self) -> None:
        other = _fire()
        other.bot_id = "eva_streak"
        skip = _fire()
        skip.side = "SKIP"
        skip.contracts = 0
        self.assertEqual(eva_wick_hourly.process_fires([other, skip]), [])
        self.assertEqual(self._positions("eva_wick_1h_ladder"), [])

    def test_noop_when_books_not_enabled(self) -> None:
        with patch.object(bot_config, "ENABLED_BOTS", ("eva_wick",)):
            self.assertEqual(eva_wick_hourly.process_fires([_fire()]), [])
        self.assertEqual(self._positions("eva_wick_1h_ladder"), [])

    def test_rung_without_an_ask_is_skipped_not_invented(self) -> None:
        # Strip the quotes off the rung-2 strike: that rung must be skipped
        # while rungs 1 and 3 still fill.
        chain = _btc_chain()
        for m in chain:
            if m["floor_strike"] == 84199.99:
                for k in ("yes_bid_dollars", "yes_ask_dollars",
                          "no_bid_dollars", "no_ask_dollars"):
                    m.pop(k, None)
        self.chains["KXBTCD"] = chain
        eva_wick_hourly.process_fires([_fire()])
        got = {
            r["market_ticker"]: int(r["contracts"])
            for r in self._positions("eva_wick_1h_ladder")
        }
        self.assertEqual(
            got,
            {f"{BTC_EVENT}-T84299.99": 4, f"{BTC_EVENT}-T84099.99": 1},
        )

    def test_degenerate_hundred_cent_ask_is_a_skip(self) -> None:
        # Kalshi's empty-book placeholder ("1.0000" no-ask / 0¢ yes bid)
        # derives to a ~100¢ "price" nobody is offering — never a fill.
        chain = [
            _mkt(BTC_EVENT, s, yes_bid=0.0, yes_ask=None)
            for s in (84299.99, 84199.99, 84099.99)
        ]
        self.chains["KXBTCD"] = chain
        self.assertEqual(eva_wick_hourly.process_fires([_fire()]), [])
        self.assertEqual(self._positions("eva_wick_1h_ladder"), [])

    def test_missing_spot_places_nothing(self) -> None:
        self.assertEqual(
            eva_wick_hourly.process_fires([_fire(spot=None)]), []
        )

    def test_picks_the_soonest_event_and_only_threshold_markets(self) -> None:
        later = [
            _mkt("KXBTCD-99JAN0102", s, close="2099-01-01T02:00:00Z")
            for s in (84299.99, 84199.99, 84099.99)
        ]
        ranges = [
            _mkt(BTC_EVENT, 84250.0, strike_type="between"),
        ]
        self.chains["KXBTCD"] = _btc_chain() + later + ranges
        eva_wick_hourly.process_fires([_fire()])
        tickers = {
            r["market_ticker"] for r in self._positions("eva_wick_1h_ladder")
        }
        self.assertTrue(all(t.startswith(f"{BTC_EVENT}-T842") or
                            t.startswith(f"{BTC_EVENT}-T841") or
                            t.startswith(f"{BTC_EVENT}-T840")
                            for t in tickers))
        self.assertNotIn(f"{BTC_EVENT}-T84250.0", tickers)


if __name__ == "__main__":
    unittest.main()
