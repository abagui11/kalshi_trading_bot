"""Dry-run the altcoin wick clones against the live books. Writes nothing.

Prints, for each altcoin series, the current quote, the spread, the depth at
the touch on the favourite side, and exactly what the clone would do with it.
Runs against a throwaway ledger, so it is safe to run while the bot is live.

    python deploy/_alt_wick_probe.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The Windows console defaults to cp1252 and this prints cents signs.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bot_config  # noqa: E402
import config  # noqa: E402
import kalshi_client  # noqa: E402
import kalshi_triggers  # noqa: E402
import paper  # noqa: E402
from kalshi_cycle import build_shared_context  # noqa: E402
from strategies.eva_wick_alt import alt_wick_strategies  # noqa: E402


def main() -> None:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    config.LEDGER_DB = Path(tmp.name) / "probe.db"
    paper.init_db()

    print(f"band {bot_config.EVA_FAV_MIN_ENTRY_CENTS:.0f}-"
          f"{bot_config.EVA_FAV_MAX_ENTRY_CENTS:.0f}¢ on the mid, "
          f"pay up to {bot_config.EVA_FAV_MAX_PAY_CENTS:.0f}¢, "
          f"{bot_config.EVA_FAV_MIN_SECONDS_LEFT / 60:.0f}-"
          f"{bot_config.EVA_FAV_MAX_SECONDS_LEFT / 60:.0f} min left\n")

    for strat in alt_wick_strategies():
        series = strat.series_filter[0]
        markets = kalshi_client.get_open_markets(series)
        if not markets:
            print(f"{strat.asset:5s} no open market")
            continue
        market = markets[0]
        ctx = build_shared_context(series, market, near_decision=True)
        mid = ctx.yes_mid_cents
        left = kalshi_triggers.minutes_to_expiry(ctx.expiry_ts, now=ctx.clock())
        if mid is None or left is None:
            print(f"{strat.asset:5s} {ctx.market_ticker} — no quote")
            continue

        side = "YES" if mid >= 50.0 else "NO"
        side_mid = kalshi_triggers.side_mid_cents(side, mid)
        ask = kalshi_client.side_ask_cents_from_market(side, market)
        depth = kalshi_client.side_ask_depth_contracts(side, market)
        spread = kalshi_client.spread_cents_from_market(market)
        print(
            f"{strat.asset:5s} {ctx.market_ticker:26s} {left:5.1f}m left  "
            f"fav {side} mid {side_mid:5.1f}¢ ask "
            f"{'  n/a' if ask is None else f'{ask:5.1f}'}¢  "
            f"spread {'n/a' if spread is None else f'{spread:.1f}'}¢  "
            f"depth {'n/a' if depth is None else f'{depth:,.0f}'} ct"
        )

        # What a clip would actually cost right now, rule or no rule. This is
        # the fill model the shadow books use, so it is worth seeing on every
        # tick and not only on the windows that happen to qualify.
        if ask is not None:
            ladder = kalshi_client.get_ask_ladder(side, ctx.market_ticker)
            want = int(bot_config.KALSHI_MAX_CONTRACTS)
            limit = ask + float(bot_config.KALSHI_LIVE_TAKE_CENTS)
            if ladder:
                got, vwap = kalshi_client.simulate_ioc_fill(ladder, want, limit)
                if got:
                    print(f"      a {want} ct IOC at max {limit:.1f}¢ fills "
                          f"{got} ct at {vwap:.2f}¢ "
                          f"({vwap - ask:+.2f}¢ vs the touch), "
                          f"{len(ladder)} levels on the book")
                else:
                    print(f"      a {want} ct IOC at max {limit:.1f}¢ "
                          f"fills nothing")
            else:
                print("      ladder unavailable")

        sug = strat.decide(ctx)
        if sug is None:
            print("      -> no entry\n")
            continue
        arm = paper.get_window_arm(strat.bot_id, ctx.market_ticker) or {}
        print(
            f"      -> BUY {sug.side} x{sug.contracts} @ {sug.entry_cents:.2f}¢ "
            f"(${sug.contracts * sug.entry_cents / 100:.2f})\n"
            f"         {arm.get('meta_json')}\n"
        )

    tmp.cleanup()


if __name__ == "__main__":
    main()
