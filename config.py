"""Load environment variables and fail loudly if anything required is missing."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

_REQUIRED_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "TELEGRAM_BOT_TOKEN",
    "MARKET_DATA_API",
    "PORTFOLIO_VALUE",
    "PAPER_PORTFOLIO_VALUE",
)


def _require(key: str) -> str:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Copy .env.example to .env and fill in all values."
        )
    return value.strip()


def _optional(key: str) -> str | None:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _optional_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes")


ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL: str = _require("ANTHROPIC_MODEL")
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
MARKET_DATA_API: str = _require("MARKET_DATA_API").rstrip("/")
PORTFOLIO_VALUE: float = float(_require("PORTFOLIO_VALUE"))
PAPER_PORTFOLIO_VALUE: float = float(_require("PAPER_PORTFOLIO_VALUE"))

# Restrict Telegram DMs to ALLOWED_TELEGRAM_IDS while testing.
PAYWALL_ENABLED: bool = _optional_bool("PAYWALL_ENABLED", default=True)

_allowed_raw = os.getenv("ALLOWED_TELEGRAM_IDS", "")
ALLOWED_TELEGRAM_IDS: list[int] = [
    int(x.strip()) for x in _allowed_raw.split(",") if x.strip()
]
if PAYWALL_ENABLED and not ALLOWED_TELEGRAM_IDS:
    raise RuntimeError(
        "PAYWALL_ENABLED=true requires ALLOWED_TELEGRAM_IDS in .env"
    )

TELEGRAM_CHAT_ID: str | None = _optional("TELEGRAM_CHAT_ID")
TELEGRAM_ADMIN_CHAT_ID: str | None = _optional("TELEGRAM_ADMIN_CHAT_ID")
MONITOR_CHAT_ID: str | None = _optional("MONITOR_CHAT_ID")

ROOT_DIR: Path = Path(__file__).resolve().parent
CHARTS_DIR: Path = ROOT_DIR / "charts"
LEDGER_DB: Path = ROOT_DIR / "ledger.db"
OHLC_DB: Path = ROOT_DIR / "ohlc.db"
SECRETS_DIR: Path = ROOT_DIR / "secrets"
TRADING_GUIDE_DIR: Path = ROOT_DIR / "Trading Guide"

DASHBOARD_PUBLIC_URL: str | None = _optional("DASHBOARD_PUBLIC_URL")
DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "8081") or "8081")

ME_TOKEN_SECRET: str = _optional("ME_TOKEN_SECRET") or TELEGRAM_BOT_TOKEN
ME_TOKEN_TTL_SEC: int = int(os.getenv("ME_TOKEN_TTL_SEC", "3600") or "3600")
ME_SESSION_TTL_SEC: int = int(os.getenv("ME_SESSION_TTL_SEC", "86400") or "86400")

# --- Kalshi ---
KALSHI_ENV: str = (_optional("KALSHI_ENV") or "demo").lower()
KALSHI_API_BASE: str = (
    _optional("KALSHI_API_BASE")
    or (
        "https://external-api.demo.kalshi.co/trade-api/v2"
        if KALSHI_ENV == "demo"
        else "https://external-api.kalshi.com/trade-api/v2"
    )
).rstrip("/")
KALSHI_API_KEY_ID: str | None = _optional("KALSHI_API_KEY_ID")
_key_path_raw = _optional("KALSHI_PRIVATE_KEY_PATH") or "secrets/kalshi_demo.key"
KALSHI_PRIVATE_KEY_PATH: Path = (
    Path(_key_path_raw)
    if Path(_key_path_raw).is_absolute()
    else ROOT_DIR / _key_path_raw
)
_series_raw = _optional("KALSHI_SERIES") or "KXBTC15M,KXETH15M"
KALSHI_SERIES: list[str] = [s.strip() for s in _series_raw.split(",") if s.strip()]
KALSHI_PAPER_ONLY: bool = _optional_bool("KALSHI_PAPER_ONLY", default=True)
# Safety ceiling on contracts; conviction/deploy% is the real risk limiter.
KALSHI_MAX_CONTRACTS: int = int(os.getenv("KALSHI_MAX_CONTRACTS", "100") or "100")
# Real |model_fair − mid| threshold (¢). Used for sizing boost / audit, not hard skip on control.
KALSHI_MIN_EDGE_CENTS: float = float(os.getenv("KALSHI_MIN_EDGE_CENTS", "8") or "8")
KALSHI_CYCLE_OFFSET_SEC: int = int(os.getenv("KALSHI_CYCLE_OFFSET_SEC", "30") or "30")
# Sizing vs bankroll. Conviction matrix sets deploy%; MAX_DEPLOY_PCT hard-caps risk.
KALSHI_BANKROLL_USD: float = float(os.getenv("KALSHI_BANKROLL_USD", "77") or "77")
KALSHI_DEPLOY_PCT: float = float(os.getenv("KALSHI_DEPLOY_PCT", "0.05") or "0.05")
# Never risk more than this fraction of book on a single trade.
KALSHI_MAX_DEPLOY_PCT: float = float(
    os.getenv("KALSHI_MAX_DEPLOY_PCT", "0.15") or "0.15"
)
# Absolute notional ceiling; 0 disables (deploy% is the limiter).
KALSHI_MAX_NOTIONAL_USD: float = float(
    os.getenv("KALSHI_MAX_NOTIONAL_USD", "0") or "0"
)
# When live, prefer Kalshi account balance for bankroll; fall back to KALSHI_BANKROLL_USD.
# Sizing still never exceeds KALSHI_BANKROLL_USD (see kalshi_sizing.sizing_bankroll_usd).
KALSHI_USE_LIVE_BALANCE: bool = _optional_bool("KALSHI_USE_LIVE_BALANCE", default=True)
# Live IOC must cross the book; paper filled at mid with no counterparty.
# Add this many cents of aggression on the *side* we buy (YES bid up / NO ask down).
KALSHI_LIVE_TAKE_CENTS: float = float(os.getenv("KALSHI_LIVE_TAKE_CENTS", "2") or "2")
# Kalshi exchange shard these series trade on (sharded 2026-08-24; crypto = 2).
# Collateral is per-shard, so sizing reads this shard's balance, not the
# cross-shard aggregate. Re-read from a market's `exchange_index` if Kalshi
# reassigns the category.
KALSHI_EXCHANGE_INDEX: int = int(os.getenv("KALSHI_EXCHANGE_INDEX", "2") or "2")
KALSHI_LIVE_TIME_IN_FORCE: str = (
    (_optional("KALSHI_LIVE_TIME_IN_FORCE") or "immediate_or_cancel").strip().lower()
)

# Multi-bot enable list. Default control = always-on conviction product path.
_bots_raw = _optional("ENABLED_BOTS") or "control"
ENABLED_BOTS: tuple[str, ...] = tuple(
    s.strip() for s in _bots_raw.split(",") if s.strip()
) or ("control",)

# Macro news RSS feeds (comma-separated). Used when MACRO_CONTEXT_ENABLED.
_macro_feeds_raw = _optional("MACRO_FEED_URLS") or (
    "https://www.federalreserve.gov/feeds/press_all.xml,"
    "https://www.cnbc.com/id/10000664/device/rss/rss.html,"
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
)
MACRO_FEED_URLS: tuple[str, ...] = tuple(
    s.strip() for s in _macro_feeds_raw.split(",") if s.strip()
)
_macro_extra = _optional("MACRO_KEYWORD_EXTRA") or ""
MACRO_KEYWORD_EXTRA: tuple[str, ...] = tuple(
    s.strip().lower() for s in _macro_extra.split(",") if s.strip()
)

# Shared ICT/HTF Claude refresh policy (see kalshi_cycle._should_refresh_htf).
# every_near_tick | once_per_window | ttl_event
HTF_REFRESH_MODE: str = (
    (_optional("HTF_REFRESH_MODE") or "every_near_tick").strip().lower()
)
HTF_BIAS_TTL_SEC: int = int(os.getenv("HTF_BIAS_TTL_SEC", "3600") or "3600")
HTF_M5_MOVE_PCT: float = float(os.getenv("HTF_M5_MOVE_PCT", "0.20") or "0.20")
HTF_REFRESH_ON_H1_CLOSE: bool = _optional_bool(
    "HTF_REFRESH_ON_H1_CLOSE", default=True
)

# --- EVA brain (hub Intelligence API) — zero-Claude bias source for eva_wick ---
# e.g. https://dashboard.eva.finance ; token must match hub SERVICE_API_TOKENS.
INTELLIGENCE_API_URL: str | None = _optional("INTELLIGENCE_API_URL")
INTELLIGENCE_SERVICE_TOKEN: str | None = _optional("INTELLIGENCE_SERVICE_TOKEN")
# Fail closed when the newest stance is older than this (minutes).
EVA_STANCE_MAX_AGE_MIN: float = float(
    os.getenv("EVA_STANCE_MAX_AGE_MIN", "90") or "90"
)
# Early take-profit: flatten when the bought side reaches entry × multiple.
EVA_WICK_TP_MULTIPLE: float = float(
    os.getenv("EVA_WICK_TP_MULTIPLE", "2.0") or "2.0"
)

# Claude-consuming side jobs — env-driven so the eva_wick profile can run
# with zero Anthropic spend (defaults preserve legacy behavior).
MACRO_CONTEXT_ENABLED: bool = _optional_bool("MACRO_CONTEXT_ENABLED", default=True)
WATCHDOG_ENABLED: bool = _optional_bool("WATCHDOG_ENABLED", default=True)
# True = Telegram sees only trades (+ TP/settles), not skip cards.
BROADCAST_ONLY_TRADES: bool = _optional_bool("BROADCAST_ONLY_TRADES", default=False)
