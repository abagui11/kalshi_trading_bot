#!/usr/bin/env python3
"""Bankroll $725 -> $1,450 and 51 -> 100 contracts (staged 2026-10-01, for 2026-10-02).

Operator call, staged the day before: $1,750 moved from the Coinbase
account to the Kalshi shard, bringing it to ~$2.5K (Coinbase execution
went paper-only in the hub repo the same day — every remaining live-entry
switch off). The explicit plan, recorded at staging time: keep trading at
51 ct through 2026-10-01, then step to 100 ct on 2026-10-02 **unless the
10-01 session produced evidence to pause** — this script is the step, not
the decision. Read the evidence first; the P&L print below is there for
exactly that.

Everything scales by the same ~2x so every ratio the 51-ct record was
built on is preserved (same discipline as the 09-28-noted, 10-01-shipped
225 -> 725 step — contracts and bankroll move together or the stop quietly
changes meaning):

  KALSHI_BANKROLL_USD      725.00 -> 1450.00  (2.00x; the shard holds ~$2.53K
                                               after the deposit — the ~$1.08K
                                               above the bankroll is deliberate
                                               cushion, pre-positioned for the
                                               NEXT step rather than sized into
                                               this one, because bankroll and
                                               contracts move together or the
                                               stop quietly changes meaning)
  KALSHI_MAX_CONTRACTS         51 -> 100      (1.96x — the user-stated target;
                                               bankroll's 2.00x slightly
                                               overshoots it, which errs small)
  KALSHI_MAX_NOTIONAL_USD  112.39 -> 224.75   (same 15.5%-of-bankroll ratio)
  KALSHI_DEPLOY_PCT          0.06 unchanged   (0.06 x 1450 = $87.00 clears
                                               100 ct at the 82c max-pay = $82)
  KALSHI_MAX_DEPLOY_PCT      0.15 unchanged
  KALSHI_DAILY_STOP_PCT      0.35 unchanged   — and genuinely unchanged in
    risk terms again: 0.35 x 1450 = $507.50 vs a daily sd of
    ~$59.66 x 100/16 = ~$373, i.e. ~1.36 sd — the same ~1.33-1.36 multiple
    every step since 09-28 has maintained.

Evidence on the table at staging time (51-ct era, live eva_wick, UTC days):
09-29 +$51.72 (77 settled), 09-30 +$52.81 (84), 10-01 -$12.99 through
17:45Z (58) — no stop trips, no regime tripwires, daily stop armed at
$253.75 and never approached. The script re-prints the latest read when
run so tomorrow's go/no-go uses tomorrow's numbers, not these.

Refuses to run until shard 2 actually covers the new bankroll — applying
the env before the $1,750 lands would record an intention as if it were a
fact. Run `deploy/reseed_live_book_1450.py` (with the bot stopped) for the
ledger-book side of the deposit, and remember the hub-side display seed:
`dashboard/edge_analytics.py` KALSHI_SEEDS_USD eva_wick 746.75 -> 2496.75.

Run on the box:  cd /opt/kalshi-15m-bot && ./.venv/bin/python deploy/patch_env_scale_1450.py
Then:            systemctl restart kalshi-bot
"""
import sqlite3
import sys
from pathlib import Path

NEW_BANKROLL = 1450.00
SHARD_FLOOR = 1450.00  # refuse unless the shard can actually cover the bankroll

TARGET = {
    "KALSHI_BANKROLL_USD": "1450.00",
    "KALSHI_MAX_CONTRACTS": "100",
    "KALSHI_MAX_NOTIONAL_USD": "224.75",
    # unchanged, restated so a drifted env cannot silently move them
    "KALSHI_DEPLOY_PCT": "0.06",
    "KALSHI_MAX_DEPLOY_PCT": "0.15",
    "KALSHI_DAILY_STOP_PCT": "0.35",
    # SOL clone went live 2026-10-01 at 25 ct; its 25 -> 50 step is decided
    # by deploy/auto_step_1002.py (the primary path). This manual fallback
    # keeps SOL where it is rather than wiping the whitelist and cap.
    "KALSHI_BOT_MAX_CONTRACTS": "eva_wick_sol=25",
    "KALSHI_LIVE_BOTS": "eva_wick,eva_wick_sol",
}

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot_config  # noqa: E402
import kalshi_client  # noqa: E402

# --- the evidence read, fresh at run time, before anything is written ------
conn = sqlite3.connect("file:/opt/kalshi-15m-bot/ledger.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
print("=== live eva_wick, last 4 UTC days (read before stepping) ===")
for r in conn.execute(
    "SELECT substr(closed_at,1,10) d, COUNT(*) n, ROUND(SUM(pnl_usd),2) pnl,"
    " SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) w"
    " FROM paper_positions WHERE bot_id='eva_wick' AND status!='open'"
    " GROUP BY d ORDER BY d DESC LIMIT 4"
):
    print(f"  {r['d']}: {r['pnl']:+.2f} over {r['n']} settled ({r['w']}W)")
trips = conn.execute("SELECT COUNT(*) n FROM regime_state").fetchone()["n"]
print(f"  regime_state trips on record: {trips}")
conn.close()

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
        "the $1,750 deposit has not landed (or was not shard-transferred). "
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
