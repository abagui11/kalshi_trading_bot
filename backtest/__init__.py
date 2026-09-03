"""Kalshi 15m backtest harness — deterministic replay of Strategy.decide()."""

from __future__ import annotations

from backtest.runner import BacktestResult, run_backtest

__all__ = ["BacktestResult", "run_backtest"]
