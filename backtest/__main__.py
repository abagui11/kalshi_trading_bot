"""CLI: python -m backtest --days 7 --bots lottery --product BTC"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kalshi 15m strategy backtest (synthetic fair mid + spot settle)."
    )
    parser.add_argument("--days", type=float, default=7.0, help="History length in days")
    parser.add_argument(
        "--product",
        default="BTC",
        choices=("BTC", "ETH"),
        help="Underlying product",
    )
    parser.add_argument(
        "--bots",
        default="lottery",
        help="Comma-separated bot ids: lottery,adverse,control",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional isolated ledger path (default: temp file)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="Optional kalshi_snapshots.db for mid/result fidelity",
    )
    parser.add_argument(
        "--biases",
        type=Path,
        default=None,
        help="Optional JSON of recorded ICT biases keyed by market_ticker",
    )
    parser.add_argument(
        "--exports",
        type=Path,
        default=Path("exports"),
        help="Directory for summary/trades CSV",
    )
    parser.add_argument(
        "--starting-usd",
        type=float,
        default=None,
        help="Override starting bankroll per bot",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    from backtest.runner import run_backtest

    bots = [b.strip() for b in str(args.bots).split(",") if b.strip()]
    result = run_backtest(
        product=args.product,
        days=args.days,
        bots=bots,
        db_path=args.db,
        archive_path=args.archive,
        bias_path=args.biases,
        exports_dir=args.exports,
        starting_usd=args.starting_usd,
    )

    print(f"windows={result.windows} ticks={result.ticks} decisions={result.decisions}")
    print(f"ledger={result.db_path}")
    for s in result.summaries:
        print(
            f"[{s['bot_id']}] equity=${s['equity_usd']:.2f} "
            f"pnl=${s['realized_pnl_usd']:+.2f} "
            f"wr={s['win_rate']*100:.0f}% "
            f"({s['wins']}W/{s['losses']}L) closed={s['closed_count']}"
        )
    if result.summary_csv:
        print(f"summary_csv={result.summary_csv}")
    if result.trades_csv:
        print(f"trades_csv={result.trades_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
