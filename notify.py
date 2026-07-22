"""Telegram notifications for Kalshi paper trades."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot

import access
import config
from models import KalshiSuggestion

logger = logging.getLogger(__name__)


def format_trade_card(suggestion: KalshiSuggestion) -> str:
    expiry = suggestion.expiry_ts or "?"
    edge = (
        f"{suggestion.edge_cents:.1f}¢"
        if suggestion.edge_cents is not None
        else "n/a"
    )
    fair = (
        f"{suggestion.fair_yes_cents:.1f}¢"
        if suggestion.fair_yes_cents is not None
        else "n/a"
    )
    mid = (
        f"{suggestion.mid_cents:.1f}¢"
        if suggestion.mid_cents is not None
        else f"{suggestion.entry_cents:.1f}¢"
        if suggestion.entry_cents is not None
        else "n/a"
    )
    return (
        f"Kalshi 15m paper trade\n"
        f"Asset: {suggestion.product_id}\n"
        f"Side: {suggestion.side}\n"
        f"Contracts: {suggestion.contracts}\n"
        f"Entry: {suggestion.entry_cents:.1f}¢\n"
        f"Mid/Fair: {mid} / {fair} (edge {edge})\n"
        f"Market: {suggestion.market_ticker}\n"
        f"Expiry: {expiry}\n"
        f"\nWhy: {suggestion.rationale}"
    )


async def broadcast_plain_text_async(text: str) -> None:
    """DM raw text to every allowlisted recipient."""
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    recipients = access.broadcast_recipient_ids()
    sent: set[int] = set()
    body = text.strip()[:4096]
    for user_id in recipients:
        if user_id in sent:
            continue
        try:
            await bot.send_message(chat_id=user_id, text=body)
            sent.add(user_id)
        except Exception:
            logger.exception("Failed to send plain broadcast to user %s", user_id)

    admin_chat = config.TELEGRAM_ADMIN_CHAT_ID or config.TELEGRAM_CHAT_ID
    if admin_chat:
        try:
            admin_id = int(str(admin_chat).strip())
        except ValueError:
            admin_id = None
        if admin_id is not None and admin_id not in sent:
            try:
                await bot.send_message(chat_id=admin_id, text=body)
            except Exception:
                logger.exception("Failed to send plain broadcast to admin %s", admin_chat)


def broadcast_plain_text(text: str) -> None:
    async def _run() -> None:
        await broadcast_plain_text_async(text)

    asyncio.run(_run())


def broadcast_kalshi_trade(suggestion: KalshiSuggestion) -> None:
    if not suggestion.is_trade():
        return
    broadcast_plain_text(format_trade_card(suggestion))
