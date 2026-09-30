#!/usr/bin/env python3
"""Which orderbook array is which? Decided against the quoted touch.

The market payload publishes yes_bid/yes_ask. The orderbook publishes two
arrays. Exactly one mapping of array -> side reproduces the quoted touch; the
other produces a price on the wrong side of the spread, which is how a paper
fill ends up better than the best offer.

    /opt/kalshi-15m-bot/.venv/bin/python deploy/_ladder_orientation.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/kalshi-15m-bot")

import bot_config  # noqa: E402
import kalshi_client  # noqa: E402


def cents(v):
    return None if v is None else round(float(v) * 100.0, 4)


for series in bot_config.active_series():
    try:
        markets = kalshi_client.get_open_markets(series)
    except Exception as exc:  # pragma: no cover - diagnostics
        print(f"{series}: markets unavailable ({exc})")
        continue
    if not markets:
        print(f"{series}: no open market")
        continue
    m = markets[0]
    ticker = m.get("ticker")
    yes_bid = cents(m.get("yes_bid_dollars"))
    yes_ask = cents(m.get("yes_ask_dollars"))
    print(f"\n=== {ticker}")
    print(f"  quoted: yes_bid {yes_bid}  yes_ask {yes_ask}")

    try:
        data = kalshi_client.request("GET", f"/markets/{ticker}/orderbook", auth=True)
    except Exception as exc:  # pragma: no cover - diagnostics
        print(f"  orderbook fetch failed: {exc}")
        continue
    book = data.get("orderbook_fp") or data.get("orderbook") or data
    if not isinstance(book, dict):
        print(f"  unexpected orderbook shape: {type(book)}")
        continue
    print(f"  orderbook keys: {sorted(book)}")

    for key in ("yes_dollars", "no_dollars", "yes", "no"):
        levels = book.get(key)
        if not levels:
            continue
        scale = 100.0 if key.endswith("_dollars") else 1.0
        px = [round(float(lv[0]) * scale, 4) for lv in levels]
        sz = [float(lv[1]) for lv in levels]
        print(f"  {key:<12} n={len(levels):<4} "
              f"min={min(px)} max={max(px)} "
              f"| first={px[0]}x{sz[0]} last={px[-1]}x{sz[-1]}")
        # If these are resting bids, the best is the HIGHEST price, and the
        # opposite side's ask is 100 - that.
        print(f"               as bids -> best {max(px)}, "
              f"implies opposite ask {round(100.0 - max(px), 4)}")
        print(f"               as asks -> best {min(px)}")
