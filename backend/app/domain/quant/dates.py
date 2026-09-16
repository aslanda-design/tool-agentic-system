"""Trading-day date arithmetic shared by every model's simulate() — not
bootstrap-specific (moved out of bootstrap.py in plans/quant_lab_phase7_8.md
section 1.2), since the SDE-based models (black_scholes_gbm.py, heston.py)
build their own SimulationResult directly and need this too."""

from __future__ import annotations

from datetime import date, timedelta


def next_trading_days(start: date, n: int) -> list[date]:
    """The next n weekdays strictly after `start` — a deliberate
    simplification (no market-holiday calendar) documented in
    plans/quant_lab.md section 11 as a general limitation, not something
    worth a new dependency for at this stage."""
    days: list[date] = []
    current = start
    while len(days) < n:
        current = current + timedelta(days=1)
        if current.weekday() < 5:  # Monday=0 .. Sunday=6
            days.append(current)
    return days
