"""Roham-owned hypothetical offline simulation; no live execution capability."""

from .engine import (
    BacktestResult, BacktestSummary, EquityPoint, SimulatedFill, SimulationConfig, run_backtest,
)

__all__ = ["BacktestResult", "BacktestSummary", "EquityPoint", "SimulatedFill",
           "SimulationConfig", "run_backtest"]
