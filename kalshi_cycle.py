"""Kalshi 15m decision cycle — shared HTF bias, multi-strategy paper fan-out."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import bot_config
import config
import kalshi_charts
import kalshi_client
import kalshi_critic
import kalshi_fair
import kalshi_finalize
import kalshi_ict
import kalshi_triggers
import notify
import paper
import research
from models import KalshiSuggestion
from patterns import market_structure_state as mss
from strategies.context import SharedCycleContext, SharedHtfBias
from strategies.registry import any_needs_htf_bias, enabled_strategies

logger = logging.getLogger(__name__)

# Claude ICT+critic can take >60s per asset. Keep the APScheduler job slot free by
# refreshing HTF on a background thread so settle/adverse ticks are not skipped.
_htf_refresh_lock = threading.Lock()
_htf_store_lock = threading.Lock()


def _near_decision_time(now: datetime | None = None) -> bool:
    """True when within KALSHI_DECISION_WINDOW_SEC of (window_open + offset)."""
    now = now or datetime.now(timezone.utc)
    offset = int(bot_config.KALSHI_CYCLE_OFFSET_SEC)
    window = int(bot_config.KALSHI_DECISION_WINDOW_SEC)
    minute = now.minute
    window_start_min = (minute // 15) * 15
    open_dt = now.replace(minute=window_start_min, second=0, microsecond=0)
    target = open_dt.timestamp() + offset
    return abs(now.timestamp() - target) <= window


def settle_due() -> list[dict[str, Any]]:
    """Settle open paper positions (all bots) whose Kalshi market has a result."""
    paper.init_db()
    settled: list[dict[str, Any]] = []
    for pos in paper.get_open_positions():
        ticker = str(pos["market_ticker"])
        bot_id = str(pos.get("bot_id") or "control")
        try:
            result = kalshi_client.get_market_result(ticker)
        except Exception:
            logger.exception("Failed to fetch result for %s", ticker)
            continue
        if not result:
            continue
        closed = paper.settle_position(
            ticker,
            result,
            bot_id=bot_id,
            position_id=int(pos["id"]),
        )
        if closed:
            logger.info(
                "Settled [%s] %s result=%s pnl=%.2f",
                bot_id,
                ticker,
                result,
                float(closed.get("pnl_usd") or 0),
            )
            try:
                from backtest.record_live import maybe_record_result

                maybe_record_result(
                    series=str(closed.get("series") or pos.get("series") or ""),
                    market_ticker=ticker,
                    product_id=str(closed.get("product_id") or pos.get("product_id") or "")
                    or None,
                    result=str(result),
                )
            except Exception:
                logger.exception("Result archive record failed for %s", ticker)
            try:
                notify.broadcast_settle(closed)
            except Exception:
                logger.exception("Settle notify failed for %s", ticker)
            settled.append(closed)
            paper.clear_window_arm(bot_id, ticker)
    return settled


def _contract_price_cents(side: str, yes_mid_cents: float) -> float:
    mid = float(yes_mid_cents)
    if side.upper() == "YES":
        return max(1.0, min(99.0, mid))
    return max(1.0, min(99.0, 100.0 - mid))


def _bankroll_usd() -> float:
    import kalshi_sizing

    return kalshi_sizing.sizing_bankroll_usd()


def size_contracts(side: str, yes_mid_cents: float) -> tuple[int, float, float]:
    import kalshi_sizing

    entry_cents = _contract_price_cents(side, yes_mid_cents)
    contracts, budget = kalshi_sizing.contracts_for_entry(entry_cents)
    return contracts, entry_cents, budget


def _strike_from_market(market: dict[str, Any]) -> float | None:
    raw = market.get("floor_strike")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _base_kwargs(
    *,
    series: str,
    ticker: str,
    product_id: str,
    mid: float | None,
    fair: float | None,
    edge: float | None,
    expiry: str | None,
    features: dict[str, Any],
) -> dict[str, Any]:
    return {
        "series": series,
        "market_ticker": ticker,
        "product_id": product_id,
        "mid_cents": mid,
        "fair_yes_cents": fair,
        "edge_cents": edge,
        "expiry_ts": expiry,
        "spot": features.get("spot"),
        "strike": features.get("strike"),
        "spot_vs_strike_pct": features.get("spot_vs_strike_pct"),
        "tau_sec": features.get("tau_sec"),
        "sigma": features.get("sigma"),
        "prior_5m_ret": features.get("prior_5m_ret"),
        "prior_15m_ret": features.get("prior_15m_ret"),
        "prior_1h_ret": features.get("prior_1h_ret"),
        "cycle_id": features.get("cycle_id"),
    }


def _build_features(
    coinbase: str,
    market: dict[str, Any],
    expiry: str | None,
    cycle_id: str,
) -> dict[str, Any]:
    strike = _strike_from_market(market)
    try:
        spot = float(research.get_live_spot_price(product_id=coinbase))
    except Exception:
        logger.exception("Live spot failed for %s — trying M5 close", coinbase)
        bars = research.get_ohlc("M5", limit=13, product_id=coinbase)
        spot = float(bars[-1]["close"]) if bars else None

    m5 = research.get_ohlc("M5", limit=20, product_id=coinbase)
    sigma = kalshi_fair.m5_log_return_sigma(m5, lookback=12)
    tau = kalshi_fair.tau_seconds(expiry)
    prior_5m = kalshi_fair.prior_return_pct(m5, 1)
    prior_15m = kalshi_fair.prior_return_pct(m5, 3)
    prior_1h = kalshi_fair.prior_return_pct(m5, 12)

    fair_res = None
    if spot is not None and strike is not None and strike > 0:
        try:
            fair_res = kalshi_fair.fair_yes_cents(spot, strike, tau, sigma)
        except ValueError:
            logger.exception("Fair value failed spot=%s strike=%s", spot, strike)

    return {
        "cycle_id": cycle_id,
        "spot": spot,
        "strike": strike,
        "sigma": sigma,
        "tau_sec": tau,
        "prior_5m_ret": prior_5m,
        "prior_15m_ret": prior_15m,
        "prior_1h_ret": prior_1h,
        "m5_bars": m5,
        "fair": fair_res,
        "spot_vs_strike_pct": (
            fair_res.spot_vs_strike_pct
            if fair_res
            else (
                ((spot / strike) - 1.0) * 100.0
                if spot and strike and strike > 0
                else None
            )
        ),
    }


def _chart_read_score(refine: Any) -> float | None:
    if refine is None:
        return None
    findings = getattr(refine, "final_findings", None) or []
    if not findings:
        return 1.0
    critical = sum(1 for f in findings if getattr(f, "severity", "") == "critical")
    soft = sum(1 for f in findings if getattr(f, "severity", "") != "critical")
    return max(0.0, 1.0 - 0.35 * critical - 0.1 * soft)


def _load_htf_from_payload(payload: dict[str, Any] | None) -> SharedHtfBias | None:
    if not payload:
        return None
    side = payload.get("side")
    if side not in ("YES", "NO", None):
        side = None
    return SharedHtfBias(
        ict_action=str(payload.get("ict_action") or "no_trade"),
        ict_bias=str(payload.get("ict_bias") or "unknown"),
        ict_rationale=str(payload.get("ict_rationale") or ""),
        gate_outcome=payload.get("gate_outcome"),
        htf_bias=str(payload.get("htf_bias") or "unknown"),
        setup_tags=list(payload.get("setup_tags") or []),
        critic_downgraded=bool(payload.get("critic_downgraded")),
        critic_passes=int(payload.get("critic_passes") or 0),
        critic_findings=list(payload.get("critic_findings") or []),
        chart_read_score=payload.get("chart_read_score"),
        ob_low=payload.get("ob_low"),
        ob_high=payload.get("ob_high"),
        structure_chart_path=payload.get("structure_chart_path"),
        entry_chart_path=payload.get("entry_chart_path"),
        side=side if side in ("YES", "NO") else None,
    )


def _load_htf_from_store(ticker: str) -> SharedHtfBias | None:
    return _load_htf_from_payload(paper.get_shared_htf_bias(ticker))


def _load_htf_reuse(
    ticker: str,
    product_id: str,
    *,
    mirror_to_ticker: bool = False,
) -> SharedHtfBias | None:
    """Prefer per-ticker bias; fall back to product-level store."""
    payload = paper.get_shared_htf_bias(ticker)
    if payload:
        return _load_htf_from_payload(payload)
    product_payload = paper.get_product_htf_bias(product_id)
    if not product_payload:
        return None
    if mirror_to_ticker and ticker:
        paper.set_shared_htf_bias(ticker, dict(product_payload))
    return _load_htf_from_payload(product_payload)


def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _h1_closed_since(refreshed_at: datetime, now: datetime) -> bool:
    """True if at least one H1 bar has closed after refreshed_at."""
    last_h1_close = now.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    return refreshed_at < last_h1_close


def _should_refresh_htf(
    *,
    ticker: str,
    product_id: str,
    spot: float | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Decide whether to call Claude for ICT bias. Never per-bot bypass."""
    mode = str(getattr(bot_config, "HTF_REFRESH_MODE", "once_per_window") or "").lower()
    now = now or datetime.now(timezone.utc)

    if mode == "every_near_tick":
        return True, "forced"

    if mode == "once_per_window":
        if paper.get_shared_htf_bias(ticker):
            return False, "reuse"
        return True, "window_first"

    if mode == "ttl_event":
        payload = paper.get_product_htf_bias(product_id)
        if not payload:
            if paper.get_shared_htf_bias(ticker):
                return False, "reuse"
            return True, "window_first"

        refreshed = _parse_iso_ts(payload.get("refreshed_at"))
        ttl = max(0, int(getattr(bot_config, "HTF_BIAS_TTL_SEC", 3600) or 3600))
        if refreshed is None:
            return True, "ttl"
        age_sec = (now - refreshed.astimezone(timezone.utc)).total_seconds()
        if age_sec >= ttl:
            return True, "ttl"

        move_pct = float(getattr(bot_config, "HTF_M5_MOVE_PCT", 0.20) or 0.20)
        spot_at = payload.get("spot_at_refresh")
        if (
            spot is not None
            and spot_at is not None
            and float(spot_at) > 0
            and move_pct > 0
        ):
            moved = abs(float(spot) / float(spot_at) - 1.0) * 100.0
            if moved >= move_pct:
                return True, "m5_move"

        if bool(getattr(bot_config, "HTF_REFRESH_ON_H1_CLOSE", True)):
            if _h1_closed_since(refreshed.astimezone(timezone.utc), now):
                return True, "h1_close"

        return False, "reuse"

    logger.warning("Unknown HTF_REFRESH_MODE=%r — using once_per_window", mode)
    if paper.get_shared_htf_bias(ticker):
        return False, "reuse"
    return True, "window_first"


