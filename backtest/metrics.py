"""Backtest performance summaries."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import paper


def summarize_bot(bot_id: str) -> dict[str, Any]:
    stats = paper.get_stats(bot_id=bot_id)
    closed = paper.get_closed_positions(limit=10_000, bot_id=bot_id)
    trades = [p for p in closed if p.get("status") == "closed"]
    return {
        "bot_id": bot_id,
        "equity_usd": stats["equity_usd"],
        "starting_usd": stats["starting_usd"],
        "realized_pnl_usd": stats["realized_pnl_usd"],
        "win_rate": stats["win_rate"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "closed_count": stats["closed_count"],
        "open_count": stats["open_count"],
        "trades": trades,
    }


def write_summary_csv(
    rows: list[dict[str, Any]],
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "bot_id",
        "starting_usd",
        "equity_usd",
        "realized_pnl_usd",
        "win_rate",
        "wins",
        "losses",
        "closed_count",
        "open_count",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def write_trades_csv(
    bots: list[str],
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "bot_id",
        "market_ticker",
        "product_id",
        "side",
        "contracts",
        "entry_cents",
        "result",
        "pnl_usd",
        "opened_at",
        "closed_at",
        "rationale",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for bot_id in bots:
            for p in paper.get_closed_positions(limit=50_000, bot_id=bot_id):
                w.writerow(
                    {
                        "bot_id": bot_id,
                        "market_ticker": p.get("market_ticker"),
                        "product_id": p.get("product_id"),
                        "side": p.get("side"),
                        "contracts": p.get("contracts"),
                        "entry_cents": p.get("entry_cents"),
                        "result": p.get("result"),
                        "pnl_usd": p.get("pnl_usd"),
                        "opened_at": p.get("opened_at"),
                        "closed_at": p.get("closed_at"),
                        "rationale": (p.get("rationale") or "")
                        .replace("\n", " ")
                        .replace("\r", " "),
                    }
                )
    return path
