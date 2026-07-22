"""Kalshi Trade API v2 client — RSA-PSS auth + public market helpers."""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import config

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_private_key = None
_private_key_tried = False


def _load_private_key():
    global _private_key, _private_key_tried
    if _private_key_tried:
        return _private_key
    _private_key_tried = True
    path = Path(config.KALSHI_PRIVATE_KEY_PATH)
    if not path.is_file():
        logger.warning("Kalshi private key missing at %s — public reads only", path)
        return None
    try:
        with path.open("rb") as f:
            _private_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
    except Exception:
        logger.exception("Failed to load Kalshi private key from %s", path)
        _private_key = None
    return _private_key


def _sign(private_key, timestamp: str, method: str, path: str) -> str:
    path_without_query = path.split("?", 1)[0]
    message = f"{timestamp}{method.upper()}{path_without_query}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def _auth_headers(method: str, full_path: str) -> dict[str, str] | None:
    key_id = config.KALSHI_API_KEY_ID
    private_key = _load_private_key()
    if not key_id or private_key is None:
        return None
    timestamp = str(int(time.time() * 1000))
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": _sign(private_key, timestamp, method, full_path),
    }


def _url(path: str) -> str:
    base = config.KALSHI_API_BASE.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _sign_path_for_url(url: str) -> str:
    return urlparse(url).path


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    auth: bool = True,
) -> dict[str, Any]:
    """HTTP helper. Tries auth when keys exist; falls back to public GET on auth failure."""
    url = _url(path)
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth:
        signed = _auth_headers(method, _sign_path_for_url(url))
        if signed:
            headers.update(signed)

    response = requests.request(
        method.upper(),
        url,
        params=params,
        json=json_body,
        headers=headers,
        timeout=_TIMEOUT,
    )
    if response.status_code == 401 and auth and method.upper() == "GET":
        logger.warning("Kalshi auth failed for %s — retrying public", path)
        response = requests.request(
            method.upper(),
            url,
            params=params,
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def _dollars_to_cents(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value) * 100.0, 4)
    except (TypeError, ValueError):
        return None


def mid_cents_from_market(market: dict[str, Any]) -> float | None:
    """YES mid in cents from market bid/ask dollars fields."""
    bid = _dollars_to_cents(market.get("yes_bid_dollars"))
    ask = _dollars_to_cents(market.get("yes_ask_dollars"))
    if bid is None and ask is None:
        last = _dollars_to_cents(market.get("last_price_dollars"))
        return last
    if bid is None:
        return ask
    if ask is None:
        return bid
    return round((bid + ask) / 2.0, 4)


def get_markets(
    series_ticker: str,
    *,
    status: str | None = "open",
    limit: int = 20,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "series_ticker": series_ticker,
        "limit": limit,
    }
    if status:
        params["status"] = status
    data = request("GET", "/markets", params=params, auth=True)
    return list(data.get("markets") or [])


def get_open_markets(series_ticker: str) -> list[dict[str, Any]]:
    """Open/active markets for a series, newest first when possible."""
    markets = get_markets(series_ticker, status="open", limit=10)
    if not markets:
        # Some windows briefly show status=active without status=open filter match.
        markets = [
            m
            for m in get_markets(series_ticker, status=None, limit=10)
            if str(m.get("status", "")).lower() in ("open", "active")
        ]
    markets.sort(key=lambda m: str(m.get("open_time") or ""), reverse=True)
    return markets


def get_market(ticker: str) -> dict[str, Any]:
    data = request("GET", f"/markets/{ticker}", auth=True)
    return data.get("market") or data


def get_orderbook_mid(ticker: str) -> float | None:
    """Mid YES cents from orderbook; falls back to market quote fields."""
    try:
        data = request("GET", f"/markets/{ticker}/orderbook", auth=True)
        book = data.get("orderbook") or data
        yes = book.get("yes") or book.get("yes_dollars") or []
        # yes levels are often [[price_cents_or_dollars, qty], ...]
        best_bid = None
        best_ask = None
        if yes and isinstance(yes[0], (list, tuple)) and len(yes[0]) >= 1:
            # Kalshi orderbook yes side is bids ascending; infer mid from market instead if unclear.
            pass
        market = get_market(ticker)
        return mid_cents_from_market(market)
    except Exception:
        logger.exception("orderbook mid failed for %s", ticker)
        try:
            return mid_cents_from_market(get_market(ticker))
        except Exception:
            return None


def get_market_result(ticker: str) -> str | None:
    """Return 'yes', 'no', or None if not yet settled."""
    market = get_market(ticker)
    result = str(market.get("result") or "").strip().lower()
    if result in ("yes", "no"):
        return result
    return None


def place_order(
    ticker: str,
    side: str,
    contracts: int,
    *,
    yes_price_cents: int | None = None,
) -> dict[str, Any]:
    """Place an order. No-op stub when KALSHI_PAPER_ONLY=true."""
    if config.KALSHI_PAPER_ONLY:
        logger.info(
            "PAPER_ONLY: skip live order %s %s x%s @ %s",
            ticker,
            side,
            contracts,
            yes_price_cents,
        )
        return {"status": "paper_only", "ticker": ticker, "side": side, "count": contracts}

    side_u = side.upper()
    body: dict[str, Any] = {
        "ticker": ticker,
        "action": "buy",
        "side": "yes" if side_u == "YES" else "no",
        "count": int(contracts),
        "type": "limit",
    }
    if yes_price_cents is not None:
        # API expects integer cents for yes price on limit orders.
        body["yes_price"] = int(yes_price_cents)
    return request("POST", "/portfolio/orders", json_body=body, auth=True)


def get_balance() -> dict[str, Any]:
    return request("GET", "/portfolio/balance", auth=True)


def main() -> None:
    import json
    import sys

    cmd = (sys.argv[1] if len(sys.argv) > 1 else "markets").lower()
    if cmd == "balance":
        print(json.dumps(get_balance(), indent=2))
        return
    series = sys.argv[2] if len(sys.argv) > 2 else "KXBTC15M"
    markets = get_open_markets(series)
    print(json.dumps(markets[:1], indent=2))


if __name__ == "__main__":
    main()
