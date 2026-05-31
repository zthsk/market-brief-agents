from __future__ import annotations

from typing import Iterable

from models.database import execute_many
from services.logging_utils import get_logger


LOGGER = get_logger(__name__)


def collect_earnings(tickers: Iterable[str]) -> int:
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            calendar = yf.Ticker(ticker).calendar
        except Exception as exc:
            LOGGER.warning("Skipping %s earnings after yfinance error: %s", ticker, exc)
            calendar = None
        if calendar is None or getattr(calendar, "empty", True):
            continue
        for earnings_date, item in calendar.iterrows():
            rows.append(
                {
                    "ticker": ticker,
                    "earnings_date": str(
                        earnings_date.date() if hasattr(earnings_date, "date") else earnings_date
                    ),
                    "eps_actual": None,
                    "eps_estimate": _first_present(item, ["Earnings Average", "EPS Estimate"]),
                    "revenue_actual": None,
                    "revenue_estimate": _first_present(item, ["Revenue Average", "Revenue Estimate"]),
                }
            )
    stored = execute_many(
        """
        INSERT INTO earnings (
            ticker, earnings_date, eps_actual, eps_estimate, revenue_actual, revenue_estimate
        )
        VALUES (
            :ticker, :earnings_date, :eps_actual, :eps_estimate,
            :revenue_actual, :revenue_estimate
        )
        ON CONFLICT(ticker, earnings_date) DO UPDATE SET
            eps_estimate=excluded.eps_estimate,
            revenue_estimate=excluded.revenue_estimate
        """,
        rows,
    )
    LOGGER.info("Stored %s earnings rows from %s fetched calendar rows.", stored, len(rows))
    return stored


def _first_present(row, keys: list[str]):
    for key in keys:
        try:
            value = row.get(key)
            if value == value:
                return float(value)
        except Exception:
            pass
    return None
