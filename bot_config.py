"""Bot runtime configuration (non-secret tunables)."""

from __future__ import annotations

import config

# Coinbase product ids used for underlying ICT context.
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

# Pre-broadcast audit refine loop (kept for ICT path reuse).
MAX_REFINE_PASSES = 3
RUN_LLM_CRITIC_PRE_BROADCAST = False  # Kalshi cycle uses lighter validation

# Fixed-fraction spot sizing (legacy ICT path / validate.py).
TRADE_DEPLOY_PCT = 0.25

# M5 OB fib entry band (bullish: from block low; bearish: from block high).
ENTRY_FIB_LOW = 0.25
ENTRY_FIB_HIGH = 0.50
ENTRY_FIB_TRANCHE_1 = 0.25
ENTRY_FIB_TRANCHE_2 = 0.50
ADD_FIB_LEVEL = 0.718
ENTRY_TRANCHE_DEPLOY_PCT = TRADE_DEPLOY_PCT / 2
ADD_DEPLOY_PCT = TRADE_DEPLOY_PCT
FIB_LEVEL_TOLERANCE_PCT = 0.008

# Paper position size guardrails per product (legacy ICT / validate).
PRODUCT_QTY_CAPS: dict[str, tuple[float, float]] = {
    "ETH-USD": (0.25, 2.0),
    "BTC-USD": (0.005, 0.05),
}
MIN_ETH_QTY = PRODUCT_QTY_CAPS["ETH-USD"][0]
MAX_ETH_QTY = PRODUCT_QTY_CAPS["ETH-USD"][1]

# Personal / house book leftovers (imports may still reference these).
PAPER_CONTRIBUTION_USD = 1000.0
HOUSE_CONTRIBUTION_TELEGRAM_ID = 0
PAPER_ACCOUNT_SIZES: tuple[float, ...] = (500.0, 1000.0, 2500.0)
PAPER_ACCOUNT_DEFAULT_USD = 1000.0
APPROVAL_WINDOW_MIN = 15
MISSED_CONNECTION_R = 0.5
USER_MIN_DEPLOY_USD = 25.0
LAUNCH_NOTICE_SENT_KEY = "personal_books_launch_v1"
MAX_OPEN_TRADES = 20

# Minimum OB zone width as % of mid price.
OB_MIN_WIDTH_PCT = 1.25
OB_MIN_WIDTH_PCT_M5 = 0.15
PRODUCT_OB_MIN_WIDTH_PCT: dict[str, float] = {
    "ETH-USD": OB_MIN_WIDTH_PCT,
    "BTC-USD": 0.60,
}

PAPER_EPOCH_LABEL = "kalshi_15m_ict"

# Watchdog left available but not scheduled by main.py for Kalshi.
WATCHDOG_ENABLED = False
WATCHDOG_INTERVAL_SEC = 60
WATCHDOG_COOLDOWN_SEC = 30 * 60
WATCHDOG_EXECUTE_ENABLED = False
WATCHDOG_EXECUTE_META_KEY = "watchdog_execute_enabled"
WATCHDOG_ALLOW_SHORTS = True
SCALE_IN_MIN_R = 0.5

MACRO_CONTEXT_ENABLED = False
MACRO_POLL_INTERVAL_SEC = 300
MACRO_MIN_SEVERITY_INJECT = 3
MACRO_PULSE_MIN_SEVERITY = 4
MACRO_WATCHDOG_GATE_MIN_SEVERITY = 4
MACRO_DEFAULT_TTL_HOURS = 24
MACRO_LLM_PROMOTE_THRESHOLD = 40

ZMOVE_ENABLED = False
ZMOVE_INTERVAL_SEC = 300
ZMOVE_THRESHOLD = 2.0
ZMOVE_LOOKBACK_H = 168
ZMOVE_COOLDOWN_SEC = 2 * 60 * 60
ZMOVE_PRODUCT_ID = "ETH-USD"

RELATIVE_STRENGTH_ENABLED = False

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
# Soft mid filter: skip lottery-ticket binaries even with ICT bias.
KALSHI_EXTREME_MID_CENTS = 5.0


def qty_caps(product_id: str) -> tuple[float, float]:
    """Return (min_qty, max_qty) for a product; fall back to ETH caps."""
    return PRODUCT_QTY_CAPS.get(product_id, PRODUCT_QTY_CAPS["ETH-USD"])


def ob_min_width_pct(product_id: str | None = None) -> float:
    """HTF OB/breaker minimum width (% of mid) for a product."""
    if not product_id:
        return OB_MIN_WIDTH_PCT
    return float(PRODUCT_OB_MIN_WIDTH_PCT.get(product_id, OB_MIN_WIDTH_PCT))


def product_label(product_id: str) -> str:
    pid = str(product_id or "").upper()
    if pid in ("BTC", "BTC-USD") or (pid.endswith("-USD") and pid.startswith("BTC")):
        return "BTC"
    if pid in ("ETH", "ETH-USD") or (pid.endswith("-USD") and pid.startswith("ETH")):
        return "ETH"
    if pid.endswith("-USD"):
        return pid[: -len("-USD")]
    return pid or "UNKNOWN"


def series_product(series: str) -> str:
    return SERIES_TO_PRODUCT.get(
        series.upper(),
        series.upper().replace("KX", "").replace("15M", "")[:3] or "BTC",
    )


def watchdog_execute_enabled() -> bool:
    """Effective watchdog paper-execution flag (config default + runtime meta override)."""
    try:
        import user_books

        raw = user_books.get_meta(WATCHDOG_EXECUTE_META_KEY)
    except Exception:
        raw = None
    if raw is None or str(raw).strip() == "":
        return bool(WATCHDOG_EXECUTE_ENABLED)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def set_watchdog_execute_enabled(enabled: bool) -> bool:
    """Persist runtime override for watchdog paper execution. Returns new value."""
    try:
        import user_books

        user_books.set_meta(WATCHDOG_EXECUTE_META_KEY, "1" if enabled else "0")
    except Exception:
        pass
    return enabled
