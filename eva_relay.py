"""Relay Kalshi trade cards through the EVA bot to strategy subscribers.

The Kalshi lanes are subscribable strategies on the EVA product
(kalshi_reversal = eva_streak, kalshi_wick = eva_wick), but this process owns
neither the EVA bot token's update stream nor its subscriber list. So the
relay is SEND ONLY, exactly like the mill's delivery:

  - recipients come from the hub's /api/v1/subscribers?strategy=<key> route
    (same service token as the intelligence reads), and
  - cards are pushed with plain HTTPS calls against the EVA bot token
    (EVA_TELEGRAM_BOT_TOKEN); the hub's dispatcher services the Accept/Reject
    callbacks (kalshi:accept:... / kalshi:reject:...).

Cards only — no capital routes to Kalshi from the pool yet, and the hub's
callback handler says so on Accept. Everything here is best-effort: a relay
failure must never break the bot's own trading loop or its native broadcast.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

import config
from models import KalshiSuggestion

logger = logging.getLogger(__name__)

_TIMEOUT = 30

# EVA product bot token (NOT this bot's own token). Unset = relay disabled.
EVA_TELEGRAM_BOT_TOKEN: str | None = (
    os.getenv("EVA_TELEGRAM_BOT_TOKEN", "").strip() or None
)

# bot_id -> strategy key on the EVA product (strategy_catalog wire keys).
_STRATEGY_BY_BOT = {
    "eva_streak": ("kalshi_reversal", "Kalshi 15m Reversal"),
    "eva_wick": ("kalshi_wick", "Kalshi 15m Wick"),
}


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{EVA_TELEGRAM_BOT_TOKEN}/{method}"


def _subscribers(strategy: str) -> list[int]:
    url = config.INTELLIGENCE_API_URL
    token = config.INTELLIGENCE_SERVICE_TOKEN
    if not url or not token:
        return []
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/api/v1/subscribers",
            params={"strategy": strategy},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        return [int(r) for r in payload.get("recipients") or []]
    except Exception:
        logger.exception("eva relay: subscriber fetch failed for %s", strategy)
        return []


def _keyboard(strategy: str, ref: str) -> str:
    # Callback data caps at 64 bytes; the ref is display context, not a lookup
    # key, so truncation is safe.
    ref = ref[: 64 - len(f"kalshi:accept:{strategy}:")]
    return json.dumps({
        "inline_keyboard": [[
            {"text": "Accept", "callback_data": f"kalshi:accept:{strategy}:{ref}"},
            {"text": "Reject", "callback_data": f"kalshi:reject:{strategy}:{ref}"},
        ]]
    })


def _card_text(suggestion: KalshiSuggestion, label: str, *, opened: bool) -> str:
    """User-facing card — leaner than the internal ops card."""
    entry = (
        f"{suggestion.entry_cents:.0f}¢"
        if suggestion.entry_cents is not None else "n/a"
    )
    header = f"{label} · {'LIVE FILL' if opened else 'TRADE SIGNAL'}"
    lines = [
        header,
        "",
        f"{suggestion.product_id} — {suggestion.side} x{suggestion.contracts} "
        f"@ {entry}",
        f"Market: {suggestion.market_ticker or 'n/a'}",
        f"Window closes: {suggestion.expiry_ts or '?'}",
    ]
    rationale = (suggestion.rationale or "").strip()
    if rationale:
        lines += ["", rationale[:500]]
    lines += [
        "",
        "Settles within the 15-minute window. Capital deployment to Kalshi "
        "is coming soon — this card is the strategy's live idea stream.",
    ]
    return "\n".join(lines)[:4096]


def relay_decision(
    suggestion: KalshiSuggestion,
    *,
    chart_path: str | None = None,
    opened: bool = False,
) -> None:
    """Push one trade card to that lane's EVA subscribers. Best-effort."""
    if EVA_TELEGRAM_BOT_TOKEN is None:
        return
    if not suggestion.is_trade():
        return
    mapped = _STRATEGY_BY_BOT.get(str(suggestion.bot_id or ""))
    if mapped is None:
        return
    strategy, label = mapped
    recipients = _subscribers(strategy)
    if not recipients:
        return

    text = _card_text(suggestion, label, opened=opened)
    keyboard = _keyboard(strategy, str(suggestion.market_ticker or ""))
    photo = Path(chart_path) if chart_path else None
    if photo is not None and not photo.is_file():
        photo = None

    for user_id in recipients:
        try:
            if photo is not None:
                with photo.open("rb") as fh:
                    requests.post(
                        _api("sendPhoto"),
                        data={
                            "chat_id": user_id,
                            "caption": f"{label} — {suggestion.product_id} "
                                       f"{suggestion.side}"[:1024],
                        },
                        files={"photo": (photo.name, fh)},
                        timeout=_TIMEOUT,
                    )
            resp = requests.post(
                _api("sendMessage"),
                data={
                    "chat_id": user_id,
                    "text": text,
                    "reply_markup": keyboard,
                },
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                logger.warning(
                    "eva relay: send to %s refused: %s", user_id, resp.text[:200]
                )
        except Exception:
            logger.exception("eva relay: send to %s failed", user_id)
