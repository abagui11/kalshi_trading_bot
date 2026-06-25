"""Telegram bot handlers — access gate, status, chat Q&A, and research."""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import access
import analytics
import chat
import config
import ledger
import notify
import paper
import research

logger = logging.getLogger(__name__)

PAYWALL_MESSAGE = (
    "Access required to receive hourly ETH trade suggestions.\n\n"
    "Contact us to subscribe. Once approved, your Telegram ID will be added to the allowlist."
)

WELCOME_MESSAGE = (
    "Welcome to the ETH Trading Agent.\n\n"
    "You will receive an hourly trade suggestion (chart + rationale) if a setup is found.\n"
    "Reply anytime to ask about the latest suggestion — e.g. \"Why this entry?\" or "
    "\"What would invalidate the trade?\"\n\n"
    "Research: ask about historical patterns, e.g. \"What % of weekly SFPs reversed "
    "in the past 4 years?\" or use /research weekly_sfp\n\n"
    "Paper PnL assumes a ${start:,.0f} portfolio with 1% risk per trade. Not financial advice."
)

_RESEARCH_KEYWORDS = re.compile(
    r"(weekly\s+sfp|sfp\s+reversal|%.*sfp|sfp.*%|sfp.*past|past.*sfp|"
    r"research\s+weekly|how\s+many\s+sfp)",
    re.IGNORECASE,
)


def _username(update: Update) -> str | None:
    user = update.effective_user
    if user is None:
        return None
    return user.username


def _is_research_query(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized.startswith("/research"):
        return True
    return bool(_RESEARCH_KEYWORDS.search(text))


def _research_years(text: str) -> int:
    match = re.search(r"(\d+)\s*years?", text, re.IGNORECASE)
    if match:
        return max(1, min(int(match.group(1)), 10))
    return 4


async def _reply(update: Update, text: str) -> None:
    if update.message is None:
        return
    await update.message.reply_text(text)


async def _handle_research(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if update.message is None:
        return

    years = _research_years(text)
    await _reply(update, f"Analyzing weekly SFPs over the past {years} years...")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, analytics.weekly_sfp_report, years)
    except Exception:
        logger.exception("Research handler failed")
        await _reply(update, "Sorry, the research analysis failed. Try again later.")
        return

    bot = context.bot
    chat_id = update.effective_chat.id if update.effective_chat else update.message.chat_id
    try:
        await notify.send_research_to_chat(
            bot,
            chat_id,
            result.chart_path,
            result.caption,
            result.summary_text,
        )
    except Exception:
        logger.exception("Failed to send research chart")
        await _reply(update, result.summary_text[:4096])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    access.register_user(user.id, _username(update))

    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return

    welcome = WELCOME_MESSAGE.format(start=config.PAPER_PORTFOLIO_VALUE)
    spot = research.get_spot_price()
    pnl = paper.format_pnl_footer(spot)
    latest = ledger.get_latest_suggestion()

    lines = [welcome, "", pnl]
    if latest:
        lines.append("")
        lines.append(f"Latest: {latest['action']} @ cycle {latest['cycle_id']}")
        if latest.get("rationale"):
            rationale = str(latest["rationale"]).strip()
            max_len = 500
            if len(rationale) > max_len:
                rationale = rationale[:max_len].rstrip() + "..."
            lines.append(rationale)

    await _reply(update, "\n".join(lines)[:4096])


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return

    await _reply(
        update,
        "Commands:\n"
        "/start — welcome + latest status\n"
        "/status — current suggestion + paper PnL\n"
        "/research weekly_sfp — weekly SFP reversal study (4 years)\n"
        "/help — this message\n\n"
        "Ask about the latest hourly suggestion, e.g.:\n"
        "• Why this entry?\n"
        "• What invalidates the trade?\n"
        "• How does this match the SFP example?\n\n"
        "Research questions (returns chart + stats), e.g.:\n"
        "• What % of weekly SFPs resulted in a reversal in the past 4 years?\n"
        "• /research weekly_sfp",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return

    latest = ledger.get_latest_suggestion()
    spot = research.get_spot_price()
    pnl = paper.format_pnl_footer(spot)

    if latest is None:
        await _reply(update, f"No suggestions yet.\n\n{pnl}")
        return

    tps = ", ".join(f"{tp:,.2f}" for tp in latest.get("take_profits", [])) or "n/a"
    body = (
        f"Cycle: {latest['cycle_id']}\n"
        f"Action: {latest['action']}\n"
        f"Entry: {latest.get('entry')}\n"
        f"SL: {latest.get('stop_loss')}\n"
        f"TP: {tps}\n"
        f"R/R: {latest.get('risk_reward')}\n\n"
        f"Rationale:\n{latest.get('rationale', '')}\n\n"
        f"{pnl}"
    )
    await _reply(update, body[:4096])


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    access.register_user(user.id, _username(update))

    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return

    args = context.args or []
    subcmd = args[0].lower() if args else "weekly_sfp"
    if subcmd not in ("weekly_sfp", "weekly-sfp", "sfp"):
        await _reply(
            update,
            "Usage: /research weekly_sfp\n\n"
            "Or ask in plain text, e.g. \"What % of weekly SFPs reversed in the past 4 years?\"",
        )
        return

    years = 4
    for arg in args[1:]:
        match = re.search(r"(\d+)", arg)
        if match:
            years = max(1, min(int(match.group(1)), 10))
            break

    text = f"weekly sfp past {years} years"
    await _handle_research(update, context, text)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None or not update.message.text:
        return

    access.register_user(user.id, _username(update))

    if not access.is_allowed(user.id):
        await _reply(update, PAYWALL_MESSAGE)
        return

    user_text = update.message.text.strip()

    if _is_research_query(user_text):
        await _handle_research(update, context, user_text)
        return

    await update.message.chat.send_action("typing")

    loop = asyncio.get_running_loop()
    try:
        reply = await loop.run_in_executor(None, chat.answer, user_text)
    except Exception:
        logger.exception("Chat handler failed")
        reply = "Sorry, something went wrong processing your message."

    spot = research.get_spot_price()
    pnl = paper.format_pnl_footer(spot)
    await _reply(update, f"{reply}\n\n{pnl}"[:4096])


def build_application() -> Application:
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
