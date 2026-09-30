"""Sample the altcoin 15m books every 30s and write a CSV. Read-only.

Answers the one thing the backtest cannot: in the 4-10 minute band the wick
rule trades, how wide are these books and how much size rests at the touch?
That is what decides whether the altcoin clones ever get a full clip.

    python deploy/_alt_book_sample.py 20   # minutes to sample
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot_config  # noqa: E402
import kalshi_client  # noqa: E402
import kalshi_triggers  # noqa: E402

OUT = Path(__file__).resolve().parent / "_alt_book_sample.csv"
SERIES = ["KXBTC15M", "KXETH15M"] + list(bot_config.ALT_WICK_VARIANTS.values())


def main() -> None:
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    deadline = time.time() + minutes * 60.0
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "ts", "asset", "ticker", "min_left", "yes_bid", "yes_ask",
            "spread_c", "fav_side", "fav_mid_c", "fav_ask_c", "fav_depth_ct",
            "in_band", "in_clock",
        ])
        while time.time() < deadline:
            now = datetime.now(timezone.utc)
            for series in SERIES:
                try:
                    markets = kalshi_client.get_open_markets(series)
                except Exception:
                    continue
                if not markets:
                    continue
                m = markets[0]
                mid = kalshi_client.mid_cents_from_market(m)
                expiry = m.get("close_time") or m.get("expected_expiration_time")
                left = kalshi_triggers.minutes_to_expiry(str(expiry), now=now)
                if mid is None or left is None:
                    continue
                side = "YES" if mid >= 50.0 else "NO"
                fav_mid = kalshi_triggers.side_mid_cents(side, mid)
                fav_ask = kalshi_client.side_ask_cents_from_market(side, m)
                depth = kalshi_client.side_ask_depth_contracts(side, m)
                secs = left * 60.0
                w.writerow([
                    now.strftime("%H:%M:%S"),
                    bot_config.series_product(series),
                    m.get("ticker"),
                    round(left, 2),
                    m.get("yes_bid_dollars"),
                    m.get("yes_ask_dollars"),
                    kalshi_client.spread_cents_from_market(m),
                    side,
                    round(fav_mid, 1),
                    None if fav_ask is None else round(fav_ask, 1),
                    None if depth is None else round(depth),
                    int(bot_config.EVA_FAV_MIN_ENTRY_CENTS
                        <= fav_mid <= bot_config.EVA_FAV_MAX_ENTRY_CENTS),
                    int(bot_config.EVA_FAV_MIN_SECONDS_LEFT
                        <= secs <= bot_config.EVA_FAV_MAX_SECONDS_LEFT),
                ])
            fh.flush()
            time.sleep(30)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
