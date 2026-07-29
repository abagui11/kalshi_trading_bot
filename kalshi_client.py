"""Kalshi Trade API v2 client — RSA-PSS auth + public market helpers."""

from __future__ import annotations

import base64
import logging
import time
import uuid
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
    if not response.ok:
        body_preview = (response.text or "")[:500]
        logger.error(
            "Kalshi %s %s -> %s: %s",
            method.upper(),
            path,
            response.status_code,
            body_preview,
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


def _fp_count(contracts: int) -> str:
    """Fixed-point contract count string required by order V2."""
    return f"{max(0, int(contracts)):.2f}"


def _fp_dollars_from_cents(cents: float) -> str:
    """Fixed-point dollar price string (YES-leg) for order V2."""
    return f"{max(0.0, float(cents)) / 100.0:.4f}"


def build_order_v2_body(
    ticker: str,
    side: str,
    contracts: int,
    *,
    side_price_cents: float,
    client_order_id: str | None = None,
    time_in_force: str = "immediate_or_cancel",
    take_cents: float = 0.0,
) -> dict[str, Any]:
    """Build CreateOrderV2Request body.

    V2 quotes the YES book only:
      - buy YES  -> side=bid at YES price
      - buy NO   -> side=ask at YES price (= 100¢ − NO price)

    ``take_cents`` worsens the *side* limit so an IOC can cross (paper filled at
    mid with no counterparty; live needs to take).
    """
    side_u = side.upper()
    take = max(0.0, float(take_cents))
    # Pay up to ``take`` more for the contract side we want to buy.
    paid_side_cents = min(99.0, max(1.0, float(side_price_cents) + take))
    if side_u == "YES":
        book_side = "bid"
        yes_cents = paid_side_cents
    elif side_u == "NO":
        book_side = "ask"
        yes_cents = 100.0 - paid_side_cents
    else:
        raise ValueError(f"side must be YES or NO, got {side!r}")
    yes_cents = min(99.0, max(1.0, yes_cents))

    return {
        "ticker": ticker,
        "client_order_id": client_order_id or str(uuid.uuid4()),
        "side": book_side,
        "count": _fp_count(contracts),
        "price": _fp_dollars_from_cents(yes_cents),
        "time_in_force": time_in_force,
        "self_trade_prevention_type": "taker_at_cross",
        # Echo for logging / ledger (not sent fields beyond API schema — strip before POST)
        "_side_limit_cents": paid_side_cents,
    }


def _api_order_body(body: dict[str, Any]) -> dict[str, Any]:
    """Drop internal keys before sending to Kalshi."""
    return {k: v for k, v in body.items() if not str(k).startswith("_")}


def filled_contract_count(order_resp: dict[str, Any]) -> float:
    """Parse V2 fill_count (fixed-point string) or legacy integer count."""
    if not order_resp:
        return 0.0
    raw = order_resp.get("fill_count")
    if raw is None:
        raw = order_resp.get("count")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def side_fill_cents_from_response(
    side: str,
    order_resp: dict[str, Any],
    *,
    fallback_side_cents: float,
) -> float:
    """Map V2 average_fill_price (YES dollars) back to side cents for the book."""
    avg = order_resp.get("average_fill_price")
    if avg is None or avg == "":
        return float(fallback_side_cents)
    try:
        yes_cents = float(avg) * 100.0
    except (TypeError, ValueError):
        return float(fallback_side_cents)
    side_u = side.upper()
    if side_u == "YES":
        return yes_cents
    if side_u == "NO":
        return 100.0 - yes_cents
    return float(fallback_side_cents)


def place_order(
    ticker: str,
    side: str,
    contracts: int,
    *,
    yes_price_cents: int | None = None,
) -> dict[str, Any]:
    """Place a live order via Create Order V2.

    ``yes_price_cents`` is the *side* entry in cents (YES price for YES buys,
    NO price for NO buys) — same convention as the legacy yes_price/no_price body.

    No-op stub when KALSHI_PAPER_ONLY=true.
    """
    import kalshi_sizing

    entry = float(yes_price_cents if yes_price_cents is not None else 0)
    try:
        kalshi_sizing.assert_order_allowed(int(contracts), entry if entry > 0 else 1.0)
    except ValueError as exc:
        logger.error("Refusing order: %s", exc)
        return {"status": "rejected", "error": str(exc), "ticker": ticker}

    if config.KALSHI_PAPER_ONLY:
        logger.info(
            "PAPER_ONLY: skip live order %s %s x%s @ %s",
            ticker,
            side,
            contracts,
            yes_price_cents,
        )
        return {
            "status": "paper_only",
            "ticker": ticker,
            "side": side,
            "count": contracts,
            "fill_count": _fp_count(contracts),
        }

    if yes_price_cents is None:
        return {
            "status": "rejected",
            "error": "missing side price",
            "ticker": ticker,
        }

    take = float(getattr(config, "KALSHI_LIVE_TAKE_CENTS", 2) or 0)
    tif = str(
        getattr(config, "KALSHI_LIVE_TIME_IN_FORCE", "immediate_or_cancel")
        or "immediate_or_cancel"
    ).strip().lower()
    if tif not in ("immediate_or_cancel", "fill_or_kill", "good_till_canceled"):
        tif = "immediate_or_cancel"

    body = build_order_v2_body(
        ticker,
        side,
        contracts,
        side_price_cents=float(yes_price_cents),
        take_cents=take,
        time_in_force=tif,
    )
    api_body = _api_order_body(body)
    logger.info(
        "Live order V2 %s %s x%s mid_side=%.1f¢ limit_side=%.1f¢ yes_px=%s tif=%s "
        "(client_order_id=%s)",
        ticker,
        api_body["side"],
        api_body["count"],
        float(yes_price_cents),
        float(body.get("_side_limit_cents") or yes_price_cents),
        api_body["price"],
        tif,
        api_body["client_order_id"],
    )
    resp = request("POST", "/portfolio/events/orders", json_body=api_body, auth=True)
    if isinstance(resp, dict):
        resp.setdefault("client_order_id", api_body["client_order_id"])
        resp.setdefault("ticker", ticker)
        resp["side_limit_cents"] = body.get("_side_limit_cents")
    return resp


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
