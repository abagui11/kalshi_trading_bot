"""Build SharedCycleContext for a replay tick."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import kalshi_fair
from backtest.data import WindowSpec, bars_as_of, spot_at
from strategies.context import SharedCycleContext, SharedHtfBias


def build_context(
    window: WindowSpec,
    bars: list[dict[str, Any]],
    *,
    now: datetime,
    htf: SharedHtfBias | None = None,
    near_decision: bool = False,
    yes_mid_override: float | None = None,
) -> SharedCycleContext | None:
    """Synthetic mid from kalshi_fair unless yes_mid_override (archive) is set."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    asof = bars_as_of(bars, now)
    if len(asof) < 3:
        return None
    spot = spot_at(bars, now)
    if spot is None:
        return None

    sigma = kalshi_fair.m5_log_return_sigma(asof, lookback=12)
    expiry_s = window.expiry_ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tau = kalshi_fair.tau_seconds(expiry_s, now=now)
    fair_res = kalshi_fair.fair_yes_cents(spot, window.strike, tau, sigma)
    fair_cents = float(fair_res.fair_yes_cents)
    mid = float(yes_mid_override) if yes_mid_override is not None else fair_cents
    # Clamp mid into tradable band for lottery tests.
    mid = max(1.0, min(99.0, mid))
    edge = fair_cents - mid

    cycle_id = now.strftime("%Y%m%dT%H%M%SZ")
    base = {
        "series": window.series,
        "market_ticker": window.market_ticker,
        "product_id": window.product_id,
        "mid_cents": mid,
        "fair_yes_cents": fair_cents,
        "edge_cents": edge,
        "expiry_ts": expiry_s,
        "spot": spot,
        "strike": window.strike,
        "spot_vs_strike_pct": fair_res.spot_vs_strike_pct,
        "tau_sec": tau,
        "sigma": sigma,
        "prior_5m_ret": kalshi_fair.prior_return_pct(asof, 1),
        "prior_15m_ret": kalshi_fair.prior_return_pct(asof, 3),
        "prior_1h_ret": kalshi_fair.prior_return_pct(asof, 12),
        "cycle_id": cycle_id,
    }
    return SharedCycleContext(
        series=window.series,
        market={"ticker": window.market_ticker, "floor_strike": window.strike},
        market_ticker=window.market_ticker,
        product_id=window.product_id,
        coinbase=window.coinbase,
        cycle_id=cycle_id,
        expiry_ts=expiry_s,
        yes_mid_cents=mid,
        spot=spot,
        strike=window.strike,
        sigma=sigma,
        tau_sec=tau,
        spot_vs_strike_pct=fair_res.spot_vs_strike_pct,
        prior_5m_ret=base["prior_5m_ret"],
        prior_15m_ret=base["prior_15m_ret"],
        prior_1h_ret=base["prior_1h_ret"],
        fair_yes_cents=fair_cents,
        edge_cents=edge,
        m5_bars=asof[-20:],
        htf=htf,
        near_decision=near_decision,
        base_kwargs=base,
        now=now,
    )