def _compute_htf_bias(
    coinbase: str,
    ticker: str,
    cycle_id: str,
    mid: float,
    fair_cents: float | None,
    *,
    product_id: str | None = None,
    spot: float | None = None,
    strike: float | None = None,
) -> SharedHtfBias | None:
    try:
        ict, snapshot, refine = kalshi_ict.propose_ict_bias(
            coinbase,
            cycle_id=cycle_id,
            model_fair_yes_cents=fair_cents,
            yes_mid_cents=float(mid),
        )
    except Exception:
        logger.exception("ICT bias failed for %s", coinbase)
        return None

    ctx = snapshot.get("market_context")
    marked = snapshot.get("marked_paths") or {}
    htf_bias = kalshi_triggers.htf_bias_from_context(ctx)
    structure_state = mss.refresh_from_context(
        ctx,
        product_id=coinbase,
        market_ticker=ticker,
        marked_paths=marked,
        htf_bias=htf_bias,
    )
    bias = kalshi_ict.ict_bias_label(ict.action)
    gate_outcome = snapshot.get("gate_outcome") or kalshi_ict.evaluate_gate_outcome(
        ict, ctx
    )
    ict_rationale = (ict.rationale or "").strip() or "no ICT rationale"
    critic_downgraded = bool(refine.downgraded) if refine else False
    critic_passes = int(refine.passes_used) if refine else 0
    critic_findings = (
        kalshi_critic.findings_to_json(refine.final_findings) if refine else []
    )
    chart_score = _chart_read_score(refine)
    ob = ict.order_block or {}
    ob_low = float(ob["low"]) if ob.get("low") is not None else None
    ob_high = float(ob["high"]) if ob.get("high") is not None else None
    setup_tags = list(ctx.setup_tags if ctx else []) + list(
        structure_state.setup_tags or []
    )
    setup_tags = list(dict.fromkeys(setup_tags))
    struct_path = marked.get("H4") or structure_state.structure_chart_path
    entry_path = marked.get("M5") or marked.get("H1") or structure_state.entry_chart_path
    side = None if critic_downgraded else kalshi_ict.ict_action_to_side(ict.action)

    if spot is None and ctx is not None and getattr(ctx, "spot", None) is not None:
        try:
            spot = float(ctx.spot)
        except (TypeError, ValueError):
            spot = None

    refreshed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pid = product_id or (coinbase.split("-")[0] if coinbase else "UNKNOWN")

    payload = {
        "side": side,
        "yes_mid": mid,
        "spot": spot,
        "strike": strike,
        "spot_at_refresh": spot,
        "refreshed_at": refreshed_at,
        "source_ticker": ticker,
        "cycle_id": cycle_id,
        "product_id": pid,
        "ict_action": ict.action,
        "ict_bias": bias,
        "ict_rationale": ict_rationale,
        "gate_outcome": gate_outcome,
        "htf_bias": htf_bias,
        "setup_tags": setup_tags,
        "critic_downgraded": critic_downgraded,
        "critic_passes": critic_passes,
        "critic_findings": critic_findings,
        "chart_read_score": chart_score,
        "ob_low": ob_low,
        "ob_high": ob_high,
        "structure_chart_path": struct_path,
        "entry_chart_path": entry_path,
    }

    htf = SharedHtfBias(
        ict_action=ict.action,
        ict_bias=bias,
        ict_rationale=ict_rationale,
        gate_outcome=gate_outcome,
        htf_bias=htf_bias,
        setup_tags=setup_tags,
        critic_downgraded=critic_downgraded,
        critic_passes=critic_passes,
        critic_findings=critic_findings,
        chart_read_score=chart_score,
        ob_low=ob_low,
        ob_high=ob_high,
        structure_chart_path=struct_path,
        entry_chart_path=entry_path,
        side=side,
    )
    with _htf_store_lock:
        paper.set_shared_htf_bias(ticker, payload)
        paper.set_product_htf_bias(pid, payload)
    return htf


