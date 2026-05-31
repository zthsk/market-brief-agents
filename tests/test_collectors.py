from datetime import datetime

import pandas as pd

from models.database import init_db, query
from services.market_data import collect_history, collect_news, collect_prices
from services.sec_filings import collect_filings


def test_collectors_handle_empty_or_unconfigured_inputs(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    assert collect_prices([]) == 0
    assert collect_news([]) == 0
    assert collect_filings([]) == 0


def test_collect_history_upserts_multiple_days(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()

    class FakeTicker:
        fast_info = {"market_cap": 1_000_000_000}

        def history(self, **kwargs):
            return pd.DataFrame(
                {
                    "Open": [10, 11, 12],
                    "High": [11, 12, 13],
                    "Low": [9, 10, 11],
                    "Close": [10, 12, 11],
                    "Volume": [1000, 1200, 1500],
                },
                index=pd.to_datetime(
                    [datetime(2026, 5, 27), datetime(2026, 5, 28), datetime(2026, 5, 29)]
                ),
            )

    class FakeYFinance:
        @staticmethod
        def Ticker(ticker):
            return FakeTicker()

    monkeypatch.setitem(__import__("sys").modules, "yfinance", FakeYFinance)

    assert collect_history(["AAPL"], period="6mo") == 3
    assert collect_history(["AAPL"], period="6mo") == 3
    rows = query("SELECT date, close, change_percent FROM daily_prices ORDER BY date")
    assert len(rows) == 3
    assert rows[-1]["date"] == "2026-05-29"
    assert round(rows[-1]["change_percent"], 2) == -8.33


def test_collect_history_skips_failed_ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()

    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker
            self.fast_info = {}

        def history(self, **kwargs):
            if self.ticker == "BAD":
                raise RuntimeError("network failure")
            return pd.DataFrame(
                {"Open": [10], "High": [11], "Low": [9], "Close": [10], "Volume": [1000]},
                index=pd.to_datetime([datetime(2026, 5, 29)]),
            )

    class FakeYFinance:
        @staticmethod
        def Ticker(ticker):
            return FakeTicker(ticker)

    monkeypatch.setitem(__import__("sys").modules, "yfinance", FakeYFinance)

    assert collect_history(["BAD", "AAPL"]) == 1
    assert query("SELECT COUNT(*) AS count FROM daily_prices")[0]["count"] == 1
