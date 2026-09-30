"""Strategy registry — enabled bots from bot_config.ENABLED_BOTS."""

from __future__ import annotations

from typing import TYPE_CHECKING

import bot_config
import config

if TYPE_CHECKING:
    from strategies.base import Strategy


def _build_registry() -> dict[str, Strategy]:
    from strategies.adverse import AdverseStrategy
    from strategies.control import ControlStrategy
    from strategies.eva_arb import EvaArbStrategy
    from strategies.eva_streak import EvaStreakStrategy
    from strategies.eva_wick import EvaWickStrategy
    from strategies.eva_wick_alt import alt_wick_strategies
    from strategies.lottery import LotteryStrategy

    control = ControlStrategy()
    lottery = LotteryStrategy()
    adverse = AdverseStrategy()
    eva_wick = EvaWickStrategy()
    eva_streak = EvaStreakStrategy()
    eva_arb = EvaArbStrategy()
    reg: dict[str, Strategy] = {
        control.bot_id: control,
        lottery.bot_id: lottery,
        adverse.bot_id: adverse,
        eva_wick.bot_id: eva_wick,
        eva_streak.bot_id: eva_streak,
        eva_arb.bot_id: eva_arb,
    }
    for alt in alt_wick_strategies():
        reg[alt.bot_id] = alt
    return reg


_REGISTRY: dict[str, Strategy] | None = None


def _registry() -> dict[str, Strategy]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def list_bot_ids() -> tuple[str, ...]:
    return tuple(bot_config.ENABLED_BOTS)


def get_strategy(bot_id: str) -> Strategy | None:
    return _registry().get(bot_id)


def enabled_strategies() -> list[Strategy]:
    reg = _registry()
    out: list[Strategy] = []
    for bot_id in bot_config.ENABLED_BOTS:
        strat = reg.get(bot_id)
        if strat is not None:
            out.append(strat)
    return out


def any_needs_htf_bias() -> bool:
    return any(s.needs_htf_bias for s in enabled_strategies())


def handles_series(strat: Strategy, series: str) -> bool:
    """Whether this bot may act on ``series`` at all.

    A bot that declares ``series_filter`` sees only those series. Everything
    else sees exactly the series an operator put in KALSHI_SERIES — so the
    altcoin series that ``bot_config.active_series`` appends on behalf of the
    paper clones are invisible to the shipped books, and the live book cannot
    be quietly moved into a market it was never measured on.
    """
    own = getattr(strat, "series_filter", None)
    if own is not None:
        return series.upper() in {s.upper() for s in own}
    return series.upper() in {s.upper() for s in config.KALSHI_SERIES}