def _open_market_for_series(series: str) -> dict[str, Any] | None:
    try:
        markets = kalshi_client.get_open_markets(series)
    except Exception:
        logger.exception("Failed to list markets for %s", series)
        return None
    if not markets:
        return None
    return markets[0]


def _htf_refresh_needed_for_open_markets() -> bool:
    """True if any open series still needs a Claude HTF refresh under current mode."""
    if not any_needs_htf_bias():
        return False
    for series in config.KALSHI_SERIES:
        market = _open_market_for_series(series)
        if not market:
            continue
        ticker = str(market.get("ticker") or "")
        product_id = bot_config.series_product(series)
        should, _reason = _should_refresh_htf(
            ticker=ticker,
            product_id=product_id,
            spot=None,
        )
        if should:
            return True
    return False


def _refresh_htf_for_series(series: str, market: dict[str, Any]) -> None:
    build_shared_context(
        series,
        market,
        near_decision=True,
        force_htf=True,
        allow_htf_refresh=True,
    )


def _htf_refresh_worker() -> None:
    """Compute Claude HTF off the scheduler thread, then re-run strategies."""
    logger.info("HTF background refresh starting")
    pairs: list[tuple[str, dict[str, Any]]] = []
    for series in config.KALSHI_SERIES:
        market = _open_market_for_series(series)
        if market is None:
            continue
        ticker = str(market.get("ticker") or "")
        product_id = bot_config.series_product(series)
        should, reason = _should_refresh_htf(
            ticker=ticker, product_id=product_id, spot=None
        )
        logger.info(
            "htf_bg_plan reason=%s ticker=%s product=%s",
            reason,
            ticker,
            product_id,
        )
        if should:
            pairs.append((series, market))

    if not pairs:
        logger.info("HTF background refresh: nothing to compute")
        return

    if len(pairs) == 1:
        _refresh_htf_for_series(pairs[0][0], pairs[0][1])
    else:
        # Parallel BTC/ETH Claude calls — wall clock ~half of sequential.
        with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
            futs = {
                pool.submit(_refresh_htf_for_series, series, market): series
                for series, market in pairs
            }
            for fut in as_completed(futs):
                series = futs[fut]
                try:
                    fut.result()
                except Exception:
                    logger.exception("HTF background refresh failed for %s", series)

    # Adverse can arm/fill off-offset once bias is stored; control only if still near.
    near = _near_decision_time()
    decided = run_strategy_cycle(
        near_decision=near,
        force_htf=False,
        allow_htf_refresh=False,
    )
    logger.info(
        "HTF background refresh done (near=%s decisions=%s)",
        near,
        len(decided),
    )


