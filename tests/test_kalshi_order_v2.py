"""Tests for Kalshi Create Order V2 body mapping."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import config
import kalshi_client


class OrderV2BodyTests(unittest.TestCase):
    def test_yes_buy_maps_to_bid(self) -> None:
        body = kalshi_client.build_order_v2_body(
            "KXBTC15M-X",
            "YES",
            5,
            side_price_cents=40.0,
            client_order_id="cid-1",
        )
        self.assertEqual(body["side"], "bid")
        self.assertEqual(body["price"], "0.4000")
        self.assertEqual(body["count"], "5.00")
        self.assertEqual(body["ticker"], "KXBTC15M-X")
        self.assertEqual(body["client_order_id"], "cid-1")
        self.assertEqual(body["time_in_force"], "immediate_or_cancel")
        self.assertEqual(body["self_trade_prevention_type"], "taker_at_cross")

    def test_no_buy_maps_to_ask_at_complement(self) -> None:
        # Buy NO @ 30¢ ≡ sell YES @ 70¢
        body = kalshi_client.build_order_v2_body(
            "KXETH15M-X",
            "NO",
            3,
            side_price_cents=30.0,
            client_order_id="cid-2",
        )
        self.assertEqual(body["side"], "ask")
        self.assertEqual(body["price"], "0.7000")
        self.assertEqual(body["count"], "3.00")

    def test_filled_contract_count_parses_fp(self) -> None:
        self.assertEqual(
            kalshi_client.filled_contract_count({"fill_count": "5.00"}), 5.0
        )
        self.assertEqual(kalshi_client.filled_contract_count({"fill_count": "0.00"}), 0.0)
        self.assertEqual(kalshi_client.filled_contract_count({"count": 2}), 2.0)
        self.assertEqual(kalshi_client.filled_contract_count({}), 0.0)

    def test_place_order_posts_events_orders_path(self) -> None:
        captured: dict = {}

        def fake_request(method, path, *, json_body=None, auth=True):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = json_body
            return {
                "order_id": "oid",
                "fill_count": "5.00",
                "remaining_count": "0.00",
                "ts_ms": 1,
            }

        with patch.object(config, "KALSHI_PAPER_ONLY", False):
            with patch.object(kalshi_client, "request", side_effect=fake_request):
                with patch("kalshi_sizing.assert_order_allowed"):
                    resp = kalshi_client.place_order(
                        "KXBTC15M-X", "NO", 5, yes_price_cents=25
                    )
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/portfolio/events/orders")
        self.assertEqual(captured["body"]["side"], "ask")
        self.assertEqual(captured["body"]["price"], "0.7500")
        self.assertEqual(resp["order_id"], "oid")


if __name__ == "__main__":
    unittest.main()
