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
KALSHI_MAX_CONTRACTS: int = int(os.getenv("KALSHI_MAX_CONTRACTS", "5") or "5")
KALSHI_MIN_EDGE_CENTS: float = float(os.getenv("KALSHI_MIN_EDGE_CENTS", "3") or "3")
KALSHI_CYCLE_OFFSET_SEC: int = int(os.getenv("KALSHI_CYCLE_OFFSET_SEC", "30") or "30")
# Sizing vs ~$77 bankroll: each trade spends up to DEPLOY_PCT of bankroll (capped by MAX_CONTRACTS).
KALSHI_BANKROLL_USD: float = float(os.getenv("KALSHI_BANKROLL_USD", "77") or "77")
KALSHI_DEPLOY_PCT: float = float(os.getenv("KALSHI_DEPLOY_PCT", "0.05") or "0.05")
# When live, prefer Kalshi account balance for bankroll; fall back to KALSHI_BANKROLL_USD.
KALSHI_USE_LIVE_BALANCE: bool = _optional_bool("KALSHI_USE_LIVE_BALANCE", default=True)
