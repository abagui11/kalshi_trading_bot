#!/usr/bin/env python3
"""Enable the XRP/SOL/HYPE wick shadow books (2026-09-30). Paper only.

Adds three paper clones of the live favourite rule to ENABLED_BOTS. They are
the same strategy class with a different bot_id and series, so the band, the
clock and the pay-the-ask mechanic cannot drift from the live book.

What this changes on the box:

* The cycle polls five Kalshi series per tick instead of two, and builds spot
  context for three more Coinbase products. No extra Claude spend — these
  bots set needs_htf_bias=False, same as the live wick.
* Three more paper_state rows appear, each starting at KALSHI_BANKROLL_USD.
* Telegram is unchanged: PAPER_ONLY_BOTS are suppressed from broadcast, so
  the live channel does not get three more books of noise.

What this does NOT change: KALSHI_LIVE_BOTS stays eva_wick, and the clones
are in bot_config.PAPER_ONLY_BOTS, which short-circuits bot_is_live before
the env is consulted at all. Adding them to the live whitelist by hand would
still not route an order.

Rollback: drop the three ids from ENABLED_BOTS and restart. Their rows stay
in the ledger and stay queryable.
"""
from pathlib import Path

ALT_BOTS = ("eva_wick_xrp", "eva_wick_sol", "eva_wick_hype")

p = Path("/opt/kalshi-15m-bot/.env")
lines = p.read_text(encoding="utf-8").splitlines()

out = []
enabled_seen = False
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key == "ENABLED_BOTS" and not line.strip().startswith("#"):
        enabled_seen = True
        current = [
            s.strip() for s in line.split("=", 1)[1].split(",") if s.strip()
        ]
        for bot in ALT_BOTS:
            if bot not in current:
                current.append(bot)
        out.append("ENABLED_BOTS=" + ",".join(current))
    elif key == "KALSHI_LIVE_BOTS" and not line.strip().startswith("#"):
        # Restated, so a drifted env cannot silently widen the live set.
        out.append("KALSHI_LIVE_BOTS=eva_wick")
    else:
        out.append(line)

if not enabled_seen:
    raise SystemExit("ENABLED_BOTS not found in .env — refusing to guess")

p.write_text("\n".join(out) + "\n", encoding="utf-8")

print("=== bot env now ===")
for line in out:
    if line.split("=", 1)[0].strip() in (
        "ENABLED_BOTS", "KALSHI_LIVE_BOTS", "KALSHI_SERIES",
        "KALSHI_MAX_CONTRACTS", "KALSHI_BANKROLL_USD",
    ):
        print("  " + line)
print("\nrestart:  systemctl restart kalshi-bot")
print("verify :  .venv/bin/python deploy/_alt_wick_verify.py")
