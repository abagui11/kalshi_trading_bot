#!/usr/bin/env python3
"""Post-deploy assertions for the altcoin shadow books. Read-only.

Run on the box after a deploy (and after the env flip) to confirm the three
things that actually matter: the clones are registered, they cannot route a
live order whatever the env says, and the live book has not been handed a
series it was never measured on.

    /opt/kalshi-15m-bot/.venv/bin/python deploy/_alt_wick_verify.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/kalshi-15m-bot")

import bot_config  # noqa: E402
import config  # noqa: E402
from strategies.registry import (  # noqa: E402
    _registry as registry,
    enabled_strategies,
    handles_series,
)

ALT = sorted(bot_config.ALT_WICK_VARIANTS)
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


print("env")
print(f"  KALSHI_SERIES     = {','.join(config.KALSHI_SERIES)}")
print(f"  ENABLED_BOTS      = {','.join(bot_config.ENABLED_BOTS)}")
print(f"  KALSHI_LIVE_BOTS  = {','.join(config.KALSHI_LIVE_BOTS) or '(empty)'}")
print(f"  active_series()   = {','.join(bot_config.active_series())}")

print("\nregistration")
reg = registry()
for bot_id in ALT:
    check(f"{bot_id} registered", bot_id in reg)
enabled = {s.bot_id for s in enabled_strategies()}
for bot_id in ALT:
    check(f"{bot_id} enabled", bot_id in enabled)

print("\nnever-live guard")
for bot_id in ALT:
    check(f"bot_is_live({bot_id}) is False", bot_config.bot_is_live(bot_id) is False)
# The guard has to hold even if an operator puts a clone in the whitelist.
saved = config.KALSHI_LIVE_BOTS
try:
    config.KALSHI_LIVE_BOTS = tuple(ALT) + ("eva_wick",)
    for bot_id in ALT:
        check(
            f"{bot_id} stays paper even when whitelisted",
            bot_config.bot_is_live(bot_id) is False,
        )
finally:
    config.KALSHI_LIVE_BOTS = saved
check("eva_wick still live", bot_config.bot_is_live("eva_wick") is True)

print("\nseries routing")
for bot_id in ("eva_wick", "eva_streak", "eva_arb"):
    strat = reg.get(bot_id)
    if strat is None:
        continue
    leaked = [s for s in bot_config.ALT_WICK_VARIANTS.values()
              if handles_series(strat, s)]
    check(f"{bot_id} sees no altcoin series", not leaked, ",".join(leaked))
    check(
        f"{bot_id} still sees {config.KALSHI_SERIES[0]}",
        handles_series(strat, config.KALSHI_SERIES[0]),
    )
for bot_id, series in bot_config.ALT_WICK_VARIANTS.items():
    strat = reg[bot_id]
    check(f"{bot_id} sees {series}", handles_series(strat, series))
    others = [s for s in config.KALSHI_SERIES if handles_series(strat, s)]
    check(f"{bot_id} sees no core series", not others, ",".join(others))

print("\nrule parity with the live book")
wick = reg["eva_wick"]
for bot_id in ALT:
    check(
        f"{bot_id} shares decide() with eva_wick",
        reg[bot_id].decide.__func__ is wick.decide.__func__,
    )

print()
if failures:
    print(f"FAILED ({len(failures)}): " + "; ".join(failures))
    raise SystemExit(1)
print("all checks passed")