def ensure_htf_refresh_started() -> bool:
    """Kick a daemon thread for Claude HTF if needed. Never blocks the caller."""
    if not _htf_refresh_needed_for_open_markets():
        return False
    if not _htf_refresh_lock.acquire(blocking=False):
        logger.info("HTF background refresh already running — light tick continues")
        return False

    def _run() -> None:
        try:
            _htf_refresh_worker()
        except Exception:
            logger.exception("HTF background refresh crashed")
        finally:
            _htf_refresh_lock.release()

    threading.Thread(target=_run, name="htf-refresh", daemon=True).start()
    logger.info("HTF background refresh kicked off")
    return True


def build_shared_context(
    series: str,
    market: dict[str, Any],
    *,
    near_decision: bool,
    force_htf: bool = False,
    allow_htf_refresh: bool = True,
) -> SharedCycleContext:
    product_id = bot_config.series_product(series)
    coinbase = bot_config.PRODUCT_TO_COINBASE.get(product_id, f"{product_id}-USD")
    ticker = str(market.get("ticker") or "")
    expiry = (
        market.get("close_time")
        or market.get("expected_expiration_time")
        or market.get("expiration_time")
    )
    expiry_s = str(expiry) if expiry else None
    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    mid = kalshi_client.mid_cents_from_market(market)
    if mid is None:
        mid = kalshi_client.get_orderbook_mid(ticker)

    features = _build_features(coinbase, market, expiry_s, cycle_id)
    fair_res = features.get("fair")
    fair_cents = fair_res.fair_yes_cents if fair_res else None
    edge = (
        fair_res.edge_cents(float(mid))
        if fair_res is not None and mid is not None
        else None
    )
    base = _base_kwargs(
        series=series,
        ticker=ticker,
        product_id=product_id,
        mid=mid,
        fair=fair_cents,
        edge=edge,
        expiry=expiry_s,
        features=features,
    )

    htf: SharedHtfBias | None = None
    need_htf = any_needs_htf_bias()
    spot = features.get("spot")
    strike = features.get("strike")
    if not need_htf:
        if near_decision or force_htf:
            logger.info(
                "htf_refresh_reason=skipped_no_consumer ticker=%s product=%s",
                ticker,
                product_id,
            )
    elif mid is not None and (near_decision or force_htf):
        should, reason = _should_refresh_htf(
            ticker=ticker,
            product_id=product_id,
            spot=float(spot) if spot is not None else None,
        )
        if should and not allow_htf_refresh:
            logger.info(
                "htf_refresh_reason=deferred ticker=%s product=%s mode=%s "
                "(would have been %s)",
                ticker,
                product_id,
                bot_config.HTF_REFRESH_MODE,
                reason,
            )
            htf = _load_htf_reuse(ticker, product_id, mirror_to_ticker=True)
        else:
            logger.info(
                "htf_refresh_reason=%s ticker=%s product=%s mode=%s",
                reason,
                ticker,
                product_id,
                bot_config.HTF_REFRESH_MODE,
            )
            if should:
                htf = _compute_htf_bias(
                    coinbase,
                    ticker,
                    cycle_id,
                    float(mid),
                    fair_cents,
                    product_id=product_id,
                    spot=float(spot) if spot is not None else None,
                    strike=float(strike) if strike is not None else None,
                )
            else:
                htf = _load_htf_reuse(ticker, product_id, mirror_to_ticker=True)
    elif need_htf:
        htf = _load_htf_reuse(ticker, product_id, mirror_to_ticker=False)

    ctx = SharedCycleContext(
        series=series,
        market=market,
        market_ticker=ticker,
        product_id=product_id,
        coinbase=coinbase,
        cycle_id=cycle_id,
        expiry_ts=expiry_s,
        yes_mid_cents=float(mid) if mid is not None else None,
        spot=features.get("spot"),
        strike=features.get("strike"),
        sigma=features.get("sigma"),
        tau_sec=features.get("tau_sec"),
        spot_vs_strike_pct=features.get("spot_vs_strike_pct"),
        prior_5m_ret=features.get("prior_5m_ret"),
        prior_15m_ret=features.get("prior_15m_ret"),
        prior_1h_ret=features.get("prior_1h_ret"),
        fair_yes_cents=fair_cents,
        edge_cents=edge,
        m5_bars=list(features.get("m5_bars") or []),
        htf=htf,
        near_decision=near_decision,
        base_kwargs=base,
    )
    try:
        from backtest.record_live import maybe_record_from_context

        maybe_record_from_context(ctx)
    except Exception:
        logger.exception("Snapshot archive record failed for %s", ticker)
    return ctx


