"""Replay clock for backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class ReplayClock:
    """UTC clock that advances in fixed steps."""

    now: datetime
    step: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=timezone.utc)

    def advance(self) -> datetime:
        self.now = self.now + self.step
        return self.now

    def set(self, when: datetime) -> datetime:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        self.now = when.astimezone(timezone.utc)
        return self.now
