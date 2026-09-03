"""Fetch Coinbase candles for Sep 2 2026 and verify Kalshi 15m trade windows by strike."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent
BASE = "https://api.coinbase.com/api/v3/brokerage/market/products/{pid}/candles"

# UTC ranges
H1_START = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
LTF_START = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)


def fetch(pid: str, gran: str, start: datetime, end: datetime) -> list[dict]:
    out: list[dict] = []
    step = {"ONE_HOUR": 3600, "FIFTEEN_MINUTE": 900, "FIVE_MINUTE": 300}[gran]
    chunk = step * 300  # max ~350 candles/request
    s = int(start.timestamp())
    e = int(end.timestamp())
    while s < e:
        ce = min(s + chunk, e)
        r = requests.get(
            BASE.format(pid=pid),
            params={"start": str(s), "end": str(ce), "granularity": gran},
            timeout=30,
        )
        r.raise_for_status()
        out.extend(r.json().get("candles", []))
        s = ce
        time.sleep(0.2)
    # dedupe + sort ascending
    seen = {}
    for c in out:
        seen[int(c["start"])] = c
    return [seen[k] for k in sorted(seen)]


def main() -> None:
    data: dict[str, dict[str, list[dict]]] = {}
    for pid in ("BTC-USD", "ETH-USD"):
        data[pid] = {
            "H1": fetch(pid, "ONE_HOUR", H1_START, END),
            "M15": fetch(pid, "FIFTEEN_MINUTE", LTF_START, END),
            "M5": fetch(pid, "FIVE_MINUTE", LTF_START, END),
        }
        for tf, rows in data[pid].items():
            print(pid, tf, len(rows), "candles",
                  datetime.fromtimestamp(int(rows[0]["start"]), tz=timezone.utc) if rows else "-",
                  "->",
                  datetime.fromtimestamp(int(rows[-1]["start"]), tz=timezone.utc) if rows else "-")
    (OUT / "candles.json").write_text(json.dumps(data))

    # Trades: (label, product, side, strike, entry_cents, settle_ET_guess)
    trades = [
        ("T1 BTC Up 28c",   "BTC-USD", "UP",   77353.35, 28.0),
        ("T2 BTC Down 20.4c","BTC-USD", "DOWN", 77293.99, 20.4),
        ("T3 BTC Up 20.9c", "BTC-USD", "UP",   77179.48, 20.9),
        ("T4 BTC Up 29c",   "BTC-USD", "UP",   77203.93, 29.0),
        ("T5 BTC Up 37c",   "BTC-USD", "UP",   77344.21, 37.0),
        ("T6 ETH Up 38.6c", "ETH-USD", "UP",    2390.07, 38.6),
        ("T7 BTC Down 21c", "BTC-USD", "DOWN", 77367.46, 21.0),
        ("T8 BTC Up 32.5c", "BTC-USD", "UP",   77327.80, 32.5),
    ]
    print("\n=== strike matching: M5/M15 opens within $8 (BTC) / $1.5 (ETH) of strike ===")
    results = []
    for label, pid, side, strike, entry in trades:
        tol = 8.0 if pid == "BTC-USD" else 1.5
        matches = []
        for c in data[pid]["M5"]:
            o = float(c["open"])
            ts = int(c["start"])
            if abs(o - strike) <= tol and ts % 900 == 0:  # 15m boundary opens only
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                # window = ts .. ts+900; settle price = close of last M5 in window? use M5 closes
                w_closes = [float(x["close"]) for x in data[pid]["M5"] if ts <= int(x["start"]) < ts + 900]
                w_his = [float(x["high"]) for x in data[pid]["M5"] if ts <= int(x["start"]) < ts + 900]
                w_los = [float(x["low"]) for x in data[pid]["M5"] if ts <= int(x["start"]) < ts + 900]
                settle = w_closes[-1] if w_closes else None
                won = None
                if settle is not None:
                    won = (settle > strike) if side == "UP" else (settle < strike)
                matches.append({
                    "utc": dt.isoformat(),
                    "open": o,
                    "settle_close": settle,
                    "win_hi": max(w_his) if w_his else None,
                    "win_lo": min(w_los) if w_los else None,
                    "would_win": won,
                })
        print(f"\n{label} strike={strike} side={side}")
        for m in matches:
            print("  ", m)
        results.append({"label": label, "pid": pid, "side": side, "strike": strike,
                        "entry_cents": entry, "matches": matches})
    (OUT / "strike_matches.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
