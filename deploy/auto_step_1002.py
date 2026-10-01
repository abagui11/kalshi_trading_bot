#!/usr/bin/env python3
"""2026-10-02 automated size step — fired once by a systemd timer.

Operator plan recorded 2026-10-01: step eva_wick 51 -> 100 ct tomorrow
unless today produced evidence to pause; step the SOL clone 25 -> 50 ct
"if it's doing good". This script turns those two sentences into rules
fixed BEFORE the evidence exists, then applies whatever they say:

  eva_wick  -> 100 ct (bankroll 1450, notional 224.75; same proportional
               step as patch_env_scale_1450.py) UNLESS any of:
                 * a UTC day since 10-01 closed at or below -$190 on the
                   live book (~1 sd of a 51-ct day; the daily stop sits
                   further out at -$253.75, so this pauses earlier);
                 * any regime_state tripwire recorded since 10-01;
                 * shard 2 holds less than the new $1,450 bankroll.
  eva_wick_sol -> 50 ct IF its live era (opened since the go-live stamp in
               deploy/.sol_live_start) has >= 10 settled trades AND
               realized P&L >= 0; otherwise it stays at 25. Never above the
               global cap, which is 100 only if the wick step went ahead.

Either verdict is sent to the admin Telegram chat with the numbers behind
it. Idempotent via deploy/.auto_step_1002.done. Dry run: --dry-run.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/opt/kalshi-15m-bot")
ENV = ROOT / ".env"
DONE = ROOT / "deploy" / ".auto_step_1002.done"
SOL_START_FILE = ROOT / "deploy" / ".sol_live_start"
DRY = "--dry-run" in sys.argv

PAUSE_DAY_LOSS_USD = -190.0
SINCE_DAY = "2026-10-01"
NEW_BANKROLL = 1450.00
SOL_MIN_TRADES = 10

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _env_map(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _telegram(text: str) -> None:
    env = _env_map(ENV)
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat:
        print("telegram: no token/admin chat configured")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15
        )
    except Exception as exc:  # the step must not hinge on the alert
        print("telegram send failed:", exc)


if DONE.exists() and not DRY:
    sys.exit(f"already ran: {DONE.read_text().strip()}")

conn = sqlite3.connect(f"file:{ROOT / 'ledger.db'}?mode=ro", uri=True)
days = conn.execute(
    "SELECT substr(closed_at,1,10) d, COUNT(*) n, ROUND(SUM(pnl_usd),2) pnl"
    " FROM paper_positions WHERE bot_id='eva_wick' AND status!='open'"
    " AND closed_at >= ? GROUP BY d ORDER BY d", (SINCE_DAY,),
).fetchall()
trips = conn.execute(
    "SELECT key, value FROM regime_state WHERE value >= ?", (SINCE_DAY,)
).fetchall() if conn.execute(
    "SELECT 1 FROM sqlite_master WHERE name='regime_state'"
).fetchone() else []

sol_start = SOL_START_FILE.read_text().strip() if SOL_START_FILE.exists() else None
sol_n, sol_pnl = (0, 0.0)
if sol_start:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_usd),0) FROM paper_positions"
        " WHERE bot_id='eva_wick_sol' AND status!='open' AND opened_at >= ?",
        (sol_start,),
    ).fetchone()
    sol_n, sol_pnl = int(row[0]), float(row[1])
conn.close()

import bot_config  # noqa: E402
import kalshi_client  # noqa: E402

shard = int(getattr(bot_config, "KALSHI_EXCHANGE_INDEX", 2))
shard_usd = None
for r in kalshi_client.get_balance().get("balance_breakdown") or []:
    if int(r.get("exchange_index", -1)) == shard:
        shard_usd = float(r["balance"])

reasons = []
for d, n, pnl in days:
    if float(pnl) <= PAUSE_DAY_LOSS_USD:
        reasons.append(f"{d} closed at ${pnl:+.2f} (<= ${PAUSE_DAY_LOSS_USD:.0f})")
if trips:
    reasons.append(f"regime tripwire(s): {trips}")
if shard_usd is None or shard_usd < NEW_BANKROLL:
    reasons.append(f"shard-{shard} ${shard_usd} < ${NEW_BANKROLL:,.0f}")
wick_go = not reasons

sol_go = bool(sol_start) and sol_n >= SOL_MIN_TRADES and sol_pnl >= 0
sol_cap = 50 if sol_go else 25

target = {
    "KALSHI_LIVE_BOTS": "eva_wick,eva_wick_sol",
    "KALSHI_BOT_MAX_CONTRACTS": f"eva_wick_sol={sol_cap}",
    "KALSHI_DEPLOY_PCT": "0.06",
    "KALSHI_MAX_DEPLOY_PCT": "0.15",
    "KALSHI_DAILY_STOP_PCT": "0.35",
}
if wick_go:
    target.update({
        "KALSHI_BANKROLL_USD": "1450.00",
        "KALSHI_MAX_CONTRACTS": "100",
        "KALSHI_MAX_NOTIONAL_USD": "224.75",
    })

day_txt = ", ".join(f"{d} {pnl:+.2f} ({n})" for d, n, pnl in days) or "none"
summary = (
    "Kalshi auto size step (10-02)\n"
    f"eva_wick: {'GO 51->100 ct' if wick_go else 'HOLD at 51 ct'}"
    + ("" if wick_go else f" — {'; '.join(reasons)}") + "\n"
    f"  days: {day_txt}; shard-{shard} ${shard_usd:,.2f}\n"
    f"SOL: {'GO 25->50 ct' if sol_go else 'HOLD at 25 ct'} — live since "
    f"{sol_start}: {sol_n} settled, ${sol_pnl:+.2f} "
    f"(needs >= {SOL_MIN_TRADES} and >= $0)"
)
print(summary)
print("target env:", json.dumps(target))
if DRY:
    sys.exit(0)

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy(ENV, ENV.with_name(f".env.bak-autostep-{stamp}"))
lines = ENV.read_text(encoding="utf-8").splitlines()
seen, out = set(), []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key in target and not line.strip().startswith("#"):
        out.append(f"{key}={target[key]}")
        seen.add(key)
    else:
        out.append(line)
out += [f"{k}={v}" for k, v in target.items() if k not in seen]
ENV.write_text("\n".join(out) + "\n", encoding="utf-8")

subprocess.run(["systemctl", "restart", "kalshi-bot"], check=False)
active = subprocess.run(
    ["systemctl", "is-active", "kalshi-bot"], capture_output=True, text=True
).stdout.strip()
DONE.write_text(f"{stamp} wick_go={wick_go} sol_cap={sol_cap}\n")
_telegram(summary + f"\nkalshi-bot after restart: {active}")
print("kalshi-bot:", active)
