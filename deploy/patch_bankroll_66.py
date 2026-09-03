#!/usr/bin/env python3
"""Set bankroll env keys to post-loss soak size and reset control paper book."""

from __future__ import annotations

from pathlib import Path

import paper

BANKROLL = "66.10"
reps = {
    "KALSHI_BANKROLL_USD": BANKROLL,
    "PORTFOLIO_VALUE": BANKROLL,
    "PAPER_PORTFOLIO_VALUE": BANKROLL,
}

p = Path(".env")
text = p.read_text(encoding="utf-8")
lines = text.splitlines()
out: list[str] = []
seen: set[str] = set()
for line in lines:
    if (not line.strip()) or line.strip().startswith("#") or ("=" not in line):
        out.append(line)
        continue
    k = line.split("=", 1)[0].strip()
    if k in reps:
        out.append(f"{k}={reps[k]}")
        seen.add(k)
    else:
        out.append(line)
for k, v in reps.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("bankroll patched to", BANKROLL)
for k, v in reps.items():
    print(f"  {k}={v}")

# Align control paper book to the new soak size (fresh product path).
paper.reset_book(float(BANKROLL), bot_id="control")
print("control paper:", paper.get_stats(bot_id="control"))
print("adverse paper (unchanged):", {
    k: paper.get_stats(bot_id="adverse").get(k)
    for k in ("equity_usd", "realized_pnl_usd", "closed_count")
})
