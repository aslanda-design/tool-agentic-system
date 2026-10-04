from datetime import date

from app.domain.quant.dates import next_trading_days


def test_next_trading_days_skips_weekends():
    # 2026-09-15 is a Tuesday
    days = next_trading_days(date(2026, 9, 15), 5)
    assert [d.weekday() for d in days] == [2, 3, 4, 0, 1]  # Wed..Fri, Mon, Tue
    assert len(days) == 5


def test_next_trading_days_starts_strictly_after_start():
    days = next_trading_days(date(2026, 9, 15), 1)
    assert days[0] == date(2026, 9, 16)
