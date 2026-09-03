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
                "decision_count": paper.count_decisions(bot_id=s["bot_id"]),
            }
        )
    return out


def get_status_payload() -> dict[str, Any]:
    bots = get_bots_payload()
    primary = next(
        (b for b in bots if b["bot_id"] in bot_config.ENABLED_BOTS),
        bots[0] if bots else {},
    )
    primary_id = primary.get("bot_id") or (
        bot_config.ENABLED_BOTS[0] if bot_config.ENABLED_BOTS else "control"
    )
    paused_note = None
    paused_path = config.ROOT_DIR / "PAUSED"
    if paused_path.is_file():
        try:
            paused_note = paused_path.read_text(encoding="utf-8").strip()
        except OSError:
            paused_note = "PAUSED"
    return {
        "bot": "kalshi_15m_multi",
        "mode": "paper" if config.KALSHI_PAPER_ONLY else "live",
        "paper_only": config.KALSHI_PAPER_ONLY,
        "paused": paused_note is not None,
        "paused_note": paused_note,
        "env": config.KALSHI_ENV,
        "series": list(config.KALSHI_SERIES),
        "equity_usd": primary.get("equity_usd"),
        "cash_usd": paper.get_stats(bot_id=primary_id).get("cash_usd"),
        "open_count": sum(int(b.get("open_count") or 0) for b in bots),
        "closed_count": sum(int(b.get("closed_count") or 0) for b in bots),
        "epoch": bot_config.PAPER_EPOCH_LABEL,
        "watchdog_enabled": bot_config.WATCHDOG_ENABLED,
        "watchdog_execute": bot_config.watchdog_execute_enabled(),
        "broadcast_only_trades": bot_config.BROADCAST_ONLY_TRADES,
        "enabled_bots": list(bot_config.ENABLED_BOTS),
        "max_contracts": int(bot_config.KALSHI_MAX_CONTRACTS),
        "max_notional_usd": float(bot_config.KALSHI_MAX_NOTIONAL_USD),
        "deploy_pct": float(bot_config.KALSHI_DEPLOY_PCT),
        "max_deploy_pct": float(bot_config.KALSHI_MAX_DEPLOY_PCT),
        "bankroll_usd": float(bot_config.KALSHI_BANKROLL_USD),
        "macro_enabled": bool(bot_config.MACRO_CONTEXT_ENABLED),
        "primary_bot": primary_id,
    }


def get_performance_payload(*, bot_id: str | None = None) -> dict[str, Any]:
    bid = bot_id or (
        bot_config.ENABLED_BOTS[0] if bot_config.ENABLED_BOTS else "control"
    )
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
        "decision_count": paper.count_decisions(bot_id=bid),
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
    market_agree = row.get("market_agree")
    if market_agree is not None:
        market_agree = bool(int(market_agree))
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
        "conviction": row.get("conviction"),
        "market_agree": market_agree,
        "deploy_pct": row.get("deploy_pct"),
        "adverse_boost": row.get("adverse_boost"),
        "side_source": row.get("side_source"),
        "structure_chart_url": _chart_url(row.get("structure_chart_path")),
        "entry_chart_url": _chart_url(
            row.get("entry_chart_path") or row.get("chart_path")
        ),
    }


def get_journal_payload(
    *,
    limit: int = 50,
    offset: int = 0,
    filter_mode: str = "all",
    bot_id: str | None = None,
) -> dict[str, Any]:
    mode = (filter_mode or "all").lower()
    total = paper.count_decisions(bot_id=bot_id, filter_mode=mode)
    rows = paper.get_decisions(
        limit=limit, offset=offset, bot_id=bot_id, filter_mode=mode
    )
    items = [enrich_decision(r) for r in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }

def _default_bot_tab(bots: list[dict[str, Any]]) -> str:
    enabled = list(bot_config.ENABLED_BOTS)
    if enabled:
        return enabled[0]
    if not bots:
        return "control"
    return bots[0]["bot_id"]


def list_paper_archives() -> list[dict[str, Any]]:
    """List archived paper epochs under archives/ (newest first)."""
    root = config.ROOT_DIR / "archives"
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or not path.name.startswith("paper_"):
            continue
        ledger = path / "ledger.db"
        csvs = sorted(path.glob("*.csv"))
        out.append(
            {
                "id": path.name,
                "label": path.name.replace("paper_", "", 1),
                "has_ledger": ledger.is_file(),
                "csv_files": [p.name for p in csvs],
                "csv_url": (
                    f"/api/archives/{path.name}/file/{csvs[0].name}" if csvs else None
                ),
            }
        )
    return out[:20]


def dashboard_context(
    *,
    filter_mode: str = "all",
    bot_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    bots = get_bots_payload()
    enabled = list(bot_config.ENABLED_BOTS)
    active = bot_id if bot_id in enabled else _default_bot_tab(bots)
    size = max(1, min(100, int(page_size)))
    page_n = max(1, int(page))
    offset = (page_n - 1) * size
    journal = get_journal_payload(
        filter_mode=filter_mode, bot_id=active, limit=size, offset=offset
    )
    total = int(journal["total"])
    total_pages = max(1, (total + size - 1) // size)
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
        "journal": journal["items"],
        "journal_filter": filter_mode,
        "journal_page": page_n,
        "journal_page_size": size,
        "journal_total": total,
        "journal_total_pages": total_pages,
        "journal_has_prev": page_n > 1,
        "journal_has_next": page_n < total_pages,
        "archives": list_paper_archives(),
    }