def _decide_for_market(series: str, market: dict[str, Any]) -> KalshiSuggestion:
    """Back-compat: control-only decision (vision path)."""
    ctx = build_shared_context(series, market, near_decision=True, force_htf=True)
    from strategies.control import ControlStrategy

    sug = ControlStrategy().decide(ctx)
    if sug is None:
        return kalshi_finalize.make_skip(
            rationale="control produced no decision",
            base=ctx.with_bot("control"),
            skip_codes=["no_decision"],
        )
    return sug


def _notify_decision(
    suggestion: KalshiSuggestion,
    *,
    market: dict[str, Any] | None = None,
    opened: bool = False,
) -> str | None:
    if (
        bot_config.BROADCAST_ONLY_TRADES
        and not suggestion.is_trade()
        and not opened
    ):
        logger.info(
            "Skip broadcast (BROADCAST_ONLY_TRADES): [%s] %s",
            suggestion.bot_id,
            suggestion.rationale[:120],
        )
        return None
    strike = suggestion.strike or _strike_from_market(market or {})
    chart_path = suggestion.entry_chart_path or suggestion.chart_path
    structure_path = suggestion.structure_chart_path
    try:
        built = kalshi_charts.build_decision_chart(
            suggestion,
            strike=strike,
            position_id=suggestion.position_id,
        )
        if built:
            chart_path = chart_path or built
            suggestion.chart_path = chart_path
    except Exception:
        logger.exception("Chart build failed for %s", suggestion.product_id)
    try:
        # Prefix bot id in rationale for Telegram clarity.
        if suggestion.bot_id and suggestion.bot_id != "control":
            if not (suggestion.rationale or "").startswith(f"[{suggestion.bot_id}]"):
                suggestion.rationale = (
                    f"[{suggestion.bot_id}] {suggestion.rationale}"
                )
        notify.broadcast_decision(
            suggestion,
            chart_path=chart_path,
            structure_chart_path=structure_path,
            opened=opened,
        )
    except Exception:
        logger.exception("Decision notify failed for %s", suggestion.market_ticker)
    return chart_path


