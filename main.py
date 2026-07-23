"""Entry point: Telegram polling + Kalshi 15m settle/decision + LTF watchdog."""

from __future__ import annotations

import asyncio
import logging
import sys

from bot import build_application
import bot_config
from kalshi_cycle import run_once

logger = logging.getLogger(__name__)


async def kalshi_job(context) -> None:
    """Settle due markets; run decision cycle near window offset."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: run_once(force_decision=False))
    except Exception:
        logger.exception("Kalshi job failed")


async def kalshi_watchdog_job(context) -> None:
    """Deterministic M5 scanner between 15m vision checkpoints."""
    if not bot_config.WATCHDOG_ENABLED:
        return
    loop = asyncio.get_running_loop()

    def _run() -> None:
        from kalshi_watchdog import run_kalshi_watchdog

        run_kalshi_watchdog()

    try:
        await loop.run_in_executor(None, _run)
    except Exception:
        logger.exception("Kalshi watchdog job failed")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = build_application()
    if app.job_queue is None:
        raise RuntimeError("JobQueue unavailable — install python-telegram-bot[job-queue]")

    interval = max(30, int(bot_config.KALSHI_JOB_INTERVAL_SEC))
    app.job_queue.run_repeating(
        kalshi_job,
        interval=interval,
        first=5,
        name="kalshi_cycle",
    )
    wd_interval = max(60, min(300, int(bot_config.WATCHDOG_INTERVAL_SEC)))
    if bot_config.WATCHDOG_ENABLED:
        app.job_queue.run_repeating(
            kalshi_watchdog_job,
            interval=wd_interval,
            first=20,
            name="kalshi_watchdog",
        )
        logger.info(
            "Watchdog enabled every %ss (execute=%s)",
            wd_interval,
            bot_config.watchdog_execute_enabled(),
        )
    logger.info(
        "Starting Kalshi 15m bot (polling + cycle every %ss, paper_only=%s, "
        "broadcast_only_trades=%s)",
        interval,
        bot_config.KALSHI_PAPER_ONLY,
        bot_config.BROADCAST_ONLY_TRADES,
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
