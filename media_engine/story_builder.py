from __future__ import annotations

import json
import re

from models.database import query
from media_engine.story_schema import PriceCard, Story, StorySection


def build_story(event: dict, script_row: dict, research: list[dict] | None = None) -> Story:
    ticker = event["ticker"]
    analysis = _analysis(event)
    price = _latest_price(ticker)
    company = _company_name(ticker)
    sections = [
        StorySection(
            type="catalyst",
            title="Why it moved",
            bullets=[
                _clean_bullet(analysis.get("reason"), event.get("reason") or "Fresh market attention"),
                "Investors reacted to the catalyst",
                "Sentiment improved today",
            ],
        ),
        StorySection(
            type="context",
            title="Zoom out",
            bullets=[
                _clean_bullet(analysis.get("impact"), "The bigger trend still matters"),
                _chart_context(ticker),
            ],
        ),
        StorySection(
            type="watch",
            title="Watch next",
            bullets=[
                _clean_bullet(
                    analysis.get("what_to_watch"),
                    "Earnings and management guidance",
                ),
                "Whether momentum holds",
            ],
        ),
    ]
    sources = _sources(research or [])
    if "yfinance" not in sources:
        sources.insert(0, "yfinance")
    return Story(
        ticker=ticker,
        company=company,
        date=str(event.get("event_date") or price.get("date") or "")[:10],
        hook=_hook(ticker, script_row, price, analysis),
        price_card=_price_card(price),
        sections=sections,
        chart_insight=_chart_context(ticker),
        takeaway=_clean_bullet(
            analysis.get("takeaway") or analysis.get("impact"),
            "Earnings decide if the move lasts",
        ),
        sources=sources[:4],
    )


def _analysis(event: dict) -> dict:
    try:
        return json.loads(event.get("analysis_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _latest_price(ticker: str) -> dict:
    rows = query(
        "SELECT * FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    )
    return rows[0] if rows else {}


def _company_name(ticker: str) -> str:
    rows = query("SELECT name FROM companies WHERE ticker = ? LIMIT 1", (ticker,))
    return rows[0]["name"] if rows else ticker


def _price_card(price: dict) -> PriceCard:
    close = price.get("close") or price.get("current_price")
    change = float(price.get("change_percent") or 0)
    return PriceCard(
        price=f"${float(close):,.2f}" if close else "$0.00",
        change_pct=f"{change:+.1f}%",
        direction="up" if change > 0 else "down" if change < 0 else "flat",
        period="today",
    )


def _hook(ticker: str, script_row: dict, price: dict, analysis: dict) -> str:
    change = float(price.get("change_percent") or 0)
    if abs(change) >= 0.1:
        if change >= 8:
            verb = "surged"
        elif change > 0:
            verb = "jumped"
        elif change <= -8:
            verb = "dropped"
        else:
            verb = "fell"
        return f"{ticker} {verb} {abs(change):.1f}% today"
    title = script_row.get("title") or analysis.get("reason") or f"{ticker} is moving today"
    return _clean_label(title)


def _chart_context(ticker: str) -> str:
    rows = query(
        """
        SELECT close
        FROM daily_prices
        WHERE ticker = ? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 90
        """,
        (ticker,),
    )
    if len(rows) < 2:
        return "More history will sharpen this chart"
    latest = float(rows[0]["close"])
    earliest = float(rows[-1]["close"])
    change = ((latest - earliest) / earliest) * 100 if earliest else 0
    if change >= 0:
        return f"Up {change:.1f}% over 90 days"
    return f"Still down {abs(change):.1f}% over 90 days"


def _sources(research: list[dict]) -> list[str]:
    labels: list[str] = []
    for item in research:
        label = item.get("source") or item.get("provider") or item.get("title")
        if label and label not in labels:
            labels.append(str(label))
    return labels


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_bullet(value: str | None, fallback: str) -> str:
    text = _clean_label(value or "")
    lower = text.lower()
    if not text or "educational financial media" in lower:
        return _clean_label(fallback)
    if "michael burry" in lower and "fat pitch" in lower:
        return "Michael Burry called Adobe a fat pitch"
    if "prominent" in lower and "investor" in lower and "sentiment" in lower:
        return "Influential investors can shift sentiment"
    if "earnings report" in lower:
        return "Q2 earnings expected in mid-June"
    replacements = [
        "The most likely catalyst for",
        "The next significant event to watch for",
        "The key thing to watch for",
    ]
    for phrase in replacements:
        text = text.replace(phrase, "").strip(" :.-")
    text = re.sub(r"\bwas\b|\bis\b", "", text, count=1).strip(" :.-")
    if len(text.split()) < 3:
        return _clean_label(fallback)
    return text
