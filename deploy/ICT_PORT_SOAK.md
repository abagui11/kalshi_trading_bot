# Kalshi ICT port — soak checklist

After deploying this ICT port, verify on paper:

1. Dashboard `/` shows Market structure (HTF bias + watching) with no open trade.
2. Ten empty 15m windows → ten SKIP journal rows with tags/rationale; Telegram received all (BROADCAST_ONLY_TRADES=false).
3. Trade/skip cards show H4 structure + M5 entry chart when charts rendered.
4. Critic: fake OB / counter-HTF without acknowledgment → retry or downgrade.
5. Watchdog with `WATCHDOG_EXECUTE_ENABLED=false` → ledger + Telegram shadow, no fill.
6. Flip execute on → paper fill from fib/SFP without vision LLM call.

Config defaults:
- WATCHDOG_ENABLED=true, WATCHDOG_EXECUTE_ENABLED=true
- BROADCAST_ONLY_TRADES=false (trades + skips always to Telegram)
- KALSHI_RUN_LLM_CRITIC=true
