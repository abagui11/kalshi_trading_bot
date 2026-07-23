"""Read-only data accessors for the Kalshi multi-bot paper dashboard."""

from __future__ import annotations

import json
from typing import Any

import bot_config
import config
import paper
from dashboard.charts import resolve_chart_path
from patterns.market_structure_state import load_structure_state


def _parse_json_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (TypeError, json.JSONDecodeError):
        pass
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _chart_url(path: str | None) -> str | None:
    resolved = resolve_chart_path(path)
    if resolved is None:
        return None
    try:
        rel = resolved.relative_to(config.CHARTS_DIR.resolve())
        return f"/api/chart/file/{rel.as_posix()}"
    except ValueError:
        return None


def get_bots_payload() -> list[dict[str, Any]]:
    """Leaderboard cards: one row per enabled bot."""
    bots = paper.get_all_bot_stats()
    out = []
    for s in bots:
        decisions = paper.get_decisions(limit=200, bot_id=s["bot_id"])
        scores = [
            float(d["chart_read_score"])
            for d in decisions
            if d.get("chart_read_score") is not None
        ]
        avg_chart = sum(scores) / len(scores) if scores else None
        out.append(
            {
                "bot_id": s["bot_id"],
                "display_name": s.get("display_name") or s["bot_id"],
                "equity_usd": s["equity_usd"],
                "starting_usd": s["starting_usd"],
                "realized_pnl_usd": s["realized_pnl_usd"],
                "win_rate": s["win_rate"],
                "wins": s["wins"],
                "losses": s["losses"],
                "closed_count": s["closed_count"],
                "open_count": s["open_count"],
                "open_cost_usd": s["open_cost_usd"],
                "avg_chart_read": avg_chart,
                "decision_count": len(decisions),
            }
        )
    return out


def get_status_payload() -> dict[str, Any]:
    bots = get_bots_payload()
    control = next((b for b in bots if b["bot_id"] == "control"), bots[0] if bots else {})
    return {
        "bot": "kalshi_15m_multi",
        "paper_only": config.KALSHI_PAPER_ONLY,
        "env": config.KALSHI_ENV,
        "series": list(config.KALSHI_SERIES),
        "equity_usd": control.get("equity_usd"),
        "cash_usd": paper.get_stats(bot_id="control").get("cash_usd"),
        "open_count": sum(int(b.get("open_count") or 0) for b in bots),
        "closed_count": sum(int(b.get("closed_count") or 0) for b in bots),
        "epoch": bot_config.PAPER_EPOCH_LABEL,
        "watchdog_enabled": bot_config.WATCHDOG_ENABLED,
        "watchdog_execute": bot_config.watchdog_execute_enabled(),
        "broadcast_only_trades": bot_config.BROADCAST_ONLY_TRADES,
        "enabled_bots": list(bot_config.ENABLED_BOTS),
    }


