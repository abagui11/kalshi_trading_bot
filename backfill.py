"""CLI to backfill historical OHLC into ohlc.db."""

from __future__ import annotations

import argparse
import json
import logging
import sys

import ohlc_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ETH-USD daily candles into ohlc.db")
    parser.add_argument(
        "--years",
        type=int,
        default=4,
        help="Years of daily history to fetch (default: 4)",
    )
    args = parser.parse_args()

    logger.info("Backfilling %s years of daily candles...", args.years)
    result = ohlc_cache.backfill_daily(years=args.years)
    print(json.dumps(result, indent=2))
    logger.info("Done. %s bars in cache (%s to %s)", result["count"], result["min_ts"], result["max_ts"])


if __name__ == "__main__":
    main()
