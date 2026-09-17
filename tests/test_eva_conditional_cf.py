"""eva_intel's conditional-read counterfactual — shadow only.

The wick bot takes its direction from the hub's scalar stances and fails
closed on them. The hub's conditional ICT read is unscored and flagged
consume:false, so what these tests pin is that reading it can neither steer a
decision nor raise into one.
"""

from __future__ import annotations

import unittest
from unittest import mock

import eva_intel


def _payload(bias, *, timeframe="H1", product_id="BTC-USD", **extra):
    read = {"product_id": product_id, "timeframe": timeframe, "bias": bias}
    read.update(extra)
    return {
        "stances": [],
        "conditional_reads": {
            "status": "experimental", "consume": False, "reads": [read],
        },
    }


class TestConditionalCounterfactual(unittest.TestCase):
    def setUp(self) -> None:
        eva_intel.reset_cache()

    def tearDown(self) -> None:
        eva_intel.reset_cache()

    def _cf(self, payload, *, actual_side="YES", lean="bullish"):
        with mock.patch.object(eva_intel, "_payload", return_value=payload):
            return eva_intel.conditional_counterfactual(
                "BTC-USD", actual_side=actual_side, lean=lean
            )

    def test_agreement_on_side_and_lean(self) -> None:
        cf = self._cf(_payload("bullish"), actual_side="YES", lean="bullish")
        self.assertEqual(cf["cond_would_side"], "YES")
        self.assertTrue(cf["cond_side_agreed"])
        self.assertTrue(cf["cond_lean_agreed"])

    def test_disagreement_is_recorded_not_applied(self) -> None:
        cf = self._cf(_payload("bearish"), actual_side="YES", lean="bullish")
        self.assertEqual(cf["cond_would_side"], "NO")
        self.assertFalse(cf["cond_side_agreed"])
        self.assertFalse(cf["cond_lean_agreed"])

    def test_null_bias_is_an_abstention(self) -> None:
        """No side is a different outcome from a neutral side."""
        cf = self._cf(_payload(None))
        self.assertEqual(cf["cond_bias"], "abstain")
        self.assertIsNone(cf["cond_would_side"])
        self.assertIsNone(cf["cond_side_agreed"])
        self.assertIsNone(cf["cond_lean_agreed"])

    def test_missing_block_degrades_quietly(self) -> None:
        cf = self._cf({"stances": []})
        self.assertEqual(cf["cond_bias"], "abstain")
        self.assertIsNone(cf["cond_would_side"])

    def test_no_payload_at_all_degrades_quietly(self) -> None:
        cf = self._cf(None)
        self.assertEqual(cf["cond_bias"], "abstain")

    def test_malformed_reads_do_not_raise(self) -> None:
        """A bookkeeping read must never be able to stop a live decision."""
        payload = {"conditional_reads": {"reads": ["not-a-dict", None, 42]}}
        cf = self._cf(payload)
        self.assertEqual(cf["cond_bias"], "abstain")

    def test_h4_used_when_h1_absent(self) -> None:
        cf = self._cf(_payload("bearish", timeframe="H4"))
        self.assertEqual(cf["cond_tf"], "H4")
        self.assertEqual(cf["cond_would_side"], "NO")

    def test_other_product_does_not_leak(self) -> None:
        cf = self._cf(_payload("bullish", product_id="ETH-USD"))
        self.assertEqual(cf["cond_bias"], "abstain")

    def test_stale_flag_carried(self) -> None:
        cf = self._cf(_payload("bullish", stale_invalidation=1))
        self.assertTrue(cf["cond_stale"])

    def test_get_conditional_reads_filters_by_product(self) -> None:
        payload = {"conditional_reads": {"reads": [
            {"product_id": "BTC-USD", "timeframe": "H1", "bias": "bullish"},
            {"product_id": "ETH-USD", "timeframe": "H1", "bias": "bearish"},
        ]}}
        with mock.patch.object(eva_intel, "_payload", return_value=payload):
            reads = eva_intel.get_conditional_reads("ETH-USD")
        self.assertEqual(set(reads), {"H1"})
        self.assertEqual(reads["H1"]["bias"], "bearish")

    def test_conditional_block_does_not_affect_get_stances(self) -> None:
        """The scalar contract the bot actually gates on must be untouched."""
        payload = {
            "stances": [
                {"product_id": "BTC-USD", "timeframe": tf, "stance": "bullish",
                 "confidence": 0.7, "created_at": "2026-09-17T12:00:00Z"}
                for tf in ("H4", "H1", "M15")
            ],
            "conditional_reads": {"reads": [
                {"product_id": "BTC-USD", "timeframe": "H1", "bias": "bearish"}
            ]},
        }
        from datetime import datetime, timezone

        with mock.patch.object(eva_intel, "_payload", return_value=payload):
            stances = eva_intel.get_stances(
                "BTC-USD", now=datetime(2026, 9, 17, 12, 5, tzinfo=timezone.utc)
            )
        assert stances is not None
        self.assertEqual(stances["H1"]["stance"], "bullish")


if __name__ == "__main__":
    unittest.main()
