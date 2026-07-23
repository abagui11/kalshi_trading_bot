"""Backtest harness (deferred).

Live multi-bot paper experiments ship first. When ready to build historical
replay, reuse the same Strategy interface:

    from strategies.registry import enabled_strategies
    from strategies.context import SharedCycleContext

    for window in replay_windows:
        ctx = SharedCycleContext(...)  # from archived M5 + Kalshi mid/result
        for strat in enabled_strategies():
            sug = strat.decide(ctx)
            # settle into an isolated paper DB per bot_id

Prefer deterministic strategies (lottery, adverse) before replaying LLM control
bias (record SharedHtfBias snapshots, or use a fair-only stub).

Do not implement the runner here yet — this module is a placeholder so the
interface stays backtest-ready.
"""

from __future__ import annotations

# Intentionally empty until Phase 4.
