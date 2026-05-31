from __future__ import annotations

from io import StringIO

import pandas as pd
import requests

from models.database import upsert_companies, upsert_universe_memberships


SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
DEFAULT_SAMPLE = [
    {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology", "industry": "Consumer Electronics", "market_cap": None},
    {"ticker": "MSFT", "name": "Microsoft Corporation", "sector": "Information Technology", "industry": "Software", "market_cap": None},
    {"ticker": "NVDA", "name": "NVIDIA Corporation", "sector": "Information Technology", "industry": "Semiconductors", "market_cap": None},
    {"ticker": "AMZN", "name": "Amazon.com, Inc.", "sector": "Consumer Discretionary", "industry": "Broadline Retail", "market_cap": None},
    {"ticker": "META", "name": "Meta Platforms, Inc.", "sector": "Communication Services", "industry": "Interactive Media", "market_cap": None},
]


def load_sp500_from_wikipedia() -> list[dict]:
    response = requests.get(
        SP500_WIKI_URL,
        headers={"User-Agent": "Market Brief Agents MVP company universe loader"},
        timeout=20,
    )
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    table = tables[0]
    rows: list[dict] = []
    for _, item in table.iterrows():
        rows.append(
            {
                "ticker": str(item["Symbol"]).replace(".", "-"),
                "name": str(item["Security"]),
                "sector": str(item["GICS Sector"]),
                "industry": str(item["GICS Sub-Industry"]),
                "market_cap": None,
            }
        )
    return rows


def seed_companies(use_sample_on_error: bool = True) -> int:
    try:
        rows = load_sp500_from_wikipedia()
    except Exception:
        if not use_sample_on_error:
            raise
        rows = DEFAULT_SAMPLE
    stored = upsert_companies(rows)
    upsert_universe_memberships(
        [
            {
                "ticker": row["ticker"],
                "universe": "sp500",
                "source": SP500_WIKI_URL,
                "seen_at": None,
            }
            for row in rows
        ]
    )
    return stored


YAHOO_SCREENER_UNIVERSES = {
    "day_gainers": "yahoo_day_gainers",
    "day_losers": "yahoo_day_losers",
    "most_actives": "yahoo_most_actives",
    "small_cap_gainers": "yahoo_small_cap_gainers",
    "most_shorted_stocks": "yahoo_most_shorted",
}


def collect_yahoo_screener_companies(size: int = 100) -> dict[str, int]:
    import yfinance as yf

    companies: dict[str, dict] = {}
    memberships = []
    counts: dict[str, int] = {}
    for query_name, universe in YAHOO_SCREENER_UNIVERSES.items():
        quotes = _yahoo_screen_quotes(yf.screen(query_name, size=size))
        counts[universe] = len(quotes)
        for quote in quotes:
            ticker = _normalize_ticker(quote.get("symbol"))
            if not ticker:
                continue
            companies.setdefault(
                ticker,
                {
                    "ticker": ticker,
                    "name": quote.get("shortName") or quote.get("longName") or ticker,
                    "sector": quote.get("sector"),
                    "industry": quote.get("industry"),
                    "market_cap": quote.get("marketCap"),
                },
            )
            memberships.append(
                {
                    "ticker": ticker,
                    "universe": universe,
                    "source": f"yahoo_screener:{query_name}",
                    "seen_at": None,
                }
            )
    upsert_companies(companies.values())
    upsert_universe_memberships(memberships)
    counts["companies"] = len(companies)
    counts["memberships"] = len(memberships)
    return counts


def _yahoo_screen_quotes(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("quotes"), list):
        return payload["quotes"]
    finance = payload.get("finance")
    if isinstance(finance, dict):
        result = finance.get("result") or []
        if result and isinstance(result[0], dict):
            return result[0].get("quotes") or []
    return []


def _normalize_ticker(value) -> str:
    ticker = str(value or "").strip().upper()
    return ticker.replace(".", "-")
