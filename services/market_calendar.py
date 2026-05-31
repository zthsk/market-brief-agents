from __future__ import annotations

from datetime import date


def is_nyse_trading_day(value: date | None = None) -> bool:
    target = value or date.today()
    if target.weekday() >= 5:
        return False
    try:
        import pandas_market_calendars as mcal
    except ModuleNotFoundError:
        return True
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=target.isoformat(), end_date=target.isoformat())
    return not schedule.empty
