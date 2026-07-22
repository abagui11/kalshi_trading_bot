"""Telegram bot — /start, /stats, /positions for Kalshi 15m paper trader."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import access
import config
import paper

logger = logging.getLogger(__name__)

PAYWALL_MESSAGE = (
    "This bot is private while testing. "
    "Your Telegram id is not on ALLOWED_TELEGRAM_IDS."
)

WELCOME = (
    "Kalshi BTC/ETH 15m paper bot\n\n"
    "I post trade + why when I take a paper fill on KXBTC15M / KXETH15M.\n"
    "Commands:\n"
    "/stats — equity, win rate, last 10\n"
    "/positions — open paper positions\n"
    "/help — this message"
)


async def _reply(update: Update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text[:4096])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    access.register_user(user.id, user.username)
    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return
    await _reply(update, WELCOME)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return
    await _reply(update, WELCOME)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return
    paper.init_db()
    await _reply(update, paper.format_stats_text())


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return
    paper.init_db()
    await _reply(update, paper.format_positions_text())


def build_application() -> Application:
    access.init_db()
    paper.init_db()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("positions", cmd_positions))
    return app
