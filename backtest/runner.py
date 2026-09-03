"""Backtest replay loop."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import paper
from backtest import archive as bt_archive
from backtest.context_builder import build_context
from backtest.data import (
    WindowSpec,
    build_windows,
    load_m5,
    settle_result_spot_vs_strike,
    spot_at,
)
from backtest.metrics import summarize_bot, write_summary_csv, write_trades_csv
from backtest.strategies_ext import (
    htf_from_record,
    resolve_strategies,
    rule_htf_from_returns,
)
from models import KalshiSuggestion

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    bots: list[str]
    summaries: list[dict[str, Any]] = field(default_factory=list)
    windows: int = 0
    ticks: int = 0
    decisions: int = 0
    db_path: Path | None = None
    summary_csv: Path | None = None
    trades_csv: Path | None = None


def _apply(sug: KalshiSuggestion) -> KalshiSuggestion:
    """Paper-only apply (no Telegram, no live Kalshi orders)."""
    sug.bot_id = sug.bot_id or "control"
    if sug.pending_limit and sug.side in ("YES", "NO"):
        order = paper.place_limit_order(sug, subtype=sug.trigger_name)
        if order:
            sug.order_id = int(order["id"])
        paper.log_decision(sug)
        return sug
    if sug.is_trade() and not sug.pending_limit:
        opened = paper.open_trade(sug)
        if opened:
            sug.opened = True
            sug.position_id = int(opened["id"])
        paper.log_decision(sug)
        return sug
    paper.log_decision(sug)
    return sug


def _near_decision(now: datetime, window: WindowSpec, offset_sec: int = 30) -> bool:
    target = window.open_ts + timedelta(seconds=offset_sec)
    return abs((now - target).total_seconds()) <= 90


def run_backtest(
    *,
    product: str = "BTC",
    days: float = 7.0,
    bots: list[str] | None = None,
    db_path: Path | str | None = None,
    archive_path: Path | str | None = None,
    bias_path: Path | str | None = None,
    exports_dir: Path | str | None = None,
    starting_usd: float | None = None,
    end: datetime | None = None,
    bars: list[dict[str, Any]] | None = None,
) -> BacktestResult:
    """Replay M5 history through enabled strategies into an isolated paper DB."""
    bot_ids = list(bots or ["lottery"])
    product = product.upper()
    end = end or datetime.now(timezone.utc)

    if bars is None:
        bars = load_m5(product, days=days, end=end)
    windows = build_windows(bars, product_id=product)
    if not windows:
        raise RuntimeError(f"No 15m windows built for {product} ({len(bars)} bars)")

    tmp_owned = False
    if db_path is None:
        tmp = tempfile.NamedTemporaryFile(prefix="bt_ledger_", suffix=".db", delete=False)
        db_path = Path(tmp.name)
        tmp.close()
        tmp_owned = True
    else:
        db_path = Path(db_path)
        if db_path.exists():
            db_path.unlink()

    arch = Path(archive_path) if archive_path else None
    biases = (
        bt_archive.load_recorded_biases(Path(bias_path))
        if bias_path
        else {}
    )
    strategies = resolve_strategies(bot_ids)
    if not strategies:
        raise ValueError(f"No strategies resolved for bots={bot_ids}")

    result = BacktestResult(bots=bot_ids, windows=len(windows), db_path=db_path)

    with paper.use_db(db_path):
        paper.init_db()
        for bid in bot_ids:
            paper.reset_book(
                float(starting_usd) if starting_usd is not None else None,
                bot_id=bid,
            )

        for window in windows:
            # Tick every 5 minutes from open to expiry (inclusive open, exclusive expiry end settle).
            t = window.open_ts
            while t < window.expiry_ts:
                paper.set_now(t)
                near = _near_decision(t, window)
                rec = biases.get(window.market_ticker)
                htf = htf_from_record(rec) if rec else None

                archived_mid = (
                    bt_archive.mid_as_of(arch, window.market_ticker, t)
                    if arch
                    else None
                )
                # Provisional context for rule HTF if needed.
                ctx = build_context(
                    window,
                    bars,
                    now=t,
                    htf=htf,
                    near_decision=near,
                    yes_mid_override=archived_mid,
                )
                if ctx is None:
                    t += timedelta(minutes=5)
                    continue

                if htf is None and any(
                    getattr(s, "needs_htf_bias", False) for s in strategies
                ):
                    htf = rule_htf_from_returns(ctx.prior_1h_ret)
                    ctx.htf = htf

                if htf is not None:
                    paper.set_shared_htf_bias(
                        window.market_ticker,
                        {
                            "side": htf.side,
                            "htf_bias": htf.htf_bias,
                            "ict_bias": htf.ict_bias,
                            "ict_action": htf.ict_action,
                            "source": "backtest",
                            "cycle_id": ctx.cycle_id,
                        },
                    )

                yes_mids = {
                    window.market_ticker: float(ctx.yes_mid_cents or 50.0)
                }
                paper.process_pending_orders(yes_mids=yes_mids)

                for strat in strategies:
                    try:
                        sug = strat.decide(ctx)
                    except Exception:
                        logger.exception(
                            "Strategy %s failed at %s %s",
                            strat.bot_id,
                            window.market_ticker,
                            t,
                        )
                        continue
                    if sug is None:
                        continue
                    sug.bot_id = strat.bot_id
                    _apply(sug)
                    result.decisions += 1

                result.ticks += 1
                t += timedelta(minutes=5)

            # Settle at expiry.
            paper.set_now(window.expiry_ts)
            settle_spot = spot_at(bars, window.expiry_ts)
            result_s = None
            if arch:
                result_s = bt_archive.result_for(arch, window.market_ticker)
            if result_s is None:
                result_s = settle_result_spot_vs_strike(settle_spot, window.strike)
            if result_s:
                for bot_id in bot_ids:
                    if paper.has_open_for_market(
                        window.market_ticker, bot_id=bot_id
                    ):
                        paper.settle_position(
                            window.market_ticker,
                            result_s,
                            bot_id=bot_id,
                        )
                paper.cancel_pending_for_market(window.market_ticker)
                for bot_id in bot_ids:
                    paper.clear_window_arm(bot_id, window.market_ticker)

        paper.set_now(None)
        result.summaries = [summarize_bot(b) for b in bot_ids]

        exports = Path(exports_dir) if exports_dir else Path("exports")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result.summary_csv = write_summary_csv(
            result.summaries,
            exports / f"backtest_summary_{product}_{stamp}.csv",
        )
        result.trades_csv = write_trades_csv(
            bot_ids,
            exports / f"backtest_trades_{product}_{stamp}.csv",
        )

    # Note: tmp DB left on disk for inspection when auto-created.
    if tmp_owned:
        logger.info("Backtest ledger kept at %s", db_path)
    return result
