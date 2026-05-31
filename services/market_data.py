from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from models.database import query, upsert_news, upsert_prices
from services.logging_utils import get_logger


LOGGER = get_logger(__name__)


def company_tickers(limit: int | None = None) -> list[str]:
    sql = "SELECT ticker FROM companies ORDER BY ticker"
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    return [row["ticker"] for row in query(sql, params)]


def universe_tickers(universes: Iterable[str], limit: int | None = None) -> list[str]:
    universe_values = list(dict.fromkeys(universes))
    if not universe_values:
        return []
    placeholders = ",".join("?" for _ in universe_values)
    sql = f"""
        SELECT DISTINCT ticker
        FROM universe_memberships
        WHERE universe IN ({placeholders})
        ORDER BY ticker
    """
    params: list = list(universe_values)
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [row["ticker"] for row in query(sql, params)]


def collect_prices(tickers: Iterable[str]) -> int:
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            yf_ticker = yf.Ticker(ticker)
            history = yf_ticker.history(period="3mo", interval="1d", auto_adjust=False, timeout=20)
            if history.empty:
                LOGGER.warning("Skipping %s prices: yfinance returned no history.", ticker)
                continue
            latest = history.iloc[-1]
            previous_close = history.iloc[-2]["Close"] if len(history) > 1 else latest["Close"]
            close = float(latest["Close"])
            change_percent = (
                ((close - float(previous_close)) / float(previous_close)) * 100
                if previous_close
                else 0
            )
            volume = int(latest.get("Volume", 0) or 0)
            average_volume = int(history["Volume"].tail(30).mean() or 0)
            info = {}
            try:
                info = yf_ticker.fast_info or {}
            except Exception as exc:
                LOGGER.warning("Skipping %s market cap lookup: %s", ticker, exc)
        except Exception as exc:
            LOGGER.warning("Skipping %s prices after yfinance error: %s", ticker, exc)
            continue
        market_cap = _read_info_number(info, "market_cap") or _read_info_number(info, "marketCap")
        rows.append(
            {
                "ticker": ticker,
                "date": _history_date(latest.name),
                "open": _float_or_none(latest.get("Open")),
                "high": _float_or_none(latest.get("High")),
                "low": _float_or_none(latest.get("Low")),
                "close": close,
                "volume": volume,
                "average_volume": average_volume,
                "current_price": close,
                "change_percent": change_percent,
                "market_cap": market_cap,
            }
        )
    stored = upsert_prices(rows)
    LOGGER.info("Stored %s price rows from %s tickers.", stored, len(rows))
    return stored


def collect_history(tickers: Iterable[str], period: str = "6mo") -> int:
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            yf_ticker = yf.Ticker(ticker)
            history = yf_ticker.history(period=period, interval="1d", auto_adjust=False, timeout=20)
            if history.empty:
                LOGGER.warning("Skipping %s history: yfinance returned no rows.", ticker)
                continue
            info = {}
            try:
                info = yf_ticker.fast_info or {}
            except Exception as exc:
                LOGGER.warning("Skipping %s market cap lookup: %s", ticker, exc)
            market_cap = _read_info_number(info, "market_cap") or _read_info_number(info, "marketCap")
        except Exception as exc:
            LOGGER.warning("Skipping %s history after yfinance error: %s", ticker, exc)
            continue

        volumes = history["Volume"].fillna(0)
        for index, (_, item) in enumerate(history.iterrows()):
            close = _float_or_none(item.get("Close"))
            if close is None:
                continue
            previous_close = (
                _float_or_none(history.iloc[index - 1].get("Close")) if index > 0 else close
            )
            average_volume = int(volumes.iloc[max(0, index - 29) : index + 1].mean() or 0)
            rows.append(
                {
                    "ticker": ticker,
                    "date": _history_date(item.name),
                    "open": _float_or_none(item.get("Open")),
                    "high": _float_or_none(item.get("High")),
                    "low": _float_or_none(item.get("Low")),
                    "close": close,
                    "volume": int(item.get("Volume", 0) or 0),
                    "average_volume": average_volume,
                    "current_price": close,
                    "change_percent": _change_percent(close, previous_close),
                    "market_cap": market_cap,
                }
            )
    stored = upsert_prices(rows)
    LOGGER.info("Stored %s historical price rows from %s fetched rows.", stored, len(rows))
    return stored


def collect_news(tickers: Iterable[str]) -> int:
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            items = yf.Ticker(ticker).news or []
        except Exception as exc:
            LOGGER.warning("Skipping %s news after yfinance error: %s", ticker, exc)
            items = []
        for item in items[:10]:
            content = item.get("content", item)
            title = content.get("title") or item.get("title")
            if not title:
                continue
            provider = content.get("provider") or {}
            published = content.get("pubDate") or item.get("providerPublishTime")
            if isinstance(published, (int, float)):
                published = datetime.fromtimestamp(published, tz=timezone.utc).isoformat()
            rows.append(
                {
                    "ticker": ticker,
                    "published_at": published,
                    "headline": title,
                    "url": content.get("canonicalUrl", {}).get("url") or item.get("link"),
                    "source": provider.get("displayName") or item.get("publisher") or "Yahoo Finance",
                    "summary": content.get("summary") or "",
                }
            )
    stored = upsert_news(rows)
    LOGGER.info("Stored %s news rows from %s fetched items.", stored, len(rows))
    return stored


def _float_or_none(value) -> float | None:
    try:
        if value != value:
            return None
        return float(value)
    except Exception:
        return None


def _change_percent(close: float, previous_close: float | None) -> float:
    if not previous_close:
        return 0
    return ((close - float(previous_close)) / float(previous_close)) * 100


def _history_date(value) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


def _read_info_number(info, key: str) -> float | None:
    try:
        value = info.get(key)
        return float(value) if value else None
    except Exception:
        return None
