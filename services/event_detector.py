from __future__ import annotations

from datetime import date

from models.database import query, upsert_event
from services.logging_utils import get_logger


EVENT_THRESHOLD = 60
LOGGER = get_logger(__name__)


def score_story(row: dict, news_count: int, has_new_8k: bool) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    change = abs(float(row.get("change_percent") or 0))
    volume = int(row.get("volume") or 0)
    average_volume = int(row.get("average_volume") or 0)
    market_cap = float(row.get("market_cap") or 0)

    if change >= 10:
        score += 50
        reasons.append(f"{change:.1f}% price move")
    elif change >= 5:
        score += 30
        reasons.append(f"{change:.1f}% price move")

    if average_volume and volume > average_volume * 2:
        score += 20
        reasons.append("volume above 2x average")

    if market_cap > 10_000_000_000:
        score += 10
        reasons.append("market cap above $10B")

    if news_count > 1:
        score += 20
        reasons.append(f"{news_count} recent headlines")

    if has_new_8k:
        score += 15
        reasons.append("new 8-K filing")

    return score, reasons


def detect_events() -> int:
    today = date.today().isoformat()
    prices = query(
        """
        SELECT *
        FROM daily_prices
        WHERE date = (SELECT MAX(date) FROM daily_prices)
        """
    )
    detected = 0
    for price in prices:
        ticker = price["ticker"]
        event_date = price.get("date") or today
        news_count = query(
            "SELECT COUNT(*) AS count FROM news WHERE ticker = ?",
            (ticker,),
        )[0]["count"]
        has_new_8k = bool(
            query(
                """
                SELECT 1 FROM sec_filings
                WHERE ticker = ? AND filing_type = '8-K' AND filing_date >= ?
                LIMIT 1
                """,
                (ticker, today),
            )
        )
        score, reasons = score_story(price, news_count, has_new_8k)
        if score >= EVENT_THRESHOLD:
            upsert_event(ticker, "story_candidate", event_date, score, "; ".join(reasons))
            detected += 1
    LOGGER.info("Detected %s story candidates from %s latest price rows.", detected, len(prices))
    return detected
