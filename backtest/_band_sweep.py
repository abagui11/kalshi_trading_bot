"""One-off: sweep TP/SL bands for eva_streak MID entry (Q3: redesign bands?).

Usage: python -m backtest._band_sweep [days]
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import backtest.eva_streak_bt as bt
from backtest.dan_rules_study import load_m5, resample_15m

COMBOS = [
    # (tp_mult, sl_frac, label)   entry ~= mid (~35-60c typically)
    (2.0, 0.5, "current 2.0x/0.5x"),
    (999.0, 0.0, "no TP, no SL (settle)"),
    (999.0, 0.5, "no TP, SL 0.5x"),
    (1.5, 0.5, "TP 1.5x, SL 0.5x"),
    (1.4, 0.6, "TP 1.4x, SL 0.6x"),
    (1.6, 0.4, "TP 1.6x, SL 0.4x"),
    (1.5, 0.0, "TP 1.5x, no SL"),
    (1.3, 0.5, "TP 1.3x, SL 0.5x"),
]


def main() -> None:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    quotes = os.path.join(os.environ.get("TEMP", "/tmp"), "kalshi_quotes.json")
    slope = bt.calibrate_slope(quotes if os.path.exists(quotes) else None)
    print(f"slope={slope:.1f} c/1%  mode=mid  days={days:.0f}")

    data = []
    for product, coinbase in (("BTC", "BTC-USD"), ("ETH", "ETH-USD")):
        m5 = load_m5(product, coinbase, days)
        c15 = resample_15m(m5)
        for c in c15:
            if not isinstance(c["ts"], datetime):
                c["ts"] = bt.parse_ts(c["ts"])
        data.append((product, c15, m5))

    print(f"\n{'bands':<24} {'n':>5} {'win':>5} {'net$':>8} {'ROI':>7} "
          f"{'tp%':>4} {'sl%':>4} {'stl%':>5}")
    for tp, sl, label in COMBOS:
        bt.TP_MULT = tp if tp else 999.0
        bt.SL_FRAC = sl
        trades = []
        for product, c15, m5 in data:
            for i in range(bt.MIN_RUN + 1, len(c15)):
                t = bt.simulate_window(product, c15, i, m5, slope, mode="mid")
                if t is not None:
                    trades.append(t)
        n = len(trades)
        if not n:
            print(f"{label:<24} {'0':>5}")
            continue
        wins = sum(1 for t in trades if t.pnl > 0)
        net = sum(t.pnl for t in trades)
        cost = sum(t.entry / 100.0 for t in trades)
        kinds = {k: sum(1 for t in trades if t.exit_kind == k) for k in ("tp", "sl", "settle")}
        print(
            f"{label:<24} {n:>5} {wins/n:>5.0%} {net:>+8.2f} {net/cost:>+7.1%} "
            f"{kinds['tp']/n:>4.0%} {kinds['sl']/n:>4.0%} {kinds['settle']/n:>5.0%}"
        )


if __name__ == "__main__":
    main()
