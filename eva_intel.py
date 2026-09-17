"""Read EVA brain stances (H4/H1/M15) from the hub Intelligence API.

Zero-Claude bias source for the Kalshi bot: the hub already paid for the
vision tokens; this module only reads the persisted result over
``GET {INTELLIGENCE_API_URL}/api/v1/intelligence/latest`` with a service token.

Fail-closed: if the API is unreachable, the token is missing, or the stances
are older than ``EVA_STANCE_MAX_AGE_MIN``, ``get_stances`` returns ``None``
and the strategy must not trade.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

TIMEFRAMES = ("H4", "H1", "M15")

# One shared payload cache: the API returns both products at once.
_POLL_SEC = 120.0
_lock = threading.Lock()
_cache: dict[str, Any] = {"fetched_at": 0.0, "payload": None}


def _fetch_latest() -> dict[str, Any] | None:
    url = config.INTELLIGENCE_API_URL
    token = config.INTELLIGENCE_SERVICE_TOKEN
    if not url or not token:
        logger.warning("EVA intel not configured (INTELLIGENCE_API_URL/_SERVICE_TOKEN)")
        return None
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/api/v1/intelligence/latest",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("EVA intel fetch failed")
        return None


def _payload() -> dict[str, Any] | None:
    now = time.time()
    with _lock:
        if _cache["payload"] is not None and now - _cache["fetched_at"] < _POLL_SEC:
            return _cache["payload"]
    fresh = _fetch_latest()
    with _lock:
        if fresh is not None:
            _cache["payload"] = fresh
            _cache["fetched_at"] = now
        # On fetch failure keep the old payload; staleness check below
        # decides whether it is still usable.
        return _cache["payload"]


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def get_stances(
    product_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Return ``{"H4": {...}, "H1": {...}, "M15": {...}}`` for a product.

    Each entry: ``{"stance": bullish|neutral|bearish, "confidence": float,
    "rationale": str, "cycle_ts": str}``. Returns ``None`` (fail closed) when
    the API/config is unavailable, any timeframe is missing, or the newest
    stance is older than ``EVA_STANCE_MAX_AGE_MIN`` minutes.
    """
    payload = _payload()
    if not payload:
        return None
    rows = payload.get("stances") or []
    out: dict[str, dict[str, Any]] = {}
    newest: datetime | None = None
    for row in rows:
        if str(row.get("product_id")) != product_id:
            continue
        tf = str(row.get("timeframe") or "").upper()
        if tf not in TIMEFRAMES:
            continue
        ts = _parse_ts(row.get("created_at")) or _parse_ts(row.get("cycle_ts"))
        prev_ts = _parse_ts(out[tf]["_ts"]) if tf in out else None
        if tf in out and ts is not None and prev_ts is not None and ts <= prev_ts:
            continue
        out[tf] = {
            "stance": str(row.get("stance") or "neutral").lower(),
            "confidence": float(row.get("confidence") or 0.5),
            "rationale": str(row.get("rationale") or ""),
            "cycle_ts": str(row.get("cycle_ts") or ""),
            "_ts": (ts.isoformat() if ts else None),
        }
        if ts is not None and (newest is None or ts > newest):
            newest = ts

    if any(tf not in out for tf in TIMEFRAMES):
        logger.warning("EVA intel incomplete for %s: have %s", product_id, sorted(out))
        return None

    ref = now or datetime.now(timezone.utc)
    max_age_sec = float(config.EVA_STANCE_MAX_AGE_MIN) * 60.0
    if newest is None or (ref - newest).total_seconds() > max_age_sec:
        logger.warning(
            "EVA intel stale for %s (newest=%s, max_age_min=%s)",
            product_id,
            newest,
            config.EVA_STANCE_MAX_AGE_MIN,
        )
        return None
    return out


def get_conditional_reads(product_id: str) -> dict[str, dict[str, Any]]:
    """{timeframe: read} for the hub's experimental conditional ICT read.

    Shadow only. The hub ships this block flagged ``consume: false`` and it is
    unscored, so nothing here may gate a trade — it exists so the wick bot can
    record what it *would* have done and let the read be judged on this book.
    Returns ``{}`` on anything unexpected rather than raising: a counterfactual
    is bookkeeping and must never be able to stop a live decision.
    """
    payload = _payload() or {}
    block = payload.get("conditional_reads") or {}
    reads = block.get("reads") if isinstance(block, dict) else block
    out: dict[str, dict[str, Any]] = {}
    for row in reads or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("product_id")) != product_id:
            continue
        tf = str(row.get("timeframe") or "").upper()
        if tf:
            out[tf] = row
    return out


def conditional_counterfactual(
    product_id: str,
    *,
    actual_side: str | None,
    lean: str | None,
) -> dict[str, Any]:
    """What the conditional read would have implied for a wick decision.

    ``lean`` is what the scalar board gave (H1 falling back to H4); the read's
    H1 bias is the like-for-like replacement. A null bias is an abstention, not
    a neutral — the wick bot would simply not have had a side, which is a
    different outcome from having a neutral one and is recorded as such.
    """
    reads = get_conditional_reads(product_id)
    read = reads.get("H1") or reads.get("H4") or {}
    bias = read.get("bias")
    would_side = None if not bias else ("YES" if bias == "bullish" else "NO")
    return {
        "cond_tf": read.get("timeframe"),
        "cond_bias": bias or "abstain",
        "cond_would_side": would_side,
        "cond_lean_agreed": None if not bias else (bias == lean),
        "cond_side_agreed": (
            None if would_side is None or actual_side is None
            else would_side == actual_side
        ),
        "cond_state": read.get("repelling_state"),
        "cond_stale": bool(read.get("stale_invalidation")),
    }


def reset_cache() -> None:
    """Test hook."""
    with _lock:
        _cache["payload"] = None
        _cache["fetched_at"] = 0.0
