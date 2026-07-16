"""Tests for W1 ETH/BTC relative-strength context and soft gate."""

from __future__ import annotations

import unittest

import research
from patterns.relative_strength import (
    build_relative_strength_context,
    soft_gate_allows,
)


def _bars(closes: list[float], start_price: float | None = None) -> list[dict]:
    """Build simple weekly OHLC bars from a close series."""
    bars: list[dict] = []
    prev = start_price if start_price is not None else closes[0]
    for i, close in enumerate(closes):
        high = max(prev, close) * 1.01
        low = min(prev, close) * 0.99
        bars.append(
            {
                "ts": f"2026-01-{i + 1:02d}T00:00:00Z",
                "open": prev,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100.0,
            }
        )
        prev = close
    return bars


class RatioBarTests(unittest.TestCase):
    def test_ratio_aligns_by_timestamp(self) -> None:
        eth = _bars([2000, 2100, 2200])
        btc = _bars([40000, 40000, 40000])
        ratio = research.build_eth_btc_ratio_bars(eth, btc)
        self.assertEqual(len(ratio), 3)
        self.assertAlmostEqual(ratio[-1]["close"], 2200 / 40000, places=6)

    def test_ratio_skips_unaligned_and_nonpositive(self) -> None:
        eth = _bars([2000, 2100])
        btc = [
            {"ts": "2026-01-01T00:00:00Z", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 1},
            {"ts": "2099-01-01T00:00:00Z", "open": 40000, "high": 40000, "low": 40000, "close": 40000, "volume": 1},
        ]
        ratio = research.build_eth_btc_ratio_bars(eth, btc)
        # First ETH bar aligns to a zero-price BTC bar (skipped); second has no match.
        self.assertEqual(ratio, [])


class SoftGateTests(unittest.TestCase):
    def test_neutral_allows_everything(self) -> None:
        for pid in ("ETH-USD", "BTC-USD"):
            for side in ("long", "short"):
                self.assertTrue(soft_gate_allows("neutral", pid, side))

    def test_eth_strong_blocks_eth_short_and_btc_long(self) -> None:
        self.assertTrue(soft_gate_allows("eth_strong", "ETH-USD", "long"))
        self.assertFalse(soft_gate_allows("eth_strong", "ETH-USD", "short"))
        self.assertTrue(soft_gate_allows("eth_strong", "BTC-USD", "short"))
        self.assertFalse(soft_gate_allows("eth_strong", "BTC-USD", "long"))

    def test_btc_strong_blocks_btc_short_and_eth_long(self) -> None:
        self.assertTrue(soft_gate_allows("btc_strong", "BTC-USD", "long"))
        self.assertFalse(soft_gate_allows("btc_strong", "BTC-USD", "short"))
        self.assertTrue(soft_gate_allows("btc_strong", "ETH-USD", "short"))
        self.assertFalse(soft_gate_allows("btc_strong", "ETH-USD", "long"))


class BuildContextTests(unittest.TestCase):
    def test_empty_when_unaligned(self) -> None:
        eth = _bars([2000, 2100])
        btc = [
            {"ts": "2099-01-01T00:00:00Z", "open": 40000, "high": 40000, "low": 40000, "close": 40000, "volume": 1},
        ]
        rs = build_relative_strength_context(eth_w1=eth, btc_w1=btc)
        self.assertEqual(rs.bias, "neutral")
        self.assertEqual(rs.spot_ratio, 0.0)

    def test_builds_summary_with_bias_value(self) -> None:
        closes_eth = [2000 + i * 20 for i in range(30)]
        closes_btc = [40000 for _ in range(30)]
        rs = build_relative_strength_context(
            eth_w1=_bars(closes_eth), btc_w1=_bars(closes_btc)
        )
        self.assertIn(rs.bias, ("eth_strong", "btc_strong", "neutral"))
        self.assertGreater(rs.spot_ratio, 0.0)
        self.assertIn("ETH/BTC relative strength", rs.summary_text)


if __name__ == "__main__":
    unittest.main()
