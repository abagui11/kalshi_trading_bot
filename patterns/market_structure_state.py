"""Live HTF market structure state per underlying (BTC-USD / ETH-USD)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from patterns.market_context import MarketContext
from patterns.signal_state import get_state, set_state

STATE_KEY_PREFIX = "market_structure_"


@dataclass
class MarketStructureState:
    """Persisted HTF bias + watching list for dashboard and decision cycles."""

    product_id: str  # Coinbase id e.g. BTC-USD
    htf_bias: str = "unknown"  # bull | bear | mixed | unknown
    h1_bias: str = "unknown"
    range_24h_label: str = ""
    primary_demand: dict[str, Any] | None = None
    primary_supply: dict[str, Any] | None = None
    setup_phase: str = "idle"
    watching: list[str] = field(default_factory=list)
    window_thesis: str = ""
    market_ticker: str | None = None
    structure_chart_path: str | None = None
    h1_chart_path: str | None = None
    entry_chart_path: str | None = None
    spot: float | None = None
    updated_at: str = ""
    setup_tags: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, product_id: str) -> MarketStructureState:
        if not data:
            return cls(product_id=product_id)
        return cls(
            product_id=str(data.get("product_id") or product_id),
            htf_bias=str(data.get("htf_bias") or "unknown"),
            h1_bias=str(data.get("h1_bias") or "unknown"),
            range_24h_label=str(data.get("range_24h_label") or ""),
            primary_demand=data.get("primary_demand"),
            primary_supply=data.get("primary_supply"),
            setup_phase=str(data.get("setup_phase") or "idle"),
            watching=list(data.get("watching") or []),
            window_thesis=str(data.get("window_thesis") or ""),
            market_ticker=data.get("market_ticker"),
            structure_chart_path=data.get("structure_chart_path"),
            h1_chart_path=data.get("h1_chart_path"),
            entry_chart_path=data.get("entry_chart_path"),
            spot=float(data["spot"]) if data.get("spot") is not None else None,
            updated_at=str(data.get("updated_at") or ""),
            setup_tags=list(data.get("setup_tags") or []),
            alerts=list(data.get("alerts") or []),
        )


def _state_key(product_id: str) -> str:
    return f"{STATE_KEY_PREFIX}{product_id}"


def load_structure_state(product_id: str) -> MarketStructureState:
    raw = get_state(_state_key(product_id))
    return MarketStructureState.from_dict(raw, product_id=product_id)


def save_structure_state(state: MarketStructureState) -> None:
    if not state.updated_at:
        state.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_state(_state_key(state.product_id), state.to_dict())


def _zone_dict(zone: Any) -> dict[str, Any] | None:
    if zone is None:
        return None
    return {
        "low": float(zone.low),
        "high": float(zone.high),
        "direction": getattr(zone, "direction", None),
        "zone_type": getattr(zone, "zone_type", None),
    }


def _fmt_zone(low: float, high: float) -> str:
    return f"{low:,.2f}–{high:,.2f}"


def watching_from_context(ctx: MarketContext) -> list[str]:
    """Human-readable zones/OBs the bot is monitoring."""
    watching: list[str] = []
    setup = ctx.setup_state
    if setup is not None:
        low = getattr(setup, "retest_low", None)
        high = getattr(setup, "retest_high", None)
        if low is not None and high is not None:
            watching.append(f"Bearish retest zone: {_fmt_zone(float(low), float(high))}")

    for ob in (ctx.order_blocks or [])[-2:]:
        watching.append(
            f"M5 {ob.direction} order block: {_fmt_zone(float(ob.low), float(ob.high))}"
        )

    snap = ctx.zone_snapshot
    if snap is not None:
        for zone in (snap.zones_containing_price or [])[:2]:
            ztype = str(getattr(zone, "zone_type", "zone")).upper()
            direction = str(getattr(zone, "direction", ""))
            watching.append(
                f"H4 {ztype} {direction}: {_fmt_zone(float(zone.low), float(zone.high))}"
            )
        if snap.primary_bullish is not None:
            z = snap.primary_bullish
            watching.append(f"Primary H4 demand: {_fmt_zone(float(z.low), float(z.high))}")
        if snap.primary_bearish is not None:
            z = snap.primary_bearish
            watching.append(f"Primary H4 supply: {_fmt_zone(float(z.low), float(z.high))}")
    # Dedupe preserve order
    return list(dict.fromkeys(watching))[:8]


def h1_bias_from_context(ctx: MarketContext) -> str:
    """Coarse H1 / 24h range bias."""
    if ctx.range_break == "above":
        return "bull"
    if ctx.range_break == "below":
        return "bear"
    if ctx.is_ranging:
        return "mixed"
    tags = {t.lower() for t in (ctx.setup_tags or [])}
    if "range_24h_break_above" in tags:
        return "bull"
    if "range_24h_break_below" in tags:
        return "bear"
    return "mixed" if ctx.range_24h else "unknown"


def window_thesis_for_bias(htf_bias: str, market_ticker: str | None = None) -> str:
    """How HTF bias maps to Kalshi YES/NO for the active 15m window."""
    ticker = market_ticker or "current window"
    if htf_bias == "bull":
        return (
            f"{ticker}: HTF bull — favor YES (up) when LTF entry + edge align; "
            "NO is counter-HTF and needs explicit acknowledgment."
        )
    if htf_bias == "bear":
        return (
            f"{ticker}: HTF bear — favor NO (down) when LTF entry + edge align; "
            "YES is counter-HTF and needs explicit acknowledgment."
        )
    if htf_bias == "mixed":
        return (
            f"{ticker}: HTF mixed — no clear H4 lean; require LTF OB/SFP + edge; "
            "tag htf_mixed."
        )
    return f"{ticker}: HTF bias unknown — wait for structure refresh."


def refresh_from_context(
    ctx: MarketContext,
    *,
    product_id: str,
    market_ticker: str | None = None,
    marked_paths: dict[str, str] | None = None,
    htf_bias: str | None = None,
) -> MarketStructureState:
    """Build and persist live structure from a MarketContext + optional chart paths."""
    from kalshi_triggers import htf_bias_from_context

    bias = htf_bias or htf_bias_from_context(ctx)
    h1 = h1_bias_from_context(ctx)
    range_label = ""
    if ctx.range_24h is not None:
        r = ctx.range_24h
        range_label = f"24h {_fmt_zone(float(r.low), float(r.high))}"
        if ctx.is_ranging:
            range_label += " (ranging)"
        elif ctx.range_break:
            range_label += f" (break {ctx.range_break})"

    snap = ctx.zone_snapshot
    phase = "idle"
    if ctx.setup_state is not None:
        phase = str(getattr(ctx.setup_state, "phase", None) or "idle")

    paths = marked_paths or {}
    state = MarketStructureState(
        product_id=product_id,
        htf_bias=bias,
        h1_bias=h1,
        range_24h_label=range_label,
        primary_demand=_zone_dict(snap.primary_bullish) if snap else None,
        primary_supply=_zone_dict(snap.primary_bearish) if snap else None,
        setup_phase=phase,
        watching=watching_from_context(ctx),
        window_thesis=window_thesis_for_bias(bias, market_ticker),
        market_ticker=market_ticker,
        structure_chart_path=paths.get("H4"),
        h1_chart_path=paths.get("H1"),
        entry_chart_path=paths.get("M5") or paths.get("M15"),
        spot=float(ctx.spot) if ctx.spot is not None else None,
        updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        setup_tags=list(ctx.setup_tags or []),
        alerts=list(ctx.alerts or [])[:6],
    )
    save_structure_state(state)
    return state


def alignment_tag(side: str | None, htf_bias: str) -> str:
    """Return aligned_htf | counter_htf | htf_mixed for a proposed YES/NO."""
    if htf_bias in ("mixed", "unknown", ""):
        return "htf_mixed"
    if not side:
        return f"htf_{htf_bias}" if htf_bias in ("bull", "bear") else "htf_mixed"
    s = side.upper()
    if htf_bias == "bull":
        return "aligned_htf" if s == "YES" else "counter_htf"
    if htf_bias == "bear":
        return "aligned_htf" if s == "NO" else "counter_htf"
    return "htf_mixed"


def htf_paragraph(htf_bias: str, side: str | None, align_tag: str) -> str:
    """Paragraph 1: HTF bias + whether this window aligns or fades it."""
    lean = {
        "bull": "bullish (favor YES / up)",
        "bear": "bearish (favor NO / down)",
        "mixed": "mixed / conflicted",
        "unknown": "unknown",
    }.get(htf_bias, htf_bias)
    if not side or side.upper() == "SKIP":
        return f"HTF bias is {lean}. No directional fill this cycle ({align_tag})."
    rel = {
        "aligned_htf": f"{side} aligns with HTF",
        "counter_htf": f"{side} fades HTF (counter-structure)",
        "htf_mixed": f"{side} with mixed HTF — LTF must carry the case",
    }.get(align_tag, align_tag)
    return f"HTF bias is {lean}. This window: {rel}."