def apply_and_log(
    suggestion: KalshiSuggestion,
    *,
    market: dict[str, Any] | None = None,
) -> KalshiSuggestion:
    """Place/paper-open (or pending limit) if trade; always log + notify."""
    suggestion.bot_id = suggestion.bot_id or "control"

    if suggestion.pending_limit and suggestion.side in ("YES", "NO"):
        order = paper.place_limit_order(
            suggestion,
            subtype=suggestion.trigger_name,
        )
        if order:
            suggestion.order_id = int(order["id"])
            suggestion.opened = False
            paper.log_decision(suggestion)
            _notify_decision(suggestion, market=market, opened=False)
            logger.info(
                "Pending limit [%s] %s %s x%s @ %.1f¢ cancel_at=%s",
                suggestion.bot_id,
                suggestion.product_id,
                suggestion.side,
                suggestion.contracts,
                suggestion.entry_cents or 0,
                suggestion.cancel_at_ts,
            )
            return suggestion
        suggestion = kalshi_finalize.make_skip(
            rationale=(
                f"limit order not parked (duplicate/pending/open). "
                f"Original: {suggestion.rationale}"
            ),
            base={
                "series": suggestion.series,
                "market_ticker": suggestion.market_ticker,
                "product_id": suggestion.product_id,
                "mid_cents": suggestion.mid_cents,
                "fair_yes_cents": suggestion.fair_yes_cents,
                "edge_cents": suggestion.edge_cents,
                "expiry_ts": suggestion.expiry_ts,
                "spot": suggestion.spot,
                "strike": suggestion.strike,
                "cycle_id": suggestion.cycle_id,
                "bot_id": suggestion.bot_id,
            },
            skip_codes=["limit_not_parked"],
            trigger_type=suggestion.trigger_type or "lottery_ticket",
        )
        suggestion.bot_id = suggestion.bot_id or "control"

    if suggestion.is_trade() and not suggestion.pending_limit:
        import kalshi_sizing

        entry = float(suggestion.entry_cents or 0)
        suggestion.contracts = kalshi_sizing.clamp_contracts(
            int(suggestion.contracts or 0), entry
        )
        if suggestion.contracts < 1:
            suggestion = kalshi_finalize.make_skip(
                rationale=(
                    f"signal was {suggestion.side} but size clamped to 0 "
                    f"(max {bot_config.KALSHI_MAX_CONTRACTS} ct / "
                    f"${float(bot_config.KALSHI_MAX_NOTIONAL_USD):.2f} notional). "
                    f"Original why: {suggestion.rationale}"
                ),
                base={
                    "series": suggestion.series,
                    "market_ticker": suggestion.market_ticker,
                    "product_id": suggestion.product_id,
                    "mid_cents": suggestion.mid_cents,
                    "fair_yes_cents": suggestion.fair_yes_cents,
                    "edge_cents": suggestion.edge_cents,
                    "expiry_ts": suggestion.expiry_ts,
                    "spot": suggestion.spot,
                    "strike": suggestion.strike,
                    "cycle_id": suggestion.cycle_id,
                    "bot_id": suggestion.bot_id,
                },
                skip_codes=["size_clamped_zero"],
                trigger_type=suggestion.trigger_type or "none",
            )
            suggestion.bot_id = suggestion.bot_id or "control"
        else:
            try:
                kalshi_sizing.assert_order_allowed(suggestion.contracts, entry)
                order_resp = kalshi_client.place_order(
                    suggestion.market_ticker,
                    suggestion.side,
                    suggestion.contracts,
                    yes_price_cents=int(round(entry)),
                )
            except Exception:
                logger.exception(
                    "Live/paper order failed [%s] %s — not opening shadow position",
                    suggestion.bot_id,
                    suggestion.market_ticker,
                )
                suggestion = kalshi_finalize.make_skip(
                    rationale=(
                        f"signal was {suggestion.side} but order rejected/failed. "
                        f"Original why: {suggestion.rationale}"
                    ),
                    base={
                        "series": suggestion.series,
                        "market_ticker": suggestion.market_ticker,
                        "product_id": suggestion.product_id,
                        "mid_cents": suggestion.mid_cents,
                        "fair_yes_cents": suggestion.fair_yes_cents,
                        "edge_cents": suggestion.edge_cents,
                        "expiry_ts": suggestion.expiry_ts,
                        "spot": suggestion.spot,
                        "strike": suggestion.strike,
                        "cycle_id": suggestion.cycle_id,
                        "bot_id": suggestion.bot_id,
                    },
                    skip_codes=["order_failed"],
                    trigger_type=suggestion.trigger_type or "none",
                )
                suggestion.bot_id = suggestion.bot_id or "control"
                paper.log_decision(suggestion)
                _notify_decision(suggestion, market=market, opened=False)
                return suggestion

            if isinstance(order_resp, dict) and (
                order_resp.get("error") or order_resp.get("status") == "rejected"
            ):
                logger.warning("Order error response: %s", order_resp)
                err = order_resp.get("error") or "rejected"
                suggestion = kalshi_finalize.make_skip(
                    rationale=f"order error: {err}",
                    base={
                        "series": suggestion.series,
                        "market_ticker": suggestion.market_ticker,
                        "product_id": suggestion.product_id,
                        "mid_cents": suggestion.mid_cents,
                        "fair_yes_cents": suggestion.fair_yes_cents,
                        "edge_cents": suggestion.edge_cents,
                        "expiry_ts": suggestion.expiry_ts,
                        "cycle_id": suggestion.cycle_id,
                        "bot_id": suggestion.bot_id,
                    },
                    skip_codes=["order_error"],
                )
                paper.log_decision(suggestion)
                _notify_decision(suggestion, market=market, opened=False)
                return suggestion

            opened = paper.open_trade(suggestion)
            if opened:
                suggestion.opened = True
                suggestion.position_id = int(opened["id"])
                chart_path = _notify_decision(
                    suggestion, market=market, opened=True
                )
                if chart_path:
                    suggestion.chart_path = chart_path
                    paper.set_position_chart_path(suggestion.position_id, chart_path)
                paper.log_decision(suggestion)
                logger.info(
                    "Opened [%s] %s %s x%s @ %.1f¢ fair=%.1f edge=%+.1f gate=%s paper_only=%s",
                    suggestion.bot_id,
                    suggestion.product_id,
                    suggestion.side,
                    suggestion.contracts,
                    suggestion.entry_cents or 0,
                    suggestion.fair_yes_cents or 0,
                    suggestion.edge_cents or 0,
                    suggestion.gate_outcome,
                    config.KALSHI_PAPER_ONLY,
                )
                return suggestion
            logger.warning(
                "Shadow open failed [%s] for %s (order may still be live)",
                suggestion.bot_id,
                suggestion.market_ticker,
            )
            suggestion = kalshi_finalize.make_skip(
                rationale=(
                    f"signal was {suggestion.side} but paper open failed "
                    f"(cash or duplicate). Original why: {suggestion.rationale}"
                ),
                base={
                    "series": suggestion.series,
                    "market_ticker": suggestion.market_ticker,
                    "product_id": suggestion.product_id,
                    "mid_cents": suggestion.mid_cents,
                    "fair_yes_cents": suggestion.fair_yes_cents,
                    "edge_cents": suggestion.edge_cents,
                    "expiry_ts": suggestion.expiry_ts,
                    "spot": suggestion.spot,
                    "strike": suggestion.strike,
                    "cycle_id": suggestion.cycle_id,
                    "bot_id": suggestion.bot_id,
                },
                htf_bias=suggestion.h1_bias_tag or "unknown",
                setup_tags=list(suggestion.setup_tags or []),
                skip_codes=["paper_open_failed"],
                structure_chart_path=suggestion.structure_chart_path,
                entry_chart_path=suggestion.entry_chart_path,
                ict_action=suggestion.ict_action,
                ict_bias=suggestion.ict_bias,
                trigger_type=suggestion.trigger_type or "none",
            )
        suggestion.bot_id = suggestion.bot_id or "control"
    else:
        logger.info(
            "[%s] %s %s",
            suggestion.bot_id,
            suggestion.series,
            suggestion.rationale[:160],
        )
    paper.log_decision(suggestion)
    _notify_decision(suggestion, market=market, opened=False)
    return suggestion


