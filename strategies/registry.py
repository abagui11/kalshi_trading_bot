"""Strategy registry — enabled bots from bot_config.ENABLED_BOTS."""

from __future__ import annotations

from typing import TYPE_CHECKING

import bot_config

if TYPE_CHECKING:
    from strategies.base import Strategy


def _build_registry() -> dict[str, Strategy]:
    from strategies.adverse import AdverseStrategy
    from strategies.control import ControlStrategy
    from strategies.lottery import LotteryStrategy

    control = ControlStrategy()
    lottery = LotteryStrategy()
    adverse = AdverseStrategy()
    return {
        control.bot_id: control,
        lottery.bot_id: lottery,
        adverse.bot_id: adverse,
    }


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
