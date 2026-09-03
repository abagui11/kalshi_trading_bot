"""Offline backtest harness tests (synthetic M5 bars, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest.archive import init_archive, mid_as_of, record_snapshot, result_for
from backtest.data import build_windows, settle_result_spot_vs_strike
from backtest.runner import run_backtest
from backtest.strategies_ext import rule_htf_from_returns


def _synthetic_bars(
    *,
    start: datetime,
    n: int = 48,
    open_px: float = 100_000.0,
) -> list[dict]:
    """Deterministic rising-then-falling M5 series."""
    bars = []
    px = open_px
    for i in range(n):
        ts = start + timedelta(minutes=5 * i)
        # Mild drift + a few sweeps for lottery hail-mary paths.
        high = px * 1.001
        low = px * 0.999
        if i % 7 == 0:
            low = px * 0.997  # sweep low
        close = px * (1.0002 if i % 2 == 0 else 0.9998)
        bars.append(
            {
                "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": px,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
            }
        )
        px = close
    return bars


def test_build_windows_and_settle():
    start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    bars = _synthetic_bars(start=start, n=36)
    windows = build_windows(bars, product_id="BTC")
    assert len(windows) >= 2
    w = windows[0]
    assert w.strike > 0
    assert (w.expiry_ts - w.open_ts) == timedelta(minutes=15)
    assert settle_result_spot_vs_strike(w.strike + 1, w.strike) == "yes"
    assert settle_result_spot_vs_strike(w.strike - 1, w.strike) == "no"


def test_rule_htf_from_returns():
    bear = rule_htf_from_returns(-0.5)
    assert bear.side == "NO"
    assert bear.htf_bias == "bear"
    bull = rule_htf_from_returns(0.5)
    assert bull.side == "YES"
    flat = rule_htf_from_returns(0.01)
    assert flat.side is None


def test_archive_mid_and_result(tmp_path: Path):
    db = tmp_path / "snaps.db"
    init_archive(db)
    record_snapshot(
        db,
        ts="2026-07-01T12:00:00Z",
        series="KXBTC15M",
        market_ticker="KXBTC15M-TEST",
        yes_mid_cents=42.0,
        spot=100_000.0,
        strike=99_900.0,
    )
    record_snapshot(
        db,
        ts="2026-07-01T12:10:00Z",
        series="KXBTC15M",
        market_ticker="KXBTC15M-TEST",
        yes_mid_cents=55.0,
        result="yes",
    )
    when = datetime(2026, 7, 1, 12, 5, tzinfo=timezone.utc)
    assert mid_as_of(db, "KXBTC15M-TEST", when) == 42.0
    assert result_for(db, "KXBTC15M-TEST") == "yes"


def test_run_backtest_lottery_offline(tmp_path: Path):
    start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    bars = _synthetic_bars(start=start, n=72)
    db = tmp_path / "bt.db"
    exports = tmp_path / "exports"
    result = run_backtest(
        product="BTC",
        bots=["lottery"],
        bars=bars,
        db_path=db,
        exports_dir=exports,
        starting_usd=100.0,
    )
    assert result.windows > 0
    assert result.ticks > 0
    assert result.summary_csv is not None and result.summary_csv.exists()
    assert result.trades_csv is not None and result.trades_csv.exists()
    assert len(result.summaries) == 1
    assert result.summaries[0]["bot_id"] == "lottery"
    assert result.summaries[0]["starting_usd"] == 100.0


def test_run_backtest_adverse_control_offline(tmp_path: Path):
    start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    bars = _synthetic_bars(start=start, n=72)
    result = run_backtest(
        product="BTC",
        bots=["adverse", "control"],
        bars=bars,
        db_path=tmp_path / "bt2.db",
        exports_dir=tmp_path / "exports",
        starting_usd=100.0,
    )
    assert {s["bot_id"] for s in result.summaries} == {"adverse", "control"}
    assert result.ticks > 0