def run_strategy_cycle(
    *,
    near_decision: bool,
    force_htf: bool = False,
    allow_htf_refresh: bool = True,
) -> list[KalshiSuggestion]:
    """Build shared context per series and fan out to all enabled strategies."""
    results: list[KalshiSuggestion] = []
    yes_mids: dict[str, float] = {}

    for series in config.KALSHI_SERIES:
        try:
            markets = kalshi_client.get_open_markets(series)
        except Exception:
            logger.exception("Failed to list markets for %s", series)
            skip = kalshi_finalize.make_skip(
                rationale=f"skipped: failed to list open markets for {series}",
                base={
                    "series": series,
                    "market_ticker": "",
                    "product_id": bot_config.series_product(series),
                    "mid_cents": None,
                    "fair_yes_cents": None,
                    "edge_cents": None,
                    "expiry_ts": None,
                    "cycle_id": None,
                    "bot_id": "control",
                },
                skip_codes=["list_markets_failed"],
            )
            results.append(apply_and_log(skip))
            continue
        if not markets:
            logger.info("%s: no open markets", series)
            continue

        market = markets[0]
        ctx = build_shared_context(
            series,
            market,
            near_decision=near_decision,
            force_htf=force_htf,
            allow_htf_refresh=allow_htf_refresh,
        )
        if ctx.yes_mid_cents is not None:
            yes_mids[ctx.market_ticker] = float(ctx.yes_mid_cents)

        for strat in enabled_strategies():
            try:
                suggestion = strat.decide(ctx)
            except Exception:
                logger.exception(
                    "Strategy %s failed for %s", strat.bot_id, ctx.market_ticker
                )
                continue
            if suggestion is None:
                continue
            suggestion.bot_id = strat.bot_id
            results.append(apply_and_log(suggestion, market=market))

    # Process lottery limits against latest mids.
    try:
        paper.process_pending_orders(yes_mids=yes_mids)
    except Exception:
        logger.exception("process_pending_orders failed")

    return results


