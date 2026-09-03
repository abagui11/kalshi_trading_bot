"""Render marked H1/M15/M5 charts for the Sep 2 2026 Kalshi wick trades.

Pure-python (no pandas/numpy.random) reimplementation of pivot -> MSB -> order
block marking, mirroring patterns/htf_structure.py, since pandas is blocked by
the local Application Control policy.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

OUT = Path(__file__).resolve().parent
ET = timezone(timedelta(hours=-4))

data = json.loads((OUT / "candles.json").read_text())


def bars(pid: str, tf: str) -> list[dict]:
    out = []
    for c in data[pid][tf]:
        out.append({
            "ts": int(c["start"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })
    return out


def ema(closes: list[float], span: int) -> list[float]:
    k = 2.0 / (span + 1.0)
    out = []
    e = closes[0]
    for c in closes:
        e = c * k + e * (1 - k)
        out.append(e)
    return out


# --- simplified order blocks: pivots (2-2 fractal), MSB on close-through, OB =
# --- last opposite candle before MSB. Zones end when a close mitigates them.
def order_blocks(b: list[dict], min_width_pct: float = 0.10) -> list[dict]:
    n = len(b)
    pivots = []  # (idx, kind, price)
    for i in range(2, n - 2):
        if b[i]["high"] == max(x["high"] for x in b[i - 2:i + 3]):
            pivots.append((i, "high", b[i]["high"]))
        if b[i]["low"] == min(x["low"] for x in b[i - 2:i + 3]):
            pivots.append((i, "low", b[i]["low"]))
    zones: list[dict] = []
    for i in range(4, n):
        close = b[i]["close"]
        last_hi = max((p for p in pivots if p[1] == "high" and p[0] < i), key=lambda p: p[0], default=None)
        last_lo = max((p for p in pivots if p[1] == "low" and p[0] < i), key=lambda p: p[0], default=None)
        for piv, direction in ((last_hi, "bullish"), (last_lo, "bearish")):
            if piv is None:
                continue
            broke = close > piv[2] if direction == "bullish" else close < piv[2]
            if not broke:
                continue
            if any(z["direction"] == direction and z["msb_idx"] >= piv[0] for z in zones):
                continue  # one OB per swing break
            opp = "bearish" if direction == "bullish" else "bullish"
            for j in range(i - 1, max(i - 30, -1), -1):
                cdir = "bullish" if b[j]["close"] >= b[j]["open"] else "bearish"
                if cdir != opp:
                    continue
                lo, hi = b[j]["low"], b[j]["high"]
                if (hi - lo) / max(b[j]["close"], 1e-9) * 100 < min_width_pct:
                    break
                zones.append({"direction": direction, "low": lo, "high": hi,
                              "start_idx": j, "msb_idx": i, "end_idx": None})
                break
    # mitigation: close beyond the zone kills it
    for z in zones:
        for i in range(z["msb_idx"] + 1, n):
            c = b[i]["close"]
            dead = c < z["low"] if z["direction"] == "bullish" else c > z["high"]
            if dead:
                z["end_idx"] = i
                break
    return zones


def draw_candles(ax, b: list[dict], width: float = 0.6) -> None:
    for i, bar in enumerate(b):
        up = bar["close"] >= bar["open"]
        color = "#2e9e6b" if up else "#d9534f"
        ax.plot([i, i], [bar["low"], bar["high"]], color=color, lw=0.8, zorder=2)
        body_lo = min(bar["open"], bar["close"])
        h = abs(bar["close"] - bar["open"]) or bar["close"] * 1e-6
        ax.add_patch(Rectangle((i - width / 2, body_lo), width, h, facecolor=color,
                               edgecolor=color, zorder=3))


def et_label(ts: int, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ts, tz=ET).strftime(fmt)


TRADES = [
    dict(key="t1", pid="BTC-USD", side="UP", entry=28.0, shares=750, strike=77353.35,
         win_start="2026-09-02T18:00:00Z",
         result="Settled UP — won $540 (+257%)",
         eva="EVA 18:00Z — H4 bearish 0.65 (range_pos 0.21, at lows) | H1 bearish 0.66 (mid-range) | M15 BULLISH 0.58 (range_pos 0.82-0.89): 'tactical mean-reversion bounce'"),
    dict(key="t2", pid="BTC-USD", side="DOWN", entry=20.4, shares=1200, strike=77293.99,
         win_start="2026-09-02T18:45:00Z",
         result="Exited early ~35.8c — +$184 (+75%); market later settled UP",
         eva="EVA 18:00Z — H4 bearish 0.65 (at lows) | H1 bearish 0.66 | M15 bullish but range_pos 0.88 NEAR TOP: pop into H1 bearish bias -> fade"),
    dict(key="t3", pid="BTC-USD", side="UP", entry=20.9, shares=250, strike=77179.48,
         win_start="2026-09-02T19:30:00Z",
         result="Settled UP — won $197.75 (+378%)",
         eva="EVA 19:00Z — H4 bearish 0.72 (range_pos 0.21) | H1 bearish 0.68 | M15 BULLISH 0.58-0.65: 'immediate oversold reversion plausible'"),
    dict(key="t4", pid="BTC-USD", side="UP", entry=29.0, shares=1050, strike=77203.93,
         win_start="2026-09-02T19:45:00Z",
         result="Exited early ~31.6c — +$27 (+8.9%); market settled UP",
         eva="EVA 19:00Z — H4 bearish 0.72 | H1 bearish 0.68 | M15 BULLISH (range_pos 0.85 upper band)"),
    dict(key="t5", pid="BTC-USD", side="UP", entry=37.0, shares=500, strike=77344.21,
         win_start="2026-09-02T20:45:00Z",
         result="Settled UP — won $315 (+170%)",
         eva="EVA 20:00Z — H4 bearish 0.65-0.72 (0.21 lows) | H1 NEUTRAL 0.55 ('near-term floor intact') | M15 BULLISH 0.58-0.62 (range_pos 0.81-0.92)"),
    dict(key="t6", pid="ETH-USD", side="UP", entry=38.6, shares=500, strike=2390.07,
         win_start="2026-09-02T20:45:00Z",
         result="Settled UP — won $306.77 (+159%)",
         eva="EVA 20:00Z — ETH H4 bearish 0.68 (0.17 lows) | H1 bearish 0.64 | M15 neutral 0.53 ('lags BTC recovery') — BTC M15 bullish led"),
    dict(key="t7", pid="BTC-USD", side="DOWN", entry=21.0, shares=1000, strike=77367.46,
         win_start="2026-09-03T01:30:00Z",
         result="Settled DOWN — won (+376% potential)",
         eva="EVA 01:00Z — H4 bearish 0.65-0.72 (range_pos 0.15-0.22) | H1 NEUTRAL 0.55-0.58 ('HL/LH chop mid-range') | M15 mixed bearish/neutral"),
    dict(key="t8", pid="BTC-USD", side="UP", entry=32.5, shares=2000, strike=77327.80,
         win_start="2026-09-03T02:00:00Z",
         result="Settled UP — won (+208% potential)",
         eva="EVA 02:00Z — H4 bearish 0.65 | H1 neutral 0.55-0.60 | M15 BULLISH 0.72 (range_pos 1.0): 'micro bullish thrust, controlled accumulation'"),
]

stats = []
for t in TRADES:
    pid = t["pid"]
    ws = int(datetime.fromisoformat(t["win_start"].replace("Z", "+00:00")).timestamp())
    we = ws + 900
    h1 = bars(pid, "H1")
    m15 = bars(pid, "M15")
    m5 = bars(pid, "M5")

    # prior-60-min BTC move (boss rule 3) — always measured on BTC
    btc5 = bars("BTC-USD", "M5")
    pre = [x for x in btc5 if ws - 3600 <= x["ts"] < ws]
    net = (pre[-1]["close"] - pre[0]["open"]) / pre[0]["open"] * 100 if pre else None
    rng = ((max(x["high"] for x in pre) - min(x["low"] for x in pre)) / pre[-1]["close"] * 100) if pre else None

    minute = datetime.fromtimestamp(ws, tz=ET).minute
    quarter_ok = minute in (0, 45)
    price_ok = t["entry"] <= 33.0

    stats.append(dict(key=t["key"], btc_net_1h_pct=round(net, 3), btc_range_1h_pct=round(rng, 3),
                      quarter_ok=quarter_ok, price_ok=price_ok,
                      window_et=f"{et_label(ws)}-{et_label(we)} ET"))

    fig = plt.figure(figsize=(16, 10), facecolor="#101418")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.15, 1], hspace=0.28, wspace=0.14,
                          left=0.05, right=0.985, top=0.90, bottom=0.06)
    ax1 = fig.add_subplot(gs[0, :])
    ax2 = fig.add_subplot(gs[1, 0])
    ax3 = fig.add_subplot(gs[1, 1])
    for ax in (ax1, ax2, ax3):
        ax.set_facecolor("#101418")
        ax.tick_params(colors="#c8d0d8", labelsize=8)
        for s in ax.spines.values():
            s.set_color("#39424c")
        ax.grid(color="#232b33", lw=0.5, zorder=0)

    # ---- H1 panel with order blocks + range box
    h1e = [x for x in h1 if x["ts"] <= ws + 4 * 3600]
    h1w = h1e[-42:]
    zones = order_blocks(h1e, min_width_pct=0.05 if pid == "BTC-USD" else 0.08)
    off = len(h1e) - len(h1w)
    draw_candles(ax1, h1w)
    closes = [x["close"] for x in h1e]
    for span, col in ((12, "#e8c547"), (26, "#7a5df0")):
        line = ema(closes, span)[off:]
        ax1.plot(range(len(line)), line, color=col, lw=1.1, label=f"EMA{span}")
    for z in zones:
        s = max(z["start_idx"] - off, 0)
        e = (z["end_idx"] - off) if z["end_idx"] is not None else len(h1w) - 1
        if e < 0 or s >= len(h1w):
            continue
        col = "#2e9e6b" if z["direction"] == "bullish" else "#d9534f"
        ax1.add_patch(Rectangle((s, z["low"]), e - s, z["high"] - z["low"],
                                facecolor=col, alpha=0.16, edgecolor=col, lw=0.7, zorder=1))
    last24 = [x for x in h1e if x["ts"] >= ws - 24 * 3600]
    ax1.axhline(max(x["high"] for x in last24), color="#c8d0d8", ls="--", lw=0.8)
    ax1.axhline(min(x["low"] for x in last24), color="#c8d0d8", ls="--", lw=0.8)
    ax1.axhline(t["strike"], color="#4ec3e0", ls=":", lw=1.2)
    widx = next(i for i, x in enumerate(h1w) if x["ts"] <= ws < x["ts"] + 3600)
    ax1.axvspan(widx - 0.5, widx + 0.5, color="#4ec3e0", alpha=0.10)
    ax1.annotate("trade hour", (widx, h1w[widx]["high"]), color="#4ec3e0", fontsize=8,
                 xytext=(0, 14), textcoords="offset points", ha="center")
    ticks = [i for i, x in enumerate(h1w) if datetime.fromtimestamp(x["ts"], tz=ET).hour % 4 == 0]
    ax1.set_xticks(ticks)
    ax1.set_xticklabels([et_label(h1w[i]["ts"], "%m/%d %H:%M") for i in ticks])
    ax1.legend(loc="upper left", fontsize=8, facecolor="#101418", labelcolor="#c8d0d8", edgecolor="#39424c")
    ax1.set_title(f"{pid} H1 — 24h range (dashed), order blocks, strike (dotted)", color="#c8d0d8", fontsize=10)

    # ---- M15 panel
    m15w = [x for x in m15 if ws - 5 * 3600 <= x["ts"] <= ws + 2 * 3600]
    o2 = next(i for i, x in enumerate(m15) if x["ts"] == m15w[0]["ts"])
    draw_candles(ax2, m15w, width=0.55)
    closes15 = [x["close"] for x in m15]
    for span, col in ((12, "#e8c547"), (26, "#7a5df0")):
        line = ema(closes15, span)[o2:o2 + len(m15w)]
        ax2.plot(range(len(line)), line, color=col, lw=1.0)
    ax2.axhline(t["strike"], color="#4ec3e0", ls=":", lw=1.2)
    wi = [i for i, x in enumerate(m15w) if ws <= x["ts"] < we]
    if wi:
        ax2.axvspan(wi[0] - 0.5, wi[-1] + 0.5, color="#4ec3e0", alpha=0.18)
        arrow_col = "#2e9e6b" if t["side"] == "UP" else "#d9534f"
        y = m15w[wi[0]]["low"] if t["side"] == "UP" else m15w[wi[0]]["high"]
        dy = -28 if t["side"] == "UP" else 28
        ax2.annotate(f"BUY {t['side']} @ {t['entry']}c", (wi[0], y), color=arrow_col,
                     fontsize=9, fontweight="bold", xytext=(0, dy), textcoords="offset points",
                     ha="center", arrowprops=dict(arrowstyle="->", color=arrow_col))
    ticks = [i for i, x in enumerate(m15w) if datetime.fromtimestamp(x["ts"], tz=ET).minute == 0]
    ax2.set_xticks(ticks)
    ax2.set_xticklabels([et_label(m15w[i]["ts"]) for i in ticks])
    ax2.set_title("M15 — trade window shaded", color="#c8d0d8", fontsize=10)

    # ---- M5 zoom
    m5w = [x for x in m5 if ws - 45 * 60 <= x["ts"] <= we + 30 * 60]
    draw_candles(ax3, m5w, width=0.55)
    ax3.axhline(t["strike"], color="#4ec3e0", ls=":", lw=1.4)
    ax3.annotate(f"strike {t['strike']:,}", (0.4, t["strike"]), color="#4ec3e0", fontsize=8,
                 xytext=(2, 4), textcoords="offset points")
    wi = [i for i, x in enumerate(m5w) if ws <= x["ts"] < we]
    if wi:
        ax3.axvspan(wi[0] - 0.5, wi[-1] + 0.5, color="#4ec3e0", alpha=0.18)
    ticks = list(range(0, len(m5w), 3))
    ax3.set_xticks(ticks)
    ax3.set_xticklabels([et_label(m5w[i]["ts"]) for i in ticks])
    ax3.set_title("M5 zoom — 15m Kalshi window shaded", color="#c8d0d8", fontsize=10)

    win_et = f"{et_label(ws)}–{et_label(we)} ET Sep 2" if ws < 1788480000 else f"{et_label(ws)}–{et_label(we)} ET Sep 2 (late)"
    fig.suptitle(
        f"{t['key'].upper()}  {pid}  BUY {t['side']} @ {t['entry']}c ×{t['shares']}   "
        f"window {et_label(ws)}–{et_label(we)} ET   strike {t['strike']:,}\n"
        f"{t['result']}   |   BTC prior-60m: net {net:+.2f}%, range {rng:.2f}%\n{t['eva']}",
        color="#e8eef4", fontsize=10.5, y=0.985)
    fig.savefig(OUT / f"{t['key']}_{pid.split('-')[0].lower()}_{t['side'].lower()}{int(t['entry'])}c.png", dpi=110)
    plt.close(fig)
    print("saved", t["key"])

(OUT / "trade_stats.json").write_text(json.dumps(stats, indent=1))
print(json.dumps(stats, indent=1))
