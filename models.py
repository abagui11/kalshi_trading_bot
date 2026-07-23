"""Shared data models for ICT suggestions and Kalshi 15m paper trades."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Suggestion:
    """ICT / vision-agent trade suggestion (spot path + Kalshi direction source)."""

    action: str  # spot_buy|spot_sell|deriv_buy|deriv_sell|no_trade
    size: float  # USD notional to deploy; paper positions store qty separately
    entry: float | None
    stop_loss: float | None
    take_profits: list[float] = field(default_factory=list)
    risk_reward: float | None = None
    rationale: str = ""
    order_block: dict[str, Any] | None = None  # low, high, start_ts, end_ts
    decision_charts: list[str] = field(default_factory=list)
    structure_chart: str | None = None
    entry_chart: str | None = None
    deploy_pct: float | None = None  # override TRADE_DEPLOY_PCT for tranche / add sizing
    entry_tranche: str | None = None  # e.g. "0.25", "0.50", "0.718", "sweep"
    order_block_ref: str | None = None  # links scale-ins to the same M5 OB
    product_id: str = "ETH-USD"  # Coinbase product, e.g. ETH-USD / BTC-USD
    macro_note: str | None = None  # required when inject-level macro is active
    trigger_name: str | None = None  # watchdog structured trigger (e.g. m5_ob_fib_short)

    @classmethod
    def no_trade(
        cls,
        rationale: str = "No setup",
        *,
        product_id: str = "ETH-USD",
    ) -> Suggestion:
        return cls(
            action="no_trade",
            size=0.0,
            entry=None,
            stop_loss=None,
            take_profits=[],
            risk_reward=None,
            rationale=rationale,
            order_block=None,
            product_id=product_id,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Suggestion:
        raw_charts = data.get("decision_charts") or []
        decision_charts = [str(x).upper() for x in raw_charts if x]
        structure = data.get("structure_chart")
        entry = data.get("entry_chart")
        deploy_raw = data.get("deploy_pct")
        product = str(data.get("product_id") or "ETH-USD")
        macro_raw = data.get("macro_note")
        trigger_raw = data.get("trigger_name")
        return cls(
            action=str(data.get("action", "no_trade")),
            size=float(data.get("size", 0) or 0),
            entry=float(data["entry"]) if data.get("entry") is not None else None,
            stop_loss=float(data["stop_loss"]) if data.get("stop_loss") is not None else None,
            take_profits=[float(tp) for tp in data.get("take_profits", [])],
            risk_reward=(
                float(data["risk_reward"]) if data.get("risk_reward") is not None else None
            ),
            rationale=str(data.get("rationale", "")),
            order_block=data.get("order_block"),
            decision_charts=decision_charts,
            structure_chart=str(structure).upper() if structure else None,
            entry_chart=str(entry).upper() if entry else None,
            deploy_pct=float(deploy_raw) if deploy_raw is not None else None,
            entry_tranche=str(data["entry_tranche"]) if data.get("entry_tranche") else None,
            order_block_ref=(
                str(data["order_block_ref"]) if data.get("order_block_ref") else None
            ),
            product_id=product,
            macro_note=str(macro_raw).strip() if macro_raw else None,
            trigger_name=str(trigger_raw).strip() if trigger_raw else None,
        )


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
    mid_cents: float | None = None  # always YES mid
    edge_cents: float | None = None  # model_fair − yes_mid
    ict_action: str | None = None
    ict_bias: str | None = None
    # Audit / feature fields (not all persisted on paper_positions)
    spot: float | None = None
    strike: float | None = None
    spot_vs_strike_pct: float | None = None
    tau_sec: float | None = None
    sigma: float | None = None
    prior_5m_ret: float | None = None
    prior_15m_ret: float | None = None
    prior_1h_ret: float | None = None
    gate_outcome: str | None = None
    trigger_type: str | None = None
    ob_low: float | None = None
    ob_high: float | None = None
    h1_bias_tag: str | None = None
    critic_passes: int = 0
    critic_findings: list[dict[str, Any]] = field(default_factory=list)
    critic_downgraded: bool = False
    would_skip_reasons: list[str] = field(default_factory=list)
    chart_path: str | None = None
    structure_chart_path: str | None = None
    entry_chart_path: str | None = None
    setup_tags: list[str] = field(default_factory=list)
    skip_codes: list[str] = field(default_factory=list)
    chart_read_score: float | None = None
    seconds_to_expiry: float | None = None
    cycle_id: str | None = None
    opened: bool = False
    position_id: int | None = None
    trigger_name: str | None = None
    bot_id: str = "control"
    # When True, paper places a working limit (not immediate fill).
    pending_limit: bool = False
    cancel_at_ts: str | None = None
    order_id: int | None = None

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
        edge_cents: float | None = None,
        expiry_ts: str | None = None,
        ict_action: str | None = None,
        ict_bias: str | None = None,
        **kwargs: Any,
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
            edge_cents=edge_cents,
            ict_action=ict_action,
            ict_bias=ict_bias,
            **kwargs,
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
            "ict_action": self.ict_action,
            "ict_bias": self.ict_bias,
            "spot": self.spot,
            "strike": self.strike,
            "spot_vs_strike_pct": self.spot_vs_strike_pct,
            "tau_sec": self.tau_sec,
            "sigma": self.sigma,
            "gate_outcome": self.gate_outcome,
            "trigger_type": self.trigger_type,
            "would_skip_reasons": list(self.would_skip_reasons),
            "critic_downgraded": self.critic_downgraded,
            "opened": self.opened,
            "position_id": self.position_id,
            "chart_path": self.chart_path,
            "structure_chart_path": self.structure_chart_path,
            "entry_chart_path": self.entry_chart_path,
            "setup_tags": list(self.setup_tags),
            "skip_codes": list(self.skip_codes),
            "chart_read_score": self.chart_read_score,
            "seconds_to_expiry": self.seconds_to_expiry,
            "trigger_name": self.trigger_name,
            "h1_bias_tag": self.h1_bias_tag,
            "bot_id": self.bot_id,
            "pending_limit": self.pending_limit,
            "cancel_at_ts": self.cancel_at_ts,
            "order_id": self.order_id,
        }
