"""Shared data models for Kalshi 15m paper trades."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class KalshiSuggestion:
    """Decision or paper fill for a single 15m binary market."""

    series: str
    market_ticker: str
    side: str  # YES | NO | SKIP
    contracts: int
    entry_cents: float | None
    expiry_ts: str | None
    rationale: str
    product_id: str  # BTC | ETH
    fair_yes_cents: float | None = None
    mid_cents: float | None = None
    edge_cents: float | None = None

    @classmethod
    def skip(
        cls,
        *,
        series: str,
        market_ticker: str,
        product_id: str,
        rationale: str,
        mid_cents: float | None = None,
        fair_yes_cents: float | None = None,
        expiry_ts: str | None = None,
    ) -> KalshiSuggestion:
        return cls(
            series=series,
            market_ticker=market_ticker,
            side="SKIP",
            contracts=0,
            entry_cents=None,
            expiry_ts=expiry_ts,
            rationale=rationale,
            product_id=product_id,
            fair_yes_cents=fair_yes_cents,
            mid_cents=mid_cents,
            edge_cents=None,
        )

    def is_trade(self) -> bool:
        return self.side in ("YES", "NO") and self.contracts > 0 and self.entry_cents is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "series": self.series,
            "market_ticker": self.market_ticker,
            "side": self.side,
            "contracts": self.contracts,
            "entry_cents": self.entry_cents,
            "expiry_ts": self.expiry_ts,
            "rationale": self.rationale,
            "product_id": self.product_id,
            "fair_yes_cents": self.fair_yes_cents,
            "mid_cents": self.mid_cents,
            "edge_cents": self.edge_cents,
        }


# Backward-compatible alias used by older imports during the rewrite.
Suggestion = KalshiSuggestion
