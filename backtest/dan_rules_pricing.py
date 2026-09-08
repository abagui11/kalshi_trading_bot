"""Join recorded Kalshi quotes with candle streak state -> EV of Dan's rules.

Uses %TEMP%/kalshi_quotes.json (deploy/_dump_quotes.py output from the VPS)
plus the cached M5 candles from backtest/dan_rules_study.py.

For every quote snapshot taken near a window open (13-15 min to expiry), we
compute the 15m candle streak state just before the window and ask: what did
Kalshi charge for the reversal side, and what settled?
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest.dan_rules_study import direction, load_m5, resample_15m

EXPORTS = Path(__file__).resolve().parent.parent / "exports"


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> None:
    qp = os.path.join(os.environ["TEMP"], "kalshi_quotes.json")
    raw = open(qp, "rb").read()
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    quotes = json.loads(raw.decode(enc))
    print(f"quote snapshots: {len(quotes)}")

    candles = {}
    for product, coinbase in (("BTC", "BTC-USD"), ("ETH", "ETH-USD")):
        c15 = resample_15m(load_m5(product, coinbase, 60))
        candles[product] = {c["ts"]: c for c in c15}

    # One quote per market: the earliest snapshot with >= 12 min to expiry.
    best: dict[str, dict] = {}
    for q in quotes:
        if q["seconds_to_expiry"] is None or q["seconds_to_expiry"] < 720:
            continue
        t = q["market_ticker"]
        if t not in best or q["seconds_to_expiry"] > best[t]["seconds_to_expiry"]:
            best[t] = q

    print(f"windows with an early quote: {len(best)}")

    # Streak state at window open + settle from candles.
    stats = defaultdict(lambda: {"n": 0, "cost": 0.0, "payout": 0.0, "wins": 0})
    for t, q in best.items():
        product = q["product_id"]
        book = candles.get(product)
        if not book:
            continue
        ts = parse_ts(q["ts"])
        wopen = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        window = book.get(wopen)
        if window is None:
            continue
        # streak of consecutive same-direction candles immediately before wopen
        k, d = 0, 0
        cur = wopen - timedelta(minutes=15)
        while True:
            c = book.get(cur)
            if c is None:
                break
            dd = direction(c)
            if dd == 0:
                break
            if d == 0:
                d = dd
            if dd != d:
                break
            k += 1
            cur -= timedelta(minutes=15)
            if k >= 6:
                break
        if d == 0 or k < 2:
            continue
        settle_up = window["close"] > window["open"]
        # Reversal trade: buy UP after down-streak (price = yes_mid), buy DOWN
        # after up-streak (price = 100 - yes_mid).
        yes_mid = float(q["yes_mid_cents"])
        if d == -1:
            price, win = yes_mid, settle_up
        else:
            price, win = 100.0 - yes_mid, not settle_up
        for kk in (2, 3, 4):
            if k >= kk:
                key = (product, f"k>={kk}", "buy UP" if d == -1 else "buy DOWN")
                s = stats[key]
                s["n"] += 1
                s["cost"] += price
                s["wins"] += win
                s["payout"] += 100.0 if win else 0.0

    print(f'\n{"cohort":<38} {"n":>4} {"hit":>6} {"avg price":>10} {"EV/contract":>12} {"ROI":>8}')
    for key in sorted(stats):
        s = stats[key]
        if s["n"] == 0:
            continue
        avg_p = s["cost"] / s["n"]
        ev = (s["payout"] - s["cost"]) / s["n"]
        roi = (s["payout"] - s["cost"]) / s["cost"] * 100
        label = f"{key[0]} {key[1]} {key[2]}"
        print(f"{label:<38} {s['n']:>4} {s['wins']/s['n']:>6.0%} {avg_p:>9.1f}c {ev:>+11.1f}c {roi:>+7.1f}%")

    # Combined (both products, both directions)
    for kk in (2, 3, 4):
        n = sum(s["n"] for key, s in stats.items() if key[1] == f"k>={kk}")
        cost = sum(s["cost"] for key, s in stats.items() if key[1] == f"k>={kk}")
        pay = sum(s["payout"] for key, s in stats.items() if key[1] == f"k>={kk}")
        wins = sum(s["wins"] for key, s in stats.items() if key[1] == f"k>={kk}")
        if n:
            print(
                f"{'ALL k>=' + str(kk):<38} {n:>4} {wins/n:>6.0%} {cost/n:>9.1f}c "
                f"{(pay-cost)/n:>+11.1f}c {(pay-cost)/cost*100:>+7.1f}%"
            )


if __name__ == "__main__":
    main()
