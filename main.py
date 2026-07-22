"""Entry point: Telegram polling + Kalshi 15m settle/decision job."""

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
    logger.info(
        "Starting Kalshi 15m bot (polling + cycle every %ss, paper_only=%s)",
        interval,
        bot_config.KALSHI_PAPER_ONLY,
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
