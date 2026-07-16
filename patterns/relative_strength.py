"""W1 ETH/BTC relative-strength context for dual-asset bias."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import research
from patterns.htf_structure import HTFZone, detect_htf_zones
from patterns.sfp import SFPEvent, detect_sfps

Bias = Literal["eth_strong", "btc_strong", "neutral"]


@dataclass
class RelativeStrengthContext:
    bias: Bias
    spot_ratio: float
    w1_bars: list[dict[str, float | str]] = field(default_factory=list)
    htf_zones: list[HTFZone] = field(default_factory=list)
    sfps: list[SFPEvent] = field(default_factory=list)
    summary_text: str = ""
    setup_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias,
            "spot_ratio": self.spot_ratio,
            "summary_text": self.summary_text,
            "setup_tags": list(self.setup_tags),
            "htf_zones": [
                {
                    "zone_type": z.zone_type,
                    "direction": z.direction,
                    "low": z.low,
                    "high": z.high,
                    "start_ts": z.start_ts,
                    "end_ts": z.end_ts,
                    "mitigated": z.mitigated,
                }
                for z in self.htf_zones
            ],
            "sfps": [
                {
                    "ts": s.ts,
                    "direction": s.direction,
                    "swept_level": s.swept_level,
                    "timeframe": s.timeframe,
                }
                for s in self.sfps
            ],
        }


def _active_zones(zones: list[HTFZone], spot: float) -> list[HTFZone]:
    active: list[HTFZone] = []
    for z in zones:
        if z.mitigated:
            continue
        mid = (float(z.low) + float(z.high)) / 2.0
        if mid <= 0:
            continue
        if abs(spot - mid) / mid <= 0.08 or float(z.low) <= spot <= float(z.high):
            active.append(z)
    return active


def _infer_bias(
    spot: float,
    zones: list[HTFZone],
    sfps: list[SFPEvent],
) -> tuple[Bias, list[str]]:
    tags: list[str] = []
    score = 0  # positive → eth_strong

    for z in _active_zones(zones, spot):
        if z.direction == "bullish":
            score += 2
            tags.append(f"ethbtc_w1_{z.zone_type}_bullish")
        else:
            score -= 2
            tags.append(f"ethbtc_w1_{z.zone_type}_bearish")

    recent = sorted(sfps, key=lambda s: s.ts)[-3:]
    for s in recent:
        if s.direction == "bullish":
            score += 1
            tags.append("ethbtc_w1_sfp_bullish")
        else:
            score -= 1
            tags.append("ethbtc_w1_sfp_bearish")

    if score >= 2:
        return "eth_strong", tags
    if score <= -2:
        return "btc_strong", tags
    return "neutral", tags


def _build_summary(
    bias: Bias,
    spot: float,
    zones: list[HTFZone],
    sfps: list[SFPEvent],
) -> str:
    lines = [
        "## ETH/BTC relative strength (W1)",
        f"Spot ratio: {spot:.6f}",
        f"Bias: {bias}",
    ]
    if bias == "eth_strong":
        lines.append(
            "Implication: prefer ETH longs / BTC shorts when setups align; "
            "ETH is the stronger asset vs BTC on the weekly ratio."
        )
    elif bias == "btc_strong":
        lines.append(
            "Implication: prefer BTC longs / ETH shorts when setups align; "
            "BTC is the stronger asset vs ETH on the weekly ratio."
        )
    else:
        lines.append(
            "Implication: no clear relative-strength edge; size both assets evenly "
            "when independent setups appear."
        )

    active = _active_zones(zones, spot)
    if active:
        lines.append("Active W1 ETH/BTC zones near price:")
        for z in active[:4]:
            lines.append(
                f"- {z.zone_type} {z.direction} "
                f"[{z.low:.6f}–{z.high:.6f}] formed {z.start_ts}"
            )
    recent = sorted(sfps, key=lambda s: s.ts)[-3:]
    if recent:
        lines.append("Recent W1 ETH/BTC SFPs:")
        for s in recent:
            lines.append(
                f"- {s.direction} SFP @ {s.swept_level:.6f} ({s.ts})"
            )
    lines.append(
        "Rationale must cite this bias when choosing or weighting ETH vs BTC trades."
    )
    return "\n".join(lines)


def build_relative_strength_context(
    *,
    eth_w1: list[dict[str, float | str]] | None = None,
    btc_w1: list[dict[str, float | str]] | None = None,
) -> RelativeStrengthContext:
    """Build W1 ETH/BTC context from weekly bars (fetched if not provided)."""
    if eth_w1 is None:
        eth_w1 = research.get_ohlc("W1", product_id="ETH-USD")
    if btc_w1 is None:
        btc_w1 = research.get_ohlc("W1", product_id="BTC-USD")

    ratio_bars = research.build_eth_btc_ratio_bars(eth_w1, btc_w1)
    if not ratio_bars:
        return RelativeStrengthContext(
            bias="neutral",
            spot_ratio=0.0,
            summary_text="## ETH/BTC relative strength (W1)\nInsufficient aligned bars.",
        )

    spot = float(ratio_bars[-1]["close"])
    zones = detect_htf_zones(ratio_bars)
    try:
        sfps = detect_sfps(ratio_bars, "W1")
    except Exception:
        sfps = []

    bias, tags = _infer_bias(spot, zones, sfps)
    summary = _build_summary(bias, spot, zones, sfps)
    return RelativeStrengthContext(
        bias=bias,
        spot_ratio=spot,
        w1_bars=ratio_bars,
        htf_zones=zones,
        sfps=sfps,
        summary_text=summary,
        setup_tags=list(dict.fromkeys(tags)),
    )


def soft_gate_allows(
    bias: Bias,
    product_id: str,
    side: str,
) -> bool:
    """Watchdog soft-gate: allow unless clearly fighting relative strength.

    Fighting means longing the weaker asset or shorting the stronger asset.
    Neutral bias always allows.
    """
    if bias == "neutral":
        return True
    is_eth = product_id.startswith("ETH")
    is_long = side == "long"
    if bias == "eth_strong":
        if is_eth and not is_long:
            return False
        if not is_eth and is_long:
            return False
    elif bias == "btc_strong":
        if not is_eth and not is_long:
            return False
        if is_eth and is_long:
            return False
    return True
