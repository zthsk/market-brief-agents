from __future__ import annotations

import os
from typing import Iterable

import requests

from models.database import upsert_filings
from services.logging_utils import get_logger


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TRACKED_FORMS = {"8-K", "10-Q", "10-K"}
LOGGER = get_logger(__name__)


def collect_filings(tickers: Iterable[str], limit_per_ticker: int = 10) -> int:
    user_agent = os.getenv("SEC_USER_AGENT")
    if not user_agent:
        LOGGER.warning("Skipping SEC filings: SEC_USER_AGENT is not configured.")
        return 0
    headers = {"User-Agent": user_agent}
    try:
        ticker_map = _load_ticker_map(headers)
    except Exception as exc:
        LOGGER.warning("Skipping SEC filings: failed to load ticker map: %s", exc)
        return 0
    rows = []
    for ticker in tickers:
        cik = ticker_map.get(ticker.upper())
        if not cik:
            LOGGER.warning("Skipping %s SEC filings: no CIK mapping.", ticker)
            continue
        try:
            response = requests.get(SEC_SUBMISSIONS_URL.format(cik=cik), headers=headers, timeout=20)
            response.raise_for_status()
            recent = response.json().get("filings", {}).get("recent", {})
        except Exception as exc:
            LOGGER.warning("Skipping %s SEC filings after request error: %s", ticker, exc)
            continue
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        kept = 0
        for form, filing_date, accession in zip(forms, dates, accessions, strict=False):
            if form not in TRACKED_FORMS:
                continue
            accession_clean = accession.replace("-", "")
            rows.append(
                {
                    "ticker": ticker,
                    "filing_type": form,
                    "filing_date": filing_date,
                    "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/{accession}-index.html",
                }
            )
            kept += 1
            if kept >= limit_per_ticker:
                break
    stored = upsert_filings(rows)
    LOGGER.info("Stored %s SEC filing rows from %s fetched filings.", stored, len(rows))
    return stored


def _load_ticker_map(headers: dict[str, str]) -> dict[str, str]:
    response = requests.get(SEC_COMPANY_TICKERS_URL, headers=headers, timeout=20)
    response.raise_for_status()
    payload = response.json()
    mapping = {}
    for item in payload.values():
        mapping[str(item["ticker"]).upper()] = str(item["cik_str"]).zfill(10)
    return mapping
