#!/usr/bin/env python3
"""Bankroll $225 -> $725 and all sizes scaled proportionally (2026-10-01).

Operator call: $500 moved from the Coinbase account to the Kalshi crypto
shard (the Coinbase sleeves were cut x0.85 in the hub repo the same day).
Everything scales by the same ~3.22x so every ratio the current record was
built on is preserved:

  KALSHI_BANKROLL_USD     225.00 -> 725.00  (shard-2 holds ~$770 after the
                                             deposit; same ~$45 cushion as
                                             the old 270-vs-225 setup)
  KALSHI_MAX_CONTRACTS        16 -> 51      (16 x 725/225 = 51.6, floored)
  KALSHI_MAX_NOTIONAL_USD  34.88 -> 112.39  (same 15.5%-of-bankroll ratio)
  KALSHI_DEPLOY_PCT         0.06 unchanged  (0.06 x 725 = $43.50 clears
                                             51 ct at the 82c max-pay)
  KALSHI_MAX_DEPLOY_PCT     0.15 unchanged
  KALSHI_DAILY_STOP_PCT     0.35 unchanged  — and this time it genuinely is
    unchanged in risk terms: the stop is denominated in bankroll while risk
    is denominated in contracts, and both scale by ~3.22x together, so the
    stop stays ~1.33 sd of a daily move (0.35 x 725 = $253.75 vs a daily sd
    of ~$59.66 x 51/16 = ~$190). The 09-28 step broke this because only the
    contracts moved.

Honest caveat, recorded where the next reader will look: the 09-28 note
said to read back 16-ct fill quality before any further step. The read at
staging time (3 days, 242 settled): -$1.27 at 70.2% win vs the 8-ct era's
+$0.209/trade at 75.1% — inside noise (daily sd ~$60), so it neither
confirms nor refutes the 16-ct edge. This step is an operator call made
with that read on the table.

Refuses to run until the cash is actually on shard 2 — the sizing path
would clamp to min(shard, bankroll) anyway, but applying the env before
the money lands would just record an intention as if it were a fact.

Run on the box:  cd /opt/kalshi-15m-bot && ./.venv/bin/python deploy/patch_env_scale_725.py
Then:            systemctl restart kalshi-bot
"""
import sys
from pathlib import Path

NEW_BANKROLL = 725.00
SHARD_FLOOR = 725.00  # refuse unless the shard can actually cover the bankroll

TARGET = {
    "KALSHI_BANKROLL_USD": "725.00",
    "KALSHI_MAX_CONTRACTS": "51",
    "KALSHI_MAX_NOTIONAL_USD": "112.39",
    # unchanged, restated so a drifted env cannot silently move them
    "KALSHI_DEPLOY_PCT": "0.06",
    "KALSHI_MAX_DEPLOY_PCT": "0.15",
    "KALSHI_DAILY_STOP_PCT": "0.35",
    "KALSHI_BOT_MAX_CONTRACTS": "",
    "KALSHI_LIVE_BOTS": "eva_wick",
}

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot_config  # noqa: E402
import kalshi_client  # noqa: E402

shard = int(getattr(bot_config, "KALSHI_EXCHANGE_INDEX", 2))
bal = kalshi_client.get_balance()
shard_usd = None
for row in bal.get("balance_breakdown") or []:
    if int(row.get("exchange_index", -1)) == shard:
        shard_usd = float(row["balance"])
        break
if shard_usd is None:
    sys.exit(f"ABORT: could not read shard-{shard} balance from {bal!r}")
print(f"shard-{shard} balance: ${shard_usd:,.2f}")
if shard_usd < SHARD_FLOOR:
    sys.exit(
        f"ABORT: shard-{shard} holds ${shard_usd:,.2f} < ${SHARD_FLOOR:,.2f} — "
        "the $500 deposit has not landed (or was not shard-transferred). "
        "Deposit first, then deploy/shard_transfer.sh, then rerun."
    )

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
    if line.split("=", 1)[0].strip() in TARGET:
        print("  " + line)
print("Now: systemctl restart kalshi-bot")
