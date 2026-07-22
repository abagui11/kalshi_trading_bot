"""Bot runtime configuration (non-secret tunables)."""

from __future__ import annotations

import config

# Coinbase product ids used for underlying context.
TRADED_PRODUCTS: tuple[str, ...] = ("BTC-USD", "ETH-USD")
DEFAULT_PRODUCT_ID = "BTC-USD"

SERIES_TO_PRODUCT: dict[str, str] = {
    "KXBTC15M": "BTC",
    "KXETH15M": "ETH",
}
PRODUCT_TO_COINBASE: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}

# When True, Telegram only gets DMs on real paper trades (not skips).
BROADCAST_ONLY_TRADES = True

# Kalshi mirrors (config is source of truth; these are convenient aliases).
KALSHI_SERIES: tuple[str, ...] = tuple(config.KALSHI_SERIES)
KALSHI_MAX_CONTRACTS = config.KALSHI_MAX_CONTRACTS
KALSHI_MIN_EDGE_CENTS = config.KALSHI_MIN_EDGE_CENTS
KALSHI_CYCLE_OFFSET_SEC = config.KALSHI_CYCLE_OFFSET_SEC
KALSHI_PAPER_ONLY = config.KALSHI_PAPER_ONLY
KALSHI_BANKROLL_USD = config.KALSHI_BANKROLL_USD
KALSHI_DEPLOY_PCT = config.KALSHI_DEPLOY_PCT
KALSHI_USE_LIVE_BALANCE = config.KALSHI_USE_LIVE_BALANCE

# Main loop cadence.
KALSHI_JOB_INTERVAL_SEC = 60
# How wide a window around (open + offset) still counts as "decision time".
KALSHI_DECISION_WINDOW_SEC = 90

PAPER_EPOCH_LABEL = "kalshi_15m_1k"


def product_label(product_id: str) -> str:
    pid = str(product_id or "").upper()
    if pid in ("BTC", "BTC-USD"):
        return "BTC"
    if pid in ("ETH", "ETH-USD"):
        return "ETH"
    return pid or "UNKNOWN"


def series_product(series: str) -> str:
    return SERIES_TO_PRODUCT.get(series.upper(), series.upper().replace("KX", "").replace("15M", "")[:3] or "BTC")
