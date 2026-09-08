"""Backtest Dan's 2026-09-07 rule ideas on historical Coinbase candles.

Rules under test (window-level proxies, strike = window open, settle = close):

A. Streak reversion — after k consecutive down (up) 15m candles, buy the
   opposite side of the next window. Variants: with/without a "sweep" (the
   last streak candle took out the prior candle's extreme).
B. Calm after the storm — after a big 1h move followed by 2 quiet candles
   (no new extreme), bias opposite the move for the next 4 windows.

Usage:  python -m backtest.dan_rules_study [days]
Caches M5 bars in exports/dan_study_m5_{product}.json.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research

EXPORTS = Path(__file__).resolve().parent.parent / "exports"

PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def load_m5(product: str, coinbase: str, days: float) -> list[dict]:
    cache = EXPORTS / f"dan_study_m5_{product}.json"
    if cache.exists():
        bars = json.loads(cache.read_text())
        if bars:
            newest = datetime.fromisoformat(str(bars[-1]["ts"]).replace("Z", "+00:00"))
            oldest = datetime.fromisoformat(str(bars[0]["ts"]).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if (now - newest) < timedelta(hours=2) and (now - oldest) > timedelta(
                days=days - 1
            ):
                return bars
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - int(days * 86400)
    bars = research.fetch_coinbase_candles_range(
        "FIVE_MINUTE", start, end, product_id=coinbase
    )
    bars = sorted(bars, key=lambda b: str(b["ts"]))
    cache.write_text(json.dumps(bars))
    return bars


def resample_15m(m5: list[dict]) -> list[dict]:
    """Aggregate M5 into :00/:15/:30/:45-aligned 15m candles."""
    buckets: dict[datetime, list[dict]] = defaultdict(list)
    for b in m5:
        ts = datetime.fromisoformat(str(b["ts"]).replace("Z", "+00:00"))
        key = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        buckets[key].append(b)
    out = []
    for key in sorted(buckets):
        bs = buckets[key]
        if len(bs) < 3:
            continue
        out.append(
            {
                "ts": key,
                "open": float(bs[0]["open"]),
                "high": max(float(x["high"]) for x in bs),
                "low": min(float(x["low"]) for x in bs),
                "close": float(bs[-1]["close"]),
            }
        )
    return out


def direction(c: dict) -> int:
    """+1 up candle, -1 down candle, 0 doji (vs candle open)."""
    if c["close"] > c["open"]:
        return 1
    if c["close"] < c["open"]:
        return -1
    return 0


def kalshi_result(c: dict) -> int:
    """Window settles YES (+1) when close > open (strike = window open)."""
    return 1 if c["close"] > c["open"] else -1


def pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "n/a"


def streak_study(c15: list[dict], product: str) -> None:
    print(f"\n=== {product}: A. streak reversion (buy opposite after k straight candles) ===")
    print("entry window result = close vs open of the NEXT candle (Kalshi settle proxy)\n")
    header = f'{"k":<3} {"n":>5} {"reversal wins":>13} {"hit rate":>9} {"sweep n":>8} {"sweep hit":>10} {"no-sweep hit":>13}'
    print(header)
    for k in (2, 3, 4, 5):
        n = wins = 0
        sw_n = sw_w = nsw_n = nsw_w = 0
        for i in range(k + 1, len(c15) - 1):
            streak = [direction(c15[j]) for j in range(i - k, i)]
            if 0 in streak:
                continue
            if len(set(streak)) != 1:
                continue
            # streak must be exactly k (candle before it differs)
            if direction(c15[i - k - 1]) == streak[0]:
                continue
            d = streak[0]
            res = kalshi_result(c15[i])  # next window after the streak
            win = res == -d  # reversal
            n += 1
            wins += win
            # sweep: last streak candle took out the prior candle's extreme
            last, prev = c15[i - 1], c15[i - 2]
            swept = last["low"] < prev["low"] if d == -1 else last["high"] > prev["high"]
            if swept:
                sw_n += 1
                sw_w += win
            else:
                nsw_n += 1
                nsw_w += win
        print(
            f"{k:<3} {n:>5} {wins:>13} {pct(wins, n):>9} {sw_n:>8} "
            f"{pct(sw_w, sw_n):>10} {pct(nsw_w, nsw_n):>13}"
        )

    # Dan's exact phrasing: 3 down candles -> slam the UP. Directional split.
    print("\nDirectional split at k=3:")
    for d, name in ((-1, "3 down -> buy UP"), (1, "3 up -> buy DOWN")):
        n = wins = 0
        for i in range(4, len(c15) - 1):
            streak = [direction(c15[j]) for j in range(i - 3, i)]
            if streak != [d, d, d]:
                continue
            if direction(c15[i - 4]) == d:
                continue
            n += 1
            wins += kalshi_result(c15[i]) == -d
        print(f"  {name:<20} n={n:>4} hit={pct(wins, n)}")


def h1_returns(c15: list[dict], i: int) -> float:
    """Trailing 1h return ending at candle i (inclusive), %."""
    if i < 3:
        return 0.0
    base = c15[i - 3]["open"]
    return (c15[i]["close"] / base - 1.0) * 100.0 if base else 0.0


def calm_study(c15: list[dict], product: str, big_1h: float = 0.6) -> None:
    print(f"\n=== {product}: B. calm-after-storm (|1h move| >= {big_1h}%, then 2 quiet candles) ===")
    print("bias = opposite the storm; entry = each of the next 4 windows\n")
    n_events = 0
    win_by_offset: dict[int, list[int]] = defaultdict(list)
    continuation_events = 0
    for i in range(8, len(c15) - 6):
        move = h1_returns(c15, i)
        if abs(move) < big_1h:
            continue
        d = 1 if move > 0 else -1
        # storm extreme over candles i-3..i
        ext = (
            max(c15[j]["high"] for j in range(i - 3, i + 1))
            if d == 1
            else min(c15[j]["low"] for j in range(i - 3, i + 1))
        )
        # two quiet candles: no new extreme
        q1, q2 = c15[i + 1], c15[i + 2]
        if d == 1 and (q1["high"] > ext or q2["high"] > ext):
            continue
        if d == -1 and (q1["low"] < ext or q2["low"] < ext):
            continue
        # skip overlapping events (one entry set per storm)
        n_events += 1
        resumed = False
        for off in range(3, 7):  # the 4 windows after the 2 quiet candles
            if i + off >= len(c15):
                break
            res = kalshi_result(c15[i + off])
            win_by_offset[off].append(1 if res == -d else 0)
            # did the storm resume through the extreme?
            c = c15[i + off]
            if (d == 1 and c["high"] > ext) or (d == -1 and c["low"] < ext):
                resumed = True
        if resumed:
            continuation_events += 1
    print(f"events: {n_events} (storm resumed through the extreme within 4 windows: {continuation_events})")
    for off in sorted(win_by_offset):
        wins = win_by_offset[off]
        print(
            f"  window +{off - 2} after quiet-2: n={len(wins):>4} "
            f"opposite-side hit={pct(sum(wins), len(wins))}"
        )


def baseline(c15: list[dict], product: str) -> None:
    ups = sum(1 for c in c15 if kalshi_result(c) == 1)
    print(f"\n{product} baseline: {len(c15)} windows, up-close rate {pct(ups, len(c15))}")


def main() -> None:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    for product, coinbase in PRODUCTS.items():
        m5 = load_m5(product, coinbase, days)
        c15 = resample_15m(m5)
        print(f"\n{'=' * 70}")
        print(f"{product}: {len(m5)} M5 bars -> {len(c15)} 15m candles over ~{days:.0f} days")
        baseline(c15, product)
        streak_study(c15, product)
        calm_study(c15, product)
        calm_study(c15, product, big_1h=0.9)


if __name__ == "__main__":
    main()
