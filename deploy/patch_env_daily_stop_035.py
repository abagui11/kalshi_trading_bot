#!/usr/bin/env python3
"""Daily loss stop 0.25 -> 0.35 of day-start bankroll (2026-09-28).

Why: the stop is denominated in BANKROLL while risk is denominated in
CONTRACTS. The 8->16 ct size-up doubled daily variance and left the dollar
limit at $56.25, which turned a 1.89-sd tail guard into a 0.95-sd trigger —
it fired on day one at the new size (15:50Z, eva_wick -$65.37).

Measured on the 7 live-era days scaled to 16 ct
(trade_ideas/analysis/scripts/_q0928_daily_stop_at_16.py), monotonic:
    0.15 -> -$171.65 (198 trades blocked, 3/7 days)
    0.25 -> -$4.78   (22 blocked, 1/7 days; 16 of the 22 would have won)
    0.35 -> $0.00    (never binds on the record)
    0.50+-> $0.00
0.35 = $78.75 = 1.32 sd, above the worst 16ct-equivalent day (-$56.50), so
it restores "insurance, not a tax" while capping a bad day at a third of
the shard rather than half.

REVISIT ON EVERY SIZE STEP. At 32 ct, $78.75 would be ~0.66 sd and this
same defect returns.
"""
from pathlib import Path

TARGET = {"KALSHI_DAILY_STOP_PCT": "0.35"}

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

print("=== risk env now ===")
for line in out:
    if line.split("=", 1)[0].strip() in (
        "KALSHI_DAILY_STOP_PCT", "KALSHI_BANKROLL_USD",
        "KALSHI_MAX_CONTRACTS", "KALSHI_LIVE_BOTS",
    ):
        print("  " + line)
