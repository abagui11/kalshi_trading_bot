"""Telegram notifications for Kalshi paper decisions (trades + skips)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Bot, InputFile

import access
import config
import paper
from models import KalshiSuggestion

logger = logging.getLogger(__name__)


def format_decision_card(suggestion: KalshiSuggestion, *, opened: bool = False) -> str:
    """Detailed trade or skip card for Telegram."""
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
        else (
            f"{suggestion.entry_cents:.1f}¢"
            if suggestion.entry_cents is not None
            else "n/a"
        )
    )
    stats = paper.get_stats()
    header = (
        "Kalshi 15m PAPER TRADE"
        if suggestion.is_trade() and opened
        else (
            "Kalshi 15m TRADE SIGNAL"
            if suggestion.is_trade()
            else "Kalshi 15m SKIP"
        )
    )
    lines = [
        header,
        f"Asset: {suggestion.product_id}",
        f"Series: {suggestion.series}",
        f"Market: {suggestion.market_ticker or 'n/a'}",
        f"Decision: {suggestion.side}",
    ]
    if suggestion.is_trade():
        lines.extend(
            [
                f"Contracts: {suggestion.contracts}",
                f"Entry: {suggestion.entry_cents:.1f}¢"
                if suggestion.entry_cents is not None
                else "Entry: n/a",
            ]
        )
    lines.extend(
        [
            f"Kalshi YES mid: {mid}",
            f"Model fair YES: {fair}",
            f"Edge: {edge} (min {config.KALSHI_MIN_EDGE_CENTS}¢)",
            f"Sizing: bankroll ${config.KALSHI_BANKROLL_USD:.0f} · "
            f"deploy {config.KALSHI_DEPLOY_PCT*100:.0f}%/trade · max {config.KALSHI_MAX_CONTRACTS} ct",
            f"Expiry / close: {expiry}",
            f"Paper equity: ${stats['equity_usd']:.2f} | open {stats['open_count']} | "
            f"{stats['wins']}W/{stats['losses']}L",
            "",
            "Why:",
            suggestion.rationale.strip() or "(no rationale)",
        ]
    )
    return "\n".join(lines)


def format_settle_card(closed: dict) -> str:
    pnl = float(closed.get("pnl_usd") or 0)
    return (
        "Kalshi 15m SETTLED\n"
        f"Asset: {closed.get('product_id')}\n"
        f"Side: {closed.get('side')} x{closed.get('contracts')}\n"
        f"Entry: {float(closed.get('entry_cents') or 0):.1f}¢\n"
        f"Result: {closed.get('result')}\n"
        f"PnL: ${pnl:+.2f}\n"
        f"Market: {closed.get('market_ticker')}\n"
        f"\n{paper.format_stats_text()}"
    )


async def _recipients() -> list[int]:
    return access.broadcast_recipient_ids()


async def broadcast_plain_text_async(text: str) -> None:
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    recipients = await _recipients()
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


async def broadcast_decision_async(
    suggestion: KalshiSuggestion,
    *,
    chart_path: str | None = None,
    opened: bool = False,
) -> None:
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    recipients = await _recipients()
    full = format_decision_card(suggestion, opened=opened)[:4096]
    path = Path(chart_path) if chart_path else None
    use_photo = path is not None and path.is_file()
    short_caption = (
        f"{suggestion.product_id} {suggestion.side}"
        + (
            f" x{suggestion.contracts} @ {suggestion.entry_cents:.1f}¢"
            if suggestion.is_trade() and suggestion.entry_cents is not None
            else ""
        )
    )[:1024]

    for user_id in recipients:
        try:
            if use_photo:
                with path.open("rb") as fh:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=InputFile(fh, filename=path.name),
                        caption=short_caption,
                    )
            await bot.send_message(chat_id=user_id, text=full)
        except Exception:
            logger.exception("Failed to send decision to user %s", user_id)
            try:
                await bot.send_message(chat_id=user_id, text=full)
            except Exception:
                logger.exception("Text fallback failed for user %s", user_id)


def broadcast_decision(
    suggestion: KalshiSuggestion,
    *,
    chart_path: str | None = None,
    opened: bool = False,
) -> None:
    async def _run() -> None:
        await broadcast_decision_async(
            suggestion, chart_path=chart_path, opened=opened
        )

    asyncio.run(_run())


def broadcast_kalshi_trade(
    suggestion: KalshiSuggestion,
    *,
    chart_path: str | None = None,
) -> None:
    """Backward-compatible: trade fill notification with optional chart."""
    if not suggestion.is_trade():
        return
    broadcast_decision(suggestion, chart_path=chart_path, opened=True)


def broadcast_settle(closed: dict) -> None:
    broadcast_plain_text(format_settle_card(closed))
