"""Pre-start sanity check for the VPS deployment (piped to remote python)."""
import os

os.chdir("/opt/kalshi-15m-bot")

import bot_config  # noqa: E402
import eva_intel  # noqa: E402
from strategies.registry import enabled_strategies  # noqa: E402

print("bots:", [x.bot_id for x in enabled_strategies()])
print(
    "paper:", bot_config.KALSHI_PAPER_ONLY,
    "watchdog:", bot_config.WATCHDOG_ENABLED,
    "macro:", bot_config.MACRO_CONTEXT_ENABLED,
    "only_trades:", bot_config.BROADCAST_ONLY_TRADES,
)
stances = eva_intel.get_stances("BTC-USD")
print("stances:", None if stances is None else {k: v["stance"] for k, v in stances.items()})
