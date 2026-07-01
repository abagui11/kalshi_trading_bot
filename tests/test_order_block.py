"""Tests for order block fib zones and H1/H12 matching."""

from __future__ import annotations

import pytest

from patterns.order_block import (
    OrderBlock,
    fib_level,
    fib_zone_bounds,
    find_matching_h1_ob,
    format_ob_with_fib,
    price_in_fib_zone,
    zones_overlap,
)


def test_bullish_fib_zone_bounds():
    z_low, z_high = fib_zone_bounds("bullish", 1554.47, 1586.51)
    assert z_low == pytest.approx(1574.27, abs=0.02)
    assert z_high == pytest.approx(1579.65, abs=0.02)


def test_1569_not_in_h12_ob_fib_sweet_spot():
    assert not price_in_fib_zone(1569.0, "bullish", 1554.47, 1586.51)


def test_1569_inside_full_ob_but_outside_fib():
    assert 1554.47 <= 1569.0 <= 1586.51
    assert not price_in_fib_zone(1569.0, "bullish", 1554.47, 1586.51)


def test_fib_level_bullish_0618():
    assert fib_level("bullish", 2380.0, 2420.0, 0.618) == pytest.approx(2404.72, abs=0.02)


def test_zones_overlap():
    assert zones_overlap(1554.0, 1586.0, 1554.47, 1586.51)
    assert not zones_overlap(1554.0, 1586.0, 1600.0, 1650.0)


def test_find_matching_h1_ob_by_bounds():
    h1_ob = OrderBlock(
        direction="bullish",
        low=1570.0,
        high=1590.0,
        start_ts="2026-06-28T08:00:00Z",
        end_ts="2026-06-28T08:00:00Z",
        displacement_ts="2026-06-28T12:00:00Z",
    )
    ob_dict = {"low": 1570.0, "high": 1590.0, "start_ts": "2026-06-28T08:00:00Z"}
    match = find_matching_h1_ob(ob_dict, [h1_ob], "bullish")
    assert match is h1_ob


def test_h12_bounds_do_not_match_unrelated_h1_ob():
    h1_ob = OrderBlock(
        direction="bullish",
        low=1570.0,
        high=1590.0,
        start_ts="2026-06-28T08:00:00Z",
        end_ts="2026-06-28T08:00:00Z",
        displacement_ts="2026-06-28T12:00:00Z",
    )
    h12_as_ob_dict = {"low": 1554.47, "high": 1586.51, "start_ts": "2026-06-28T10:00:00Z"}
    assert find_matching_h1_ob(h12_as_ob_dict, [h1_ob], "bullish") is None


def test_format_ob_with_fib_includes_h1_label_context():
    ob = OrderBlock(
        direction="bullish",
        low=2380.0,
        high=2420.0,
        start_ts="2026-06-20T12:00:00Z",
        end_ts="2026-06-20T12:00:00Z",
        displacement_ts="2026-06-20T14:00:00Z",
    )
    text = format_ob_with_fib(ob)
    assert "H1 OB" in text
    assert "2,404.72" in text
