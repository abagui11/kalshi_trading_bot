"""Telegram welcome copy and inline keyboards for beta onboarding."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import bot_config
import config

CB_FUND = "ui:fund"
CB_METRICS = "ui:metrics"
CB_RESEARCH = "ui:research"
CB_REFRESH = "ui:refresh"

WELCOME_MESSAGE = (
    "Welcome to the ETH/BTC Trading Agent (beta).\n\n"
    "This bot does NOT place real trades. It runs an ICT-style swing/day strategy "
    "on a shared paper portfolio and shows its rationale for ETH and BTC setups "
    "(including W1 ETH/BTC relative strength).\n\n"
    "Thesis: as capital concentrates on permissionless agential rails, "
    "liquid crypto pairs create large, repeatable ICT opportunities — "
    "order blocks, SFPs, and HTF structure that this agent watches hourly.\n\n"
    "Fund is a placeholder for future real funding. Today it adds a fake "
    f"${bot_config.PAPER_CONTRIBUTION_USD:,.0f} paper deposit (once per user) "
    "so you can track your share of the book.\n\n"
    "Use the buttons below, or /research for market studies. Not financial advice."
)

RESEARCH_HELP = (
    "Research — how to use it\n\n"
    "• /research — topic catalog (digest, funding, volume, dominance, macro, SFP studies)\n"
    "• /research funding — run a specific topic\n"
    "• Or ask in plain English: \"What's ETH funding?\" / \"weekly SFP study\"\n\n"
    "Research is read-only context for the paper strategy; it does not move the portfolio."
)


def main_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("Fund", callback_data=CB_FUND),
            InlineKeyboardButton("My Metrics", callback_data=CB_METRICS),
        ],
    ]
    dash = config.DASHBOARD_PUBLIC_URL
    if dash:
        rows.append(
            [InlineKeyboardButton("Portfolio", url=dash.rstrip("/"))]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "Portfolio (set DASHBOARD_PUBLIC_URL)",
                    callback_data=CB_REFRESH,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("Research", callback_data=CB_RESEARCH),
            InlineKeyboardButton("Refresh", callback_data=CB_REFRESH),
        ]
    )
    return InlineKeyboardMarkup(rows)


def format_metrics_message(metrics: dict) -> str:
    if not metrics.get("ok"):
        return (
            "My Metrics\n\n"
            "You have not Funded yet. Tap Fund to add a fake "
            f"${bot_config.PAPER_CONTRIBUTION_USD:,.0f} paper deposit "
            "(placeholder for future real funding)."
        )
    return (
        "My Metrics (paper)\n\n"
        f"Your deposit: ${metrics['amount_usd']:,.0f}\n"
        f"Ownership: {metrics['share_pct']:.2f}%\n"
        f"Your equity: ${metrics['equity_usd']:,.2f}\n"
        f"Your PnL: ${metrics['pnl_usd']:+,.2f} ({metrics['pnl_pct']:+.2f}%)\n"
        f"Portfolio equity: ${metrics['portfolio_equity_usd']:,.2f}\n"
        f"Total contributed: ${metrics['total_contributed_usd']:,.0f}\n\n"
        "Figures track your share of the shared paper book."
    )


def format_fund_result(result: dict) -> str:
    if not result.get("ok"):
        reason = result.get("reason") or "failed"
        if reason == "already_funded":
            amount = float(
                result.get("amount_usd")
                or result.get("amount")
                or bot_config.PAPER_CONTRIBUTION_USD
            )
            return (
                "Already funded.\n\n"
                f"Your deposit: ${amount:,.0f}\n"
                f"Ownership: {float(result.get('share_pct') or 0):.2f}%\n"
                "Tap My Metrics for live equity and PnL."
            )
        return f"Fund failed ({reason})."
    amount = float(result.get("amount_usd") or result.get("amount") or 0)
    return (
        "Funded (paper).\n\n"
        f"Added ${amount:,.0f} to the shared portfolio.\n"
        f"Your ownership: {float(result['share_pct']):.2f}%\n"
        f"Book cash: ${float(result['cash_usd']):,.2f}\n"
        f"Total contributed: ${float(result['total_contributed_usd']):,.0f}\n\n"
        "This is a placeholder for future real funding — nothing left your wallet."
    )
