"""Pure-matplotlib entry charts for eva_wick Telegram broadcasts.

Renders the same three-panel layout as the Sep 2 2026 wick-review package
(exports/wick_review_20260902): H1 with order blocks / 24h range / strike,
M15 (resampled from M5) with the entry marked, and an M5 zoom of the live
Kalshi window. EVA H4/H1/M15 stances go in the header so the chart itself
makes the case for the trade.

No pandas / mplfinance / numpy.random — those are blocked by the local
Application Control policy; everything here is plain matplotlib on lists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import config
import research

logger = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-4))

_BG = "#101418"
_FG = "#c8d0d8"
_GRID = "#232b33"
_SPINE = "#39424c"
_UP = "#2e9e6b"
_DOWN = "#d9534f"
_STRIKE = "#4ec3e0"


def _epoch(ts: Any) -> int:
    return int(
        datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    )


def _bars(timeframe: str, limit: int, coinbase: str) -> list[dict]:
    raw = research.get_ohlc(timeframe, limit=limit, product_id=coinbase)
    return [
        {
            "ts": _epoch(b["ts"]),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
        }
        for b in raw
    ]


def _resample_m15(m5: list[dict]) -> list[dict]:
    """Aggregate M5 bars into quarter-hour M15 bars (drop leading partial)."""
    groups: dict[int, list[dict]] = {}
    for b in m5:
        groups.setdefault(b["ts"] - b["ts"] % 900, []).append(b)
    out = []
    for start in sorted(groups):
        g = sorted(groups[start], key=lambda x: x["ts"])
        out.append(
            {
                "ts": start,
                "open": g[0]["open"],
                "high": max(x["high"] for x in g),
                "low": min(x["low"] for x in g),
                "close": g[-1]["close"],
            }
        )
    return out


def _ema(closes: list[float], span: int) -> list[float]:
    k = 2.0 / (span + 1.0)
    out = []
    e = closes[0]
    for c in closes:
        e = c * k + e * (1 - k)
        out.append(e)
    return out


def _order_blocks(b: list[dict], min_width_pct: float = 0.05) -> list[dict]:
    """Pivots (2-2 fractal) -> MSB on close-through -> OB = last opposite
    candle before the break; a close beyond the zone mitigates it.
    Mirrors patterns/htf_structure.py (see wick_review chart_gen)."""
    n = len(b)
    pivots: list[tuple[int, str, float]] = []
    for i in range(2, n - 2):
        if b[i]["high"] == max(x["high"] for x in b[i - 2 : i + 3]):
            pivots.append((i, "high", b[i]["high"]))
        if b[i]["low"] == min(x["low"] for x in b[i - 2 : i + 3]):
            pivots.append((i, "low", b[i]["low"]))
    zones: list[dict] = []
    for i in range(4, n):
        close = b[i]["close"]
        last_hi = max(
            (p for p in pivots if p[1] == "high" and p[0] < i),
            key=lambda p: p[0],
            default=None,
        )
        last_lo = max(
            (p for p in pivots if p[1] == "low" and p[0] < i),
            key=lambda p: p[0],
            default=None,
        )
        for piv, direction in ((last_hi, "bullish"), (last_lo, "bearish")):
            if piv is None:
                continue
            broke = close > piv[2] if direction == "bullish" else close < piv[2]
            if not broke:
                continue
            if any(
                z["direction"] == direction and z["msb_idx"] >= piv[0]
                for z in zones
            ):
                continue
            opp = "bearish" if direction == "bullish" else "bullish"
            for j in range(i - 1, max(i - 30, -1), -1):
                cdir = "bullish" if b[j]["close"] >= b[j]["open"] else "bearish"
                if cdir != opp:
                    continue
                lo, hi = b[j]["low"], b[j]["high"]
                if (hi - lo) / max(b[j]["close"], 1e-9) * 100 < min_width_pct:
                    break
                zones.append(
                    {
                        "direction": direction,
                        "low": lo,
                        "high": hi,
                        "start_idx": j,
                        "msb_idx": i,
                        "end_idx": None,
                    }
                )
                break
    for z in zones:
        for i in range(z["msb_idx"] + 1, n):
            c = b[i]["close"]
            dead = c < z["low"] if z["direction"] == "bullish" else c > z["high"]
            if dead:
                z["end_idx"] = i
                break
    return zones


def _draw_candles(ax, b: list[dict], width: float = 0.6) -> None:
    for i, bar in enumerate(b):
        up = bar["close"] >= bar["open"]
        color = _UP if up else _DOWN
        ax.plot([i, i], [bar["low"], bar["high"]], color=color, lw=0.8, zorder=2)
        body_lo = min(bar["open"], bar["close"])
        h = abs(bar["close"] - bar["open"]) or bar["close"] * 1e-6
        ax.add_patch(
            Rectangle(
                (i - width / 2, body_lo),
                width,
                h,
                facecolor=color,
                edgecolor=color,
                zorder=3,
            )
        )


def _et(ts: int, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ts, tz=ET).strftime(fmt)


def _style(ax) -> None:
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_FG, labelsize=8)
    for s in ax.spines.values():
        s.set_color(_SPINE)
    ax.grid(color=_GRID, lw=0.5, zorder=0)


def build_eva_entry_chart(
    *,
    product_id: str,
    coinbase: str,
    side: str,
    entry_side_cents: float,
    strike: float,
    expiry_ts: str,
    stances: dict[str, dict[str, Any]],
    pattern: str,
    wick_line: str = "",
    session_pos: float | None = None,
    btc_move: float | None = None,
) -> str | None:
    """Render the eva_wick entry chart; return PNG path or None on failure."""
    try:
        h1 = _bars("H1", 120, coinbase)
        m5 = _bars("M5", 350, coinbase)
    except Exception:
        logger.exception("eva_charts: OHLC fetch failed for %s", coinbase)
        return None
    if len(h1) < 30 or len(m5) < 60:
        return None
    m15 = _resample_m15(m5)

    win_end = _epoch(expiry_ts)
    win_start = win_end - 900
    now = int(datetime.now(timezone.utc).timestamp())
    direction = "UP" if side == "YES" else "DOWN"

    fig = plt.figure(figsize=(16, 10), facecolor=_BG)
    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[1.15, 1],
        hspace=0.28,
        wspace=0.14,
        left=0.05,
        right=0.985,
        top=0.88,
        bottom=0.06,
    )
    ax1 = fig.add_subplot(gs[0, :])
    ax2 = fig.add_subplot(gs[1, 0])
    ax3 = fig.add_subplot(gs[1, 1])
    for ax in (ax1, ax2, ax3):
        _style(ax)

    # ---- H1: order blocks, EMAs, 24h range, strike, live hour shaded
    h1w = h1[-42:]
    off = len(h1) - len(h1w)
    zones = _order_blocks(h1, min_width_pct=0.05 if "BTC" in coinbase else 0.08)
    _draw_candles(ax1, h1w)
    closes = [x["close"] for x in h1]
    for span, col in ((12, "#e8c547"), (26, "#7a5df0")):
        line = _ema(closes, span)[off:]
        ax1.plot(range(len(line)), line, color=col, lw=1.1, label=f"EMA{span}")
    for z in zones:
        s = max(z["start_idx"] - off, 0)
        e = (z["end_idx"] - off) if z["end_idx"] is not None else len(h1w) - 1
        if e < 0 or s >= len(h1w):
            continue
        col = _UP if z["direction"] == "bullish" else _DOWN
        ax1.add_patch(
            Rectangle(
                (s, z["low"]),
                e - s,
                z["high"] - z["low"],
                facecolor=col,
                alpha=0.16,
                edgecolor=col,
                lw=0.7,
                zorder=1,
            )
        )
    last24 = h1[-24:]
    ax1.axhline(max(x["high"] for x in last24), color=_FG, ls="--", lw=0.8)
    ax1.axhline(min(x["low"] for x in last24), color=_FG, ls="--", lw=0.8)
    ax1.axhline(strike, color=_STRIKE, ls=":", lw=1.2)
    ax1.axvspan(len(h1w) - 1.5, len(h1w) - 0.5, color=_STRIKE, alpha=0.10)
    ticks = [
        i
        for i, x in enumerate(h1w)
        if datetime.fromtimestamp(x["ts"], tz=ET).hour % 4 == 0
    ]
    ax1.set_xticks(ticks)
    ax1.set_xticklabels([_et(h1w[i]["ts"], "%m/%d %H:%M") for i in ticks])
    ax1.legend(
        loc="upper left", fontsize=8, facecolor=_BG, labelcolor=_FG, edgecolor=_SPINE
    )
    ax1.set_title(
        f"{coinbase} H1 — 24h range (dashed), order blocks, strike (dotted)",
        color=_FG,
        fontsize=10,
    )

    # ---- M15: session context + entry marker
    m15w = [x for x in m15 if x["ts"] >= now - 7 * 3600]
    if len(m15w) >= 4:
        _draw_candles(ax2, m15w, width=0.55)
        o2 = len(m15) - len(m15w)
        closes15 = [x["close"] for x in m15]
        for span, col in ((12, "#e8c547"), (26, "#7a5df0")):
            line = _ema(closes15, span)[o2:]
            ax2.plot(range(len(line)), line, color=col, lw=1.0)
        ax2.axhline(strike, color=_STRIKE, ls=":", lw=1.2)
        wi = [i for i, x in enumerate(m15w) if win_start <= x["ts"] < win_end]
        anchor = wi[0] if wi else len(m15w) - 1
        ax2.axvspan(anchor - 0.5, anchor + 0.5, color=_STRIKE, alpha=0.18)
        arrow_col = _UP if direction == "UP" else _DOWN
        y = m15w[anchor]["low"] if direction == "UP" else m15w[anchor]["high"]
        dy = -28 if direction == "UP" else 28
        ax2.annotate(
            f"BUY {direction} @ {entry_side_cents:.0f}c",
            (anchor, y),
            color=arrow_col,
            fontsize=9,
            fontweight="bold",
            xytext=(0, dy),
            textcoords="offset points",
            ha="center",
            arrowprops=dict(arrowstyle="->", color=arrow_col),
        )
        ticks = [
            i
            for i, x in enumerate(m15w)
            if datetime.fromtimestamp(x["ts"], tz=ET).minute == 0
        ]
        ax2.set_xticks(ticks)
        ax2.set_xticklabels([_et(m15w[i]["ts"]) for i in ticks])
    ax2.set_title("M15 — entry window shaded", color=_FG, fontsize=10)

    # ---- M5 zoom on the live Kalshi window
    m5w = [x for x in m5 if x["ts"] >= win_start - 45 * 60]
    if len(m5w) >= 3:
        _draw_candles(ax3, m5w, width=0.55)
        ax3.axhline(strike, color=_STRIKE, ls=":", lw=1.4)
        ax3.annotate(
            f"strike {strike:,.2f}",
            (0.4, strike),
            color=_STRIKE,
            fontsize=8,
            xytext=(2, 4),
            textcoords="offset points",
        )
        wi = [i for i, x in enumerate(m5w) if win_start <= x["ts"] < win_end]
        if wi:
            ax3.axvspan(wi[0] - 0.5, wi[-1] + 0.5, color=_STRIKE, alpha=0.18)
        ticks = list(range(0, len(m5w), 3))
        ax3.set_xticks(ticks)
        ax3.set_xticklabels([_et(m5w[i]["ts"]) for i in ticks])
    ax3.set_title("M5 zoom — 15m Kalshi window shaded", color=_FG, fontsize=10)

    h4s, h1s, m15s = stances["H4"], stances["H1"], stances["M15"]
    extras = []
    if session_pos is not None:
        extras.append(f"session range pos {session_pos:.0%}")
    if btc_move is not None:
        extras.append(f"BTC prior-60m {btc_move:+.2f}%")
    header = (
        f"{product_id}  eva_wick {pattern}  BUY {direction} @ "
        f"{entry_side_cents:.0f}c   window {_et(win_start)}–{_et(win_end)} ET   "
        f"strike {strike:,.2f}\n"
        f"EVA: H4 {h4s['stance']} {h4s['confidence']:.2f} | "
        f"H1 {h1s['stance']} {h1s['confidence']:.2f} | "
        f"M15 {m15s['stance']} {m15s['confidence']:.2f}"
        + (f"   ({', '.join(extras)})" if extras else "")
        + (f"\n{wick_line}" if wick_line else "")
    )
    fig.suptitle(header, color="#e8eef4", fontsize=10.5, y=0.985)

    out_dir: Path = config.CHARTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"eva_{product_id}_{direction}_{ts}.png"
    try:
        fig.savefig(out, dpi=110)
        return str(out)
    except Exception:
        logger.exception("eva_charts: savefig failed")
        return None
    finally:
        plt.close(fig)
