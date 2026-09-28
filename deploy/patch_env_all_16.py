#!/usr/bin/env python3
"""All three sleeves at 16 contracts; only eva_wick live (2026-09-28).

Step one of the measured size-up: 8 -> 16 ct against the recorded book
(participation cap ~7,257 ct/trade, per-contract edge unmeasured above 8).
The full-window quote log prices realized fills vs the decision-time ask, so
this step's slippage is measurable after ~a week.

Deploy budget must clear 16 ct at the 82c max-pay: 16 x $0.82 = $13.12.
Bankroll $225 x 0.06 = $13.50 (was 0.05 = $11.25, which caps at 13-15 ct in
the band). KALSHI_MAX_NOTIONAL_USD=34.88 and MAX_DEPLOY_PCT=0.15 already
clear it; the daily stop stays 0.25 of day-start bankroll.
"""
from pathlib import Path

TARGET = {
    "KALSHI_MAX_CONTRACTS": "16",
    "KALSHI_DEPLOY_PCT": "0.06",
    # empty = every bot uses the global 16
    "KALSHI_BOT_MAX_CONTRACTS": "",
    # unchanged, restated so a drifted env cannot silently widen the live set
    "KALSHI_LIVE_BOTS": "eva_wick",
}

p = Path("/opt/kalshi-15m-bot/.env")
lines = p.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key in TARGET and not line.strip().startswith("#"):
        out.append(f"{key}={TARGET[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, val in TARGET.items():
    if key not in seen:
        out.append(f"{key}={val}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")

print("=== sizing env now ===")
for line in out:
    if line.split("=", 1)[0].strip() in (
        "KALSHI_MAX_CONTRACTS", "KALSHI_DEPLOY_PCT", "KALSHI_BOT_MAX_CONTRACTS",
        "KALSHI_LIVE_BOTS", "KALSHI_BANKROLL_USD", "KALSHI_MAX_NOTIONAL_USD",
    ):
        print("  " + line)
