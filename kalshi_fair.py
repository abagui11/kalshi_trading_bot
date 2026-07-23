"""Short-horizon binary fair value for Kalshi 15m (Coinbase spot vs floor_strike proxy).

Kalshi may settle on BRTI / window averages rather than Coinbase last. This module
prices Coinbase spot vs market floor_strike as an explicit proxy — known bias, not truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


def _phi(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def parse_expiry_ts(expiry: str | None) -> datetime | None:
    if not expiry:
        return None
    s = str(expiry).strip()
    try:
        if s.endswith("Z"):
            # Support fractional seconds
            if "." in s:
                return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    tzinfo=timezone.utc
                )
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def tau_seconds(expiry: str | None, *, now: datetime | None = None) -> float:
    """Seconds remaining until expiry; floor at 1s."""
    now = now or datetime.now(timezone.utc)
    exp = parse_expiry_ts(expiry)
    if exp is None:
        return 15.0 * 60.0
    return max(1.0, (exp - now).total_seconds())


def m5_log_return_sigma(bars: Sequence[dict[str, Any]], *, lookback: int = 12) -> float:
    """Annualized? No — per-sqrt-second vol from M5 log returns over ~lookback bars.

    Returns sigma such that move over tau seconds ≈ sigma * sqrt(tau).
    Each M5 bar is 300s; sigma_bar = stdev(log returns); sigma_1s = sigma_bar / sqrt(300).
    """
    closes: list[float] = []
    for b in bars[-(lookback + 1) :]:
        try:
            closes.append(float(b["close"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(closes) < 3:
        # Fallback: ~0.05% per sqrt(minute) rough crypto noise
        return 0.0005 / math.sqrt(60.0)
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(rets) < 2:
        return 0.0005 / math.sqrt(60.0)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    sigma_bar = math.sqrt(max(var, 1e-16))
    return sigma_bar / math.sqrt(300.0)


def prior_return_pct(bars: Sequence[dict[str, Any]], n_bars: int) -> float | None:
    """Simple close-to-close return over n M5 bars, as percent."""
    if len(bars) < n_bars + 1:
        return None
    try:
        a = float(bars[-(n_bars + 1)]["close"])
        b = float(bars[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if a <= 0:
        return None
    return (b / a - 1.0) * 100.0


@dataclass(frozen=True)
class FairValueResult:
    """Proxy fair value for YES (price above strike at settle)."""

    fair_yes_cents: float
    spot: float
    strike: float
    gap: float  # ln(spot/strike)
    sigma: float
    tau_sec: float
    spot_vs_strike_pct: float
    d: float  # gap / (sigma * sqrt(tau))

    @property
    def edge_vs_mid(self) -> float:
        """Placeholder; real edge needs mid — use edge_cents(mid)."""
        return 0.0

    def edge_cents(self, yes_mid_cents: float) -> float:
        return float(self.fair_yes_cents) - float(yes_mid_cents)


def fair_yes_cents(
    spot: float,
    strike: float,
    tau_sec: float,
    sigma: float,
) -> FairValueResult:
    """P(YES) ≈ Φ(ln(S/K) / (σ √τ)) → cents.

    Tiny gap + long τ → ~50¢. Large gap / short τ → extreme.
    """
    s = float(spot)
    k = float(strike)
    if s <= 0 or k <= 0:
        raise ValueError(f"spot and strike must be positive (spot={s}, strike={k})")
    tau = max(1.0, float(tau_sec))
    sig = max(1e-12, float(sigma))
    gap = math.log(s / k)
    d = gap / (sig * math.sqrt(tau))
    p = _phi(d)
    cents = max(1.0, min(99.0, p * 100.0))
    return FairValueResult(
        fair_yes_cents=cents,
        spot=s,
        strike=k,
        gap=gap,
        sigma=sig,
        tau_sec=tau,
        spot_vs_strike_pct=(s / k - 1.0) * 100.0,
        d=d,
    )


def side_agrees_with_edge(side: str, edge_cents: float) -> bool:
    """YES only if fair > mid (edge>0); NO only if fair < mid (edge<0)."""
    s = side.upper()
    if s == "YES":
        return edge_cents > 0
    if s == "NO":
        return edge_cents < 0
    return False


def has_min_edge(edge_cents: float, min_edge: float) -> bool:
    return abs(float(edge_cents)) >= float(min_edge)
