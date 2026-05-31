from __future__ import annotations

import json
import os

from services.gemini import generate_json, gemini_configured
from services.logging_utils import get_logger


SYSTEM_PROMPT = """You analyze public company news for educational financial media.
Do not provide investment advice, price targets, or buy/sell/hold recommendations.
Return JSON only."""
LOGGER = get_logger(__name__)


def analyze_story(
    ticker: str,
    price_movement: dict,
    news: list[dict],
    filings: list[dict],
    research: list[dict] | None = None,
) -> dict:
    if os.getenv("AI_PROVIDER", "").lower() == "gemini" and gemini_configured():
        try:
            return _gemini_analysis(ticker, price_movement, news, filings, research or [])
        except Exception as exc:
            LOGGER.warning("Falling back to local analysis for %s after Gemini error: %s", ticker, exc)
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai_analysis(ticker, price_movement, news, filings)
        except Exception as exc:
            LOGGER.warning("Falling back to local analysis for %s after OpenAI error: %s", ticker, exc)
    else:
        LOGGER.info("Using local analysis for %s because OPENAI_API_KEY is not configured.", ticker)
    return _fallback_analysis(ticker, price_movement, news, filings)


def _gemini_analysis(
    ticker: str,
    price_movement: dict,
    news: list[dict],
    filings: list[dict],
    research: list[dict],
) -> dict:
    prompt = {
        "ticker": ticker,
        "price_movement": price_movement,
        "news": news[:8],
        "filings": filings[:5],
        "web_research": _compact_research(research[:8]),
        "task": [
            "Identify the most likely catalyst without inventing facts.",
            "Explain why the move matters for an educational short video.",
            "List risks and uncertainty.",
            "Name the next event or data point to watch.",
            "Prefer web_research highlights when they are fresher or more specific than cached Yahoo headlines.",
            "Use Tier 1 sources when available for exact financial, legal, regulatory, filing, earnings, insider-trade, or ownership claims.",
            "Use Tier 3 and Tier 4 only as context or discovery leads that require confirmation.",
        ],
        "required_keys": ["reason", "impact", "risk", "what_to_watch"],
    }
    return generate_json(prompt, SYSTEM_PROMPT)


def _compact_research(research: list[dict]) -> list[dict]:
    return [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "published_at": item.get("published_at"),
            "highlights": item.get("highlights") or [],
            "source_tier": item.get("source_tier") or (item.get("source_quality") or {}).get("tier"),
            "claim_use_policy": item.get("claim_use_policy"),
            "requires_confirmation": item.get("requires_confirmation", False),
        }
        for item in research
    ]


def _openai_analysis(ticker: str, price_movement: dict, news: list[dict], filings: list[dict]) -> dict:
    from openai import OpenAI

    client = OpenAI()
    prompt = {
        "ticker": ticker,
        "price_movement": price_movement,
        "news": news[:5],
        "filings": filings[:5],
        "task": [
            "Why the stock moved",
            "Most likely catalyst",
            "Whether catalyst is significant",
            "Risks",
            "Next event investors should watch",
        ],
    }
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content or "{}")


def _fallback_analysis(ticker: str, price_movement: dict, news: list[dict], filings: list[dict]) -> dict:
    change = float(price_movement.get("change_percent") or 0)
    direction = "rose" if change >= 0 else "fell"
    headline = news[0]["headline"] if news else "recent market activity"
    filing_text = f" A recent {filings[0]['filing_type']} filing may also be relevant." if filings else ""
    return {
        "reason": f"{ticker} {direction} {abs(change):.1f}% alongside {headline}.{filing_text}",
        "impact": "The move may indicate a change in investor attention or expectations around the company.",
        "risk": "Headlines can move faster than fundamentals, and follow-up details may change the story.",
        "what_to_watch": "Watch company updates, earnings commentary, volume trends, and any follow-up filings.",
    }
