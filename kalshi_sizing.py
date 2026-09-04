"""Shared Kalshi contract sizing — keep live risk identical to paper soak caps."""

from __future__ import annotations

import logging

import bot_config
import config

logger = logging.getLogger(__name__)


def configured_bankroll_usd() -> float:
    return max(0.0, float(bot_config.KALSHI_BANKROLL_USD))


def max_notional_usd() -> float:
    """Hard ceiling on notional ($/trade). Default ~paper 5% of $77."""
    raw = getattr(bot_config, "KALSHI_MAX_NOTIONAL_USD", None)
    if raw is None:
        return 5.0
    return max(0.0, float(raw))


def max_contracts_cap() -> int:
    return max(0, int(bot_config.KALSHI_MAX_CONTRACTS))


def _spendable_usd(bal: dict) -> float | None:
    """Cash actually usable for these markets, in dollars.

    Kalshi sharded on 2026-08-24 and collateral does not move across shards, so
    the top-level balance fields are a cross-shard aggregate that cannot all be
    spent here. Prefer this series' shard from ``balance_breakdown``; only fall
    back to the aggregate when the breakdown is missing.
    """
    shard = int(getattr(bot_config, "KALSHI_EXCHANGE_INDEX", 2))
    for row in bal.get("balance_breakdown") or []:
        try:
            if int(row["exchange_index"]) == shard:
                return max(0.0, float(row["balance"]))
        except (KeyError, TypeError, ValueError):
            continue
    if bal.get("balance_dollars") is not None:
        return max(0.0, float(bal["balance_dollars"]))
    for key in ("balance", "portfolio_value"):
        if bal.get(key) is not None:
            raw = float(bal[key])
            return raw / 100.0 if raw > 1000 else raw
    return None


def sizing_bankroll_usd() -> float:
    """Bankroll used for % deploy sizing.

    Live balance may be fetched, but never *above* configured soak bankroll —
    so depositing more cash cannot silently size up.
    """
    configured = configured_bankroll_usd()
    if config.KALSHI_PAPER_ONLY or not bot_config.KALSHI_USE_LIVE_BALANCE:
        return configured
    try:
        import kalshi_client

        live = _spendable_usd(kalshi_client.get_balance())
        if live is None:
            return configured
        if live < configured:
            logger.info(
                "Sizing off shard-%s balance $%.2f (below configured $%.2f)",
                getattr(bot_config, "KALSHI_EXCHANGE_INDEX", 2),
                live,
                configured,
            )
        return min(live, configured)
    except Exception:
        logger.exception(
            "Live balance fetch failed — using KALSHI_BANKROLL_USD=%.2f", configured
        )
        return configured


def max_deploy_pct() -> float:
    return max(0.0, float(getattr(bot_config, "KALSHI_MAX_DEPLOY_PCT", 0.15)))


def contracts_for_entry(entry_cents: float, *, deploy_pct: float | None = None) -> tuple[int, float]:
    """Return (contracts, budget_usd) for a side entry price in cents."""
    bankroll = sizing_bankroll_usd()
    pct = float(bot_config.KALSHI_DEPLOY_PCT if deploy_pct is None else deploy_pct)
    pct = min(pct, max_deploy_pct()) if max_deploy_pct() > 0 else pct
    budget = max(0.0, bankroll * pct)
    # Also clamp budget to absolute notional ceiling (0 = disabled).
    budget = min(budget, max_notional_usd()) if max_notional_usd() > 0 else budget
    price = float(entry_cents) / 100.0
    cap = max_contracts_cap()
    if price <= 0 or cap < 1:
        return 0, budget
    raw = int(budget // price)
    contracts = max(0, min(cap, raw))
    if contracts < 1 and budget >= price and cap >= 1:
        contracts = 1
    contracts = clamp_contracts(contracts, entry_cents)
    return contracts, budget


def clamp_contracts(contracts: int, entry_cents: float) -> int:
    """Hard clamp: never exceed MAX_CONTRACTS or MAX_NOTIONAL."""
    ct = max(0, int(contracts))
    cap = max_contracts_cap()
    ct = min(ct, cap)
    price = float(entry_cents) / 100.0
    ceiling = max_notional_usd()
    if price > 0 and ceiling > 0:
        ct = min(ct, int(ceiling // price))
    return max(0, ct)


def assert_order_allowed(contracts: int, entry_cents: float) -> None:
    """Raise ValueError if an order would exceed live safety caps."""
    ct = int(contracts)
    if ct < 1:
        raise ValueError("contracts must be >= 1")
    clamped = clamp_contracts(ct, entry_cents)
    if clamped != ct:
        raise ValueError(
            f"order rejected: {ct} ct @ {entry_cents:.1f}¢ exceeds "
            f"max {max_contracts_cap()} ct / ${max_notional_usd():.2f} notional "
            f"(clamped would be {clamped})"
        )
    notional = ct * (float(entry_cents) / 100.0)
    if max_notional_usd() > 0 and notional > max_notional_usd() + 1e-9:
        raise ValueError(
            f"order rejected: notional ${notional:.2f} > max ${max_notional_usd():.2f}"
        )
