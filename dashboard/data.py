"""Read-only data accessors for the Kalshi paper dashboard."""

from __future__ import annotations

from typing import Any

import bot_config
import config
import paper


def get_status_payload() -> dict[str, Any]:
    stats = paper.get_stats()
    return {
        "bot": "kalshi_15m",
        "paper_only": config.KALSHI_PAPER_ONLY,
        "env": config.KALSHI_ENV,
        "series": list(config.KALSHI_SERIES),
        "equity_usd": stats["equity_usd"],
        "cash_usd": stats["cash_usd"],
        "open_count": stats["open_count"],
        "closed_count": stats["closed_count"],
        "epoch": bot_config.PAPER_EPOCH_LABEL,
    }


def get_performance_payload() -> dict[str, Any]:
    stats = paper.get_stats()
    return {
        "equity_usd": stats["equity_usd"],
        "starting_usd": stats["starting_usd"],
        "realized_pnl_usd": stats["realized_pnl_usd"],
        "win_rate": stats["win_rate"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "closed_count": stats["closed_count"],
        "open_count": stats["open_count"],
    }


def get_open_positions_payload() -> list[dict[str, Any]]:
    out = []
    for p in paper.get_open_positions():
        out.append(
            {
                "product_id": p["product_id"],
                "side": p["side"],
                "contracts": p["contracts"],
                "entry_cents": p["entry_cents"],
                "market_ticker": p["market_ticker"],
                "expiry_ts": p.get("expiry_ts"),
                "opened_at": p.get("opened_at"),
                "rationale": p.get("rationale") or "",
            }
        )
    return out


def get_closed_positions_payload(limit: int = 25) -> list[dict[str, Any]]:
    out = []
    for p in paper.get_closed_positions(limit=limit):
        out.append(
            {
                "product_id": p["product_id"],
                "side": p["side"],
                "contracts": p["contracts"],
                "entry_cents": p["entry_cents"],
                "market_ticker": p["market_ticker"],
                "result": p.get("result"),
                "pnl_usd": p.get("pnl_usd"),
                "closed_at": p.get("closed_at"),
                "rationale": p.get("rationale") or "",
            }
        )
    return out


def dashboard_context() -> dict[str, Any]:
    return {
        "status": get_status_payload(),
        "performance": get_performance_payload(),
        "open_positions": get_open_positions_payload(),
        "closed_positions": get_closed_positions_payload(),
    }