def run_decision_cycle() -> list[KalshiSuggestion]:
    """Near-offset vision cycle: refresh HTF and run all strategies."""
    return run_strategy_cycle(
        near_decision=True, force_htf=True, allow_htf_refresh=True
    )


def run_once(*, force_decision: bool = False) -> dict[str, Any]:
    """One job tick: settle, process limits, run strategies as needed.

    Scheduled ticks keep Claude off the critical path: HTF refresh runs in a
    background thread so APScheduler does not skip the next 60s settle/adverse
    tick with \"maximum number of running instances reached\".
    """
    paper.init_db()
    settled = settle_due()
    near = force_decision or _near_decision_time()

    if force_decision:
        # Explicit CLI / force path: block until Claude finishes.
        decided = run_strategy_cycle(
            near_decision=True,
            force_htf=True,
            allow_htf_refresh=True,
        )
    else:
        if near:
            ensure_htf_refresh_started()
        # Light path only — reuse stored HTF; never hold the job on vision.
        decided = run_strategy_cycle(
            near_decision=near,
            force_htf=False,
            allow_htf_refresh=False,
        )
        if not near:
            logger.info(
                "Off-offset tick — lottery/adverse only (%s decisions, %s settled)",
                len(decided),
                len(settled),
            )

    return {
        "settled": settled,
        "decisions": [d.to_dict() for d in decided],
        "near_decision": near,
        "stats": paper.get_stats(bot_id="control"),
        "bots": paper.get_all_bot_stats(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = run_once(force_decision=True)
    print(json.dumps(out, indent=2, default=str))
