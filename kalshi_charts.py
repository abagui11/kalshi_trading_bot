"""Simple Coinbase M5 charts for Kalshi Telegram updates."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

import bot_config
import config
import research
from models import KalshiSuggestion

logger = logging.getLogger(__name__)

DPI = 120
FIGSIZE = (12, 6)


def _charts_dir() -> Path:
    path = config.CHARTS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_decision_chart(
    suggestion: KalshiSuggestion,
    *,
    strike: float | None = None,
    bars: int = 36,
) -> str | None:
    """Render recent M5 candles for the underlying; return PNG path or None."""
    coinbase = bot_config.PRODUCT_TO_COINBASE.get(
        suggestion.product_id, f"{suggestion.product_id}-USD"
    )
    try:
        raw = research.get_ohlc("M5", limit=bars, product_id=coinbase)
    except Exception:
        logger.exception("Failed to fetch OHLC for chart %s", coinbase)
        return None
    if not raw:
        return None

    df = pd.DataFrame(raw)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )[["Open", "High", "Low", "Close", "Volume"]]

    side = suggestion.side
    mid = suggestion.mid_cents
    fair = suggestion.fair_yes_cents
    title_bits = [
        f"{suggestion.product_id} M5",
        f"{side}",
    ]
    if mid is not None:
        title_bits.append(f"mid {mid:.1f}¢")
    if fair is not None:
        title_bits.append(f"fair {fair:.1f}¢")
    if suggestion.edge_cents is not None:
        title_bits.append(f"edge {suggestion.edge_cents:.1f}¢")
    title = " · ".join(title_bits)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = _charts_dir() / f"kalshi_{suggestion.product_id}_{side}_{ts}.png"

    try:
        fig, axes = mpf.plot(
            df,
            type="candle",
            style="charles",
            title=title,
            ylabel=coinbase,
            volume=True,
            figsize=FIGSIZE,
            returnfig=True,
            tight_layout=True,
        )
        ax = axes[0] if isinstance(axes, (list, tuple)) else axes
        if strike is not None and strike > 0:
            ax.axhline(strike, color="#e67e22", linestyle="--", linewidth=1.2, label=f"strike {strike:.2f}")
            ax.legend(loc="upper left", fontsize=8)
        fig.savefig(out, dpi=DPI, bbox_inches="tight")
        plt.close(fig)
        return str(out)
    except Exception:
        logger.exception("Failed to render Kalshi chart")
        try:
            plt.close("all")
        except Exception:
            pass
        return None
