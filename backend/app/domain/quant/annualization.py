"""Shared statistics for the stochastic-process model family
(black_scholes_gbm.py, heston.py) — see plans/quant_lab_phase7_8.md
section 1.1. These models store/display ANNUALIZED parameters (matching
how Black-Scholes/Heston are quoted everywhere else), unlike
linear_regression.py/time_series_ar.py which work in raw daily log-return
units — a deliberate, local convention difference, not an inconsistency."""

from __future__ import annotations

import numpy as np

TRADING_DAYS_PER_YEAR = 252


def log_returns(closes: list[float]) -> np.ndarray:
    prices = np.array(closes, dtype=float)
    if np.any(prices <= 0):
        raise ValueError("closes must be strictly positive")
    return np.diff(np.log(prices))


def annualized_mean_and_std(returns: np.ndarray) -> tuple[float, float]:
    """(mu, sigma), both annualized: mu = mean(returns) * 252,
    sigma = std(returns) * sqrt(252)."""
    mu = float(np.mean(returns)) * TRADING_DAYS_PER_YEAR
    sigma = float(np.std(returns)) * np.sqrt(TRADING_DAYS_PER_YEAR)
    return mu, sigma


def annualized_variance(returns: np.ndarray) -> float:
    """var(returns) * 252 — the building block for GBM's sigma^2 and
    Heston's v0/theta."""
    return float(np.var(returns)) * TRADING_DAYS_PER_YEAR
