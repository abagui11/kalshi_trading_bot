"""Path-aware backtest of the deployed eva_streak mechanics.

Uses Coinbase M5 bars as a proxy for Kalshi window dynamics:

* Strike = window open (Coinbase close at :00/:15/:30/:45).
* YES mid proxy from spot-vs-strike via a logistic calibrated on the
  recorded VPS quotes (deploy/_dump_quotes.py → %TEMP%/kalshi_quotes.json)
  when available; otherwise a default slope of 80 (% excursion → cents).
* Signal: ≥3 consecutive same-direction 15m candles ending one slot before
  the window, last candle sweeps the prior extreme (matches eva_streak.py).
* Entry: resting limit at min(35¢, open_side_mid − 2¢); fills on the first
  M5 tick where side mid ≤ limit, before T−3m.
* Exits after fill: TP at 2× entry, SL at 0.5× entry, else settle.
* Size: 1 contract (matches live MAX_CONTRACTS).

Usage:  python -m backtest.eva_streak_bt [days]
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest.dan_rules_study import direction, load_m5, resample_15m

LIMIT_CENTS = 35.0
LIMIT_DISCOUNT = 2.0
MIN_SIDE_MID = 20.0
MIN_RUN = 3
TP_MULT = 2.0
SL_FRAC = 0.5
CANCEL_MIN = 3.0
DEFAULT_SLOPE = 80.0  # cents per 1% spot-vs-strike


def parse_ts(s) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def calibrate_slope(quotes_path: str | None) -> float:
    """OLS of (yes_mid − 50) on spot_vs_strike_pct from early-window quotes."""
    if not quotes_path or not os.path.exists(quotes_path):
        return DEFAULT_SLOPE
    raw = open(quotes_path, "rb").read()
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    quotes = json.loads(raw.decode(enc))
    xs, ys = [], []
    for q in quotes:
        if q.get("yes_mid_cents") is None or q.get("spot") is None or q.get("strike") is None:
            continue
        ste = q.get("seconds_to_expiry")
        if ste is not None and float(ste) < 600:  # early window only (>=10m left)
            continue
        strike = float(q["strike"])
        if strike <= 0:
            continue
        exc = (float(q["spot"]) / strike - 1.0) * 100.0
        if abs(exc) > 0.8:
            continue
        xs.append(exc)
        ys.append(float(q["yes_mid_cents"]) - 50.0)
    if len(xs) < 50:
        return DEFAULT_SLOPE
    num = sum(x * y for x, y in zip(xs, ys))
    den = sum(x * x for x in xs)
    slope = num / den if den else DEFAULT_SLOPE
    return max(40.0, min(140.0, slope))


def yes_mid_proxy(spot: float, strike: float, slope: float) -> float:
    if strike <= 0:
        return 50.0
    exc = (spot / strike - 1.0) * 100.0
    return max(1.0, min(99.0, 50.0 + slope * exc))


def side_mid(side: str, yes: float) -> float:
    return yes if side == "YES" else 100.0 - yes


@dataclass
class Trade:
    product: str
    window_open: datetime
    side: str
    run: int
    entry: float
    fill_min: float
    exit: float
    exit_kind: str  # tp | sl | settle
    pnl: float
    settle_pnl: float  # counterfactual: always hold to settle


def detect_at(c15: list[dict], i: int) -> tuple[int, int, bool] | None:
    """Streak ending at candle i-1 (window open = c15[i].ts)."""
    if i < MIN_RUN + 1:
        return None
    d = direction(c15[i - 1])
    if d == 0:
        return None
    run = 1
    for j in range(i - 2, max(-1, i - 1 - 8), -1):
        if direction(c15[j]) != d:
            break
        run += 1
    if run < MIN_RUN:
        return None
    # Exact-k boundary: candle before the run must differ (same as study).
    # We still take run >= MIN_RUN even if longer.
    last, prev = c15[i - 1], c15[i - 2]
    swept = last["low"] < prev["low"] if d == -1 else last["high"] > prev["high"]
    if not swept:
        return None
    return run, d, True


def m5_path(m5: list[dict], open_ts: datetime, expiry: datetime) -> list[tuple[datetime, float]]:
    """(ts, close) for M5 bars strictly inside (open, expiry − CANCEL_MIN]."""
    cancel = expiry - timedelta(minutes=CANCEL_MIN)
    out = []
    for b in m5:
        ts = parse_ts(b["ts"])
        if open_ts < ts <= cancel:
            out.append((ts, float(b["close"])))
    return out


def simulate_window(
    product: str,
    c15: list[dict],
    i: int,
    m5: list[dict],
    slope: float,
    *,
    mode: str = "limit",
) -> Trade | None:
    hit = detect_at(c15, i)
    if hit is None:
        return None
    run, d, _ = hit
    side = "YES" if d == -1 else "NO"
    w = c15[i]
    open_ts = w["ts"] if isinstance(w["ts"], datetime) else parse_ts(w["ts"])
    expiry = open_ts + timedelta(minutes=15)
    strike = float(w["open"])

    open_spot = strike
    for b in m5:
        ts = parse_ts(b["ts"])
        if ts == open_ts or (open_ts < ts <= open_ts + timedelta(minutes=5)):
            open_spot = float(b["close"])
            break
    open_yes = yes_mid_proxy(open_spot, strike, slope)
    open_side = side_mid(side, open_yes)
    if open_side < MIN_SIDE_MID:
        return None

    path = m5_path(m5, open_ts, expiry)

    if mode == "mid":
        # Take the open mid immediately (no limit patience).
        fill_px = open_side
        fill_min = 0.0
        fill_idx = -1
    else:
        limit = min(LIMIT_CENTS, open_side - LIMIT_DISCOUNT)
        if limit < 5.0:
            return None
        fill_px = None
        fill_min = None
        fill_idx = None
        for idx, (ts, spot) in enumerate(path):
            yes = yes_mid_proxy(spot, strike, slope)
            sm = side_mid(side, yes)
            if sm <= limit + 1e-9:
                fill_px = limit
                fill_min = (ts - open_ts).total_seconds() / 60.0
                fill_idx = idx
                break
        if fill_px is None:
            return None

    tp_at = fill_px * TP_MULT
    sl_at = fill_px * SL_FRAC
    exit_px = None
    exit_kind = None
    for ts, spot in path[fill_idx + 1 :]:
        yes = yes_mid_proxy(spot, strike, slope)
        sm = side_mid(side, yes)
        if sm >= tp_at:
            exit_px, exit_kind = sm, "tp"
            break
        if sm <= sl_at:
            exit_px, exit_kind = sm, "sl"
            break
    won = (float(w["close"]) > float(w["open"])) == (side == "YES")
    settle_px = 100.0 if won else 0.0
    settle_pnl = (settle_px - fill_px) / 100.0
    if exit_px is None:
        exit_px, exit_kind = settle_px, "settle"
    pnl = (exit_px - fill_px) / 100.0
    return Trade(
        product=product,
        window_open=open_ts,
        side=side,
        run=run,
        entry=fill_px,
        fill_min=fill_min or 0.0,
        exit=exit_px,
        exit_kind=exit_kind,
        pnl=pnl,
        settle_pnl=settle_pnl,
    )


def summarize(trades: list[Trade], label: str) -> None:
    if not trades:
        print(f"\n{label}: no fills")
        return
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    net = sum(t.pnl for t in trades)
    cost = sum(t.entry / 100.0 for t in trades)
    by_exit = defaultdict(list)
    for t in trades:
        by_exit[t.exit_kind].append(t)
    # Counterfactual: hold every fill to settlement (ignore TP/SL path exits).
    # Reconstruct settle from exit when settle, else from pnl sign if we stored it —
    # we didn't; approximate by re-deriving from recorded exit_kind==settle only is
    # incomplete. Store settle_pnl on Trade instead — see simulate_window.
    settle_pnls = [t.settle_pnl for t in trades]
    settle_net = sum(settle_pnls)
    settle_wins = sum(1 for p in settle_pnls if p > 0)
    print(f"\n=== {label} ===")
    print(
        f"fills={n}  win={len(wins)/n:.0%}  net=${net:+.2f}  "
        f"ROI={net/cost:+.1%}  avg entry={sum(t.entry for t in trades)/n:.1f}c"
    )
    print(
        f"  hold-to-settle counterfactual: win={settle_wins/n:.0%}  "
        f"net=${settle_net:+.2f}  ROI={settle_net/cost:+.1%}"
    )
    for kind in ("tp", "sl", "settle"):
        g = by_exit.get(kind, [])
        if not g:
            continue
        w = sum(1 for t in g if t.pnl > 0)
        print(
            f"  {kind:<7} n={len(g):>4} ({len(g)/n:.0%})  "
            f"win={w/len(g):.0%}  net=${sum(t.pnl for t in g):+.2f}  "
            f"avg pnl=${sum(t.pnl for t in g)/len(g):+.3f}"
        )
    by_side = defaultdict(list)
    for t in trades:
        by_side[t.side].append(t)
    for side, g in sorted(by_side.items()):
        w = sum(1 for t in g if t.pnl > 0)
        print(
            f"  {side:<7} n={len(g):>4}  win={w/len(g):.0%}  "
            f"net=${sum(t.pnl for t in g):+.2f}"
        )


def signal_funnel(c15: list[dict], product: str) -> int:
    signals = 0
    for i in range(MIN_RUN + 1, len(c15)):
        if detect_at(c15, i) is not None:
            signals += 1
    print(f"{product}: streak+sweep signals = {signals} / {len(c15)} windows")
    return signals


def main() -> None:
    global TP_MULT, SL_FRAC
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    if len(sys.argv) > 3:  # optional: days tp_mult sl_frac (0 disables)
        TP_MULT = float(sys.argv[2]) or 999.0
        SL_FRAC = float(sys.argv[3])
    quotes = os.path.join(os.environ.get("TEMP", "/tmp"), "kalshi_quotes.json")
    slope = calibrate_slope(quotes if os.path.exists(quotes) else None)
    print(f"yes_mid slope calibrated to {slope:.1f} c per 1% spot-vs-strike")
    print(
        f"rules: run>={MIN_RUN}+sweep, limit=min({LIMIT_CENTS:.0f}, mid-{LIMIT_DISCOUNT:.0f}), "
        f"TP={TP_MULT}x SL={SL_FRAC}x cancel T-{CANCEL_MIN:.0f}m, 1 ct"
    )

    all_trades: list[Trade] = []
    for mode in ("limit", "mid"):
        print(f"\n######## MODE: {mode} ########")
        mode_trades: list[Trade] = []
        for product, coinbase in (("BTC", "BTC-USD"), ("ETH", "ETH-USD")):
            m5 = load_m5(product, coinbase, days)
            c15 = resample_15m(m5)
            for c in c15:
                if not isinstance(c["ts"], datetime):
                    c["ts"] = parse_ts(c["ts"])
            n_sig = signal_funnel(c15, product)
            trades = []
            for i in range(MIN_RUN + 1, len(c15)):
                t = simulate_window(product, c15, i, m5, slope, mode=mode)
                if t is not None:
                    trades.append(t)
            if mode == "limit":
                fill_rate = len(trades) / n_sig if n_sig else 0.0
                print(
                    f"{product}: fill rate among signals = "
                    f"{fill_rate:.0%} ({len(trades)}/{n_sig})"
                )
            summarize(trades, f"{product} {mode} (~{days:.0f}d)")
            mode_trades.extend(trades)
        summarize(mode_trades, f"ALL {mode} (~{days:.0f}d)")
        if mode == "limit":
            all_trades = mode_trades

    if all_trades:
        by_day = defaultdict(list)
        for t in all_trades:
            by_day[t.window_open.strftime("%Y-%m-%d")].append(t)
        recent = sorted(by_day)[-7:]
        print("\n=== last 7 days (limit mode) ===")
        for day in recent:
            g = by_day[day]
            w = sum(1 for t in g if t.pnl > 0)
            print(
                f"  {day}  n={len(g):>3} win={w/len(g):.0%} "
                f"net=${sum(t.pnl for t in g):+.2f}"
            )


if __name__ == "__main__":
    main()
