#!/usr/bin/env python3
"""Raw market payload + raw orderbook for one open market. Diagnostics only."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/kalshi-15m-bot")

import kalshi_client  # noqa: E402

series = sys.argv[1] if len(sys.argv) > 1 else "KXBTC15M"
markets = kalshi_client.get_open_markets(series)
m = markets[0]
ticker = m["ticker"]

print("=== market payload ===")
for k in sorted(m):
    if any(t in k for t in ("bid", "ask", "price", "ticker", "status", "close",
                            "expir", "volume", "interest", "strike")):
        print(f"  {k:<28} {m[k]}")

data = kalshi_client.request("GET", f"/markets/{ticker}/orderbook", auth=True)
print("\n=== orderbook response top-level keys ===")
print(" ", sorted(data))
book = data.get("orderbook_fp") or data.get("orderbook") or data
for key, levels in (book or {}).items():
    if not isinstance(levels, list):
        print(f"\n{key}: {levels!r}")
        continue
    print(f"\n{key}: {len(levels)} levels")
    print("  first 5 :", json.dumps(levels[:5]))
    print("  last 5  :", json.dumps(levels[-5:]))
