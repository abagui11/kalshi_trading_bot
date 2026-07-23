"""Strategy protocol for Kalshi multi-bot paper experiments."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from models import KalshiSuggestion
from strategies.context import SharedCycleContext


@runtime_checkable
class Strategy(Protocol):
    """One experiment bot: decide from shared cycle context."""

    bot_id: str
    display_name: str
    needs_htf_bias: bool

    def decide(self, ctx: SharedCycleContext) -> KalshiSuggestion | None:
        """Return a suggestion for this bot, or None to stay silent this tick."""
        ...