def get_performance_payload(*, bot_id: str | None = None) -> dict[str, Any]:
    bid = bot_id or "control"
    stats = paper.get_stats(bot_id=bid)
    decisions = paper.get_decisions(limit=200, bot_id=bid)
    scores = [
        float(d["chart_read_score"])
        for d in decisions
        if d.get("chart_read_score") is not None
    ]
    avg_chart = sum(scores) / len(scores) if scores else None
    return {
        "bot_id": bid,
        "display_name": bot_config.BOT_DISPLAY_NAMES.get(bid, bid),
        "equity_usd": stats["equity_usd"],
        "starting_usd": stats["starting_usd"],
        "realized_pnl_usd": stats["realized_pnl_usd"],
        "win_rate": stats["win_rate"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "closed_count": stats["closed_count"],
        "open_count": stats["open_count"],
        "avg_chart_read": avg_chart,
        "decision_count": len(decisions),
    }


def get_structure_payload() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for coinbase in bot_config.TRADED_PRODUCTS:
        state = load_structure_state(coinbase)
        out.append(
            {
                "product_id": coinbase,
                "product_label": bot_config.product_label(coinbase),
                "htf_bias": state.htf_bias,
                "h1_bias": state.h1_bias,
                "range_24h_label": state.range_24h_label,
                "setup_phase": state.setup_phase,
                "watching": list(state.watching),
                "window_thesis": state.window_thesis,
                "market_ticker": state.market_ticker,
                "spot": state.spot,
                "updated_at": state.updated_at,
                "alerts": list(state.alerts),
                "setup_tags": list(state.setup_tags)[:12],
                "h4_chart_url": _chart_url(state.structure_chart_path),
                "h1_chart_url": _chart_url(state.h1_chart_path),
                "primary_demand": state.primary_demand,
                "primary_supply": state.primary_supply,
            }
        )
    return out


def get_open_positions_payload(*, bot_id: str | None = None) -> list[dict[str, Any]]:
    out = []
    for p in paper.get_open_positions(bot_id=bot_id):
        out.append(
            {
                "bot_id": p.get("bot_id") or "control",
                "product_id": p["product_id"],
                "side": p["side"],
                "contracts": p["contracts"],
                "entry_cents": p["entry_cents"],
                "market_ticker": p["market_ticker"],
                "expiry_ts": p.get("expiry_ts"),
                "opened_at": p.get("opened_at"),
                "rationale": p.get("rationale") or "",
                "chart_path": p.get("chart_path"),
            }
        )
    return out


def get_closed_positions_payload(
    limit: int = 25,
    *,
    bot_id: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    for p in paper.get_closed_positions(limit=limit, bot_id=bot_id):
        out.append(
            {
                "bot_id": p.get("bot_id") or "control",
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


def enrich_decision(row: dict[str, Any]) -> dict[str, Any]:
    tags = _parse_json_list(row.get("setup_tags"))
    skips = _parse_json_list(row.get("skip_codes") or row.get("would_skip_reasons"))
    side = str(row.get("side") or "SKIP")
    is_skip = side.upper() == "SKIP" or not int(row.get("opened") or 0)
    return {
        "id": row.get("id"),
        "bot_id": row.get("bot_id") or "control",
        "ts": row.get("ts"),
        "cycle_id": row.get("cycle_id"),
        "series": row.get("series"),
        "market_ticker": row.get("market_ticker"),
        "product_id": row.get("product_id"),
        "side": side,
        "opened": bool(row.get("opened")),
        "is_skip": is_skip and side.upper() == "SKIP",
        "is_trade": side.upper() in ("YES", "NO"),
        "rationale": row.get("rationale") or "",
        "yes_mid_cents": row.get("yes_mid_cents"),
        "entry_cents": row.get("entry_cents"),
        "fair_yes_cents": row.get("model_fair_yes_cents"),
        "edge_cents": row.get("edge_cents"),
        "ict_bias": row.get("ict_bias"),
        "htf_bias": row.get("h1_bias_tag"),
        "gate_outcome": row.get("gate_outcome"),
        "trigger_type": row.get("trigger_type"),
        "trigger_name": row.get("trigger_name"),
        "setup_tags": tags,
        "skip_codes": skips,
        "seconds_to_expiry": row.get("seconds_to_expiry"),
        "chart_read_score": row.get("chart_read_score"),
        "critic_downgraded": bool(row.get("critic_downgraded")),
        "structure_chart_url": _chart_url(row.get("structure_chart_path")),
        "entry_chart_url": _chart_url(
            row.get("entry_chart_path") or row.get("chart_path")
        ),
    }


def get_journal_payload(
    *,
    limit: int = 50,
    filter_mode: str = "all",
    bot_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = paper.get_decisions(limit=limit, bot_id=bot_id)
    out = [enrich_decision(r) for r in rows]
    mode = (filter_mode or "all").lower()
    if mode == "trades":
        out = [d for d in out if d["is_trade"]]
    elif mode == "skips":
        out = [d for d in out if d["side"] == "SKIP"]
    return out


def _default_bot_tab(bots: list[dict[str, Any]]) -> str:
    if not bots:
        return "control"
    # Prefer best realized PnL; fall back to control if present.
    best = bots[0]["bot_id"]
    if any(b["bot_id"] == "control" for b in bots):
        # Use leader unless control is requested as stable default — plan says best PnL or control.
        return best
    return best


def dashboard_context(
    *,
    filter_mode: str = "all",
    bot_id: str | None = None,
) -> dict[str, Any]:
    bots = get_bots_payload()
    enabled = list(bot_config.ENABLED_BOTS)
    active = bot_id if bot_id in enabled else _default_bot_tab(bots)
    return {
        "status": get_status_payload(),
        "bots": bots,
        "active_bot": active,
        "bot_tabs": [
            {
                "bot_id": bid,
                "display_name": bot_config.BOT_DISPLAY_NAMES.get(bid, bid),
            }
            for bid in enabled
        ],
        "performance": get_performance_payload(bot_id=active),
        "open_positions": get_open_positions_payload(bot_id=active),
        "closed_positions": get_closed_positions_payload(bot_id=active),
        "structure": get_structure_payload(),
        "journal": get_journal_payload(filter_mode=filter_mode, bot_id=active),
        "journal_filter": filter_mode,
    }
