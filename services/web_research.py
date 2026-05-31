from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any, Iterable
from xml.etree import ElementTree

import requests

from media_engine.paths import research_dir
from models.database import execute, query, upsert_research_sources
from services.logging_utils import get_logger


EXA_SEARCH_URL = "https://api.exa.ai/search"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
DEFAULT_EXCLUDED_DOMAINS = {
    "bitrss.com",
    "pinterest.com",
    "medium.com",
    "substack.com",
}
LOGGER = get_logger(__name__)
DEFAULT_RESEARCH_PROVIDERS = ("exa",)
ALL_RESEARCH_PROVIDERS = ("exa", "google_news", "press_releases")
OFFICIAL_SOURCE_TERMS = ("newsroom", "investor relations", "investor", "ir.")
WIRE_SOURCE_TERMS = ("business wire", "pr newswire", "prnewswire", "globe newswire", "globenewswire", "accesswire")
WIRE_DOMAINS = ("businesswire.com", "prnewswire.com", "globenewswire.com", "accesswire.com")
TIER_2_SOURCE_TERMS = (
    "reuters",
    "associated press",
    "ap news",
    "bloomberg",
    "cnbc",
    "fox business",
    "wall street journal",
    "wsj",
    "marketwatch",
    "yahoo finance",
    "nasdaq",
)
TIER_2_DOMAINS = (
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "cnbc.com",
    "foxbusiness.com",
    "wsj.com",
    "marketwatch.com",
    "finance.yahoo.com",
    "nasdaq.com",
)
TIER_3_SOURCE_TERMS = (
    "business insider",
    "cnn business",
    "barron's",
    "barrons",
    "forbes",
    "motley fool",
    "seeking alpha",
    "benzinga",
    "zacks",
    "investorplace",
)
TIER_3_DOMAINS = (
    "businessinsider.com",
    "markets.businessinsider.com",
    "barrons.com",
    "fool.com",
    "seekingalpha.com",
    "benzinga.com",
    "zacks.com",
    "investorplace.com",
)
TIER_4_SOURCE_TERMS = (
    "fox news",
    "cnn",
    "minichart",
    "stocktwits",
    "simply wall",
    "simplywall",
    "tikr",
    "tradingkey",
)
TIER_4_DOMAINS = (
    "foxnews.com",
    "cnn.com",
    "minichart.com",
    "stocktwits.com",
    "simplywall.st",
    "tikr.com",
    "aol.com",
    "tradingkey.com",
)
SOURCE_TIER_LABELS = {
    1: "primary_official",
    2: "reputable_financial",
    3: "market_commentary",
    4: "discovery_low_confirmation",
}
CLAIM_USE_POLICIES = {
    1: "hard_facts_and_official_claims",
    2: "hard_facts_with_tier1_preferred_for_exact_claims",
    3: "context_only_requires_confirmation",
    4: "discovery_only_requires_confirmation",
}


def collect_event_research(
    event: dict,
    limit: int = 8,
    force: bool = False,
    providers: list[str] | tuple[str, ...] | None = None,
    exa_min_accepted_sources: int = 8,
) -> int:
    selected = _research_providers(providers)
    existing = research_for_event(int(event["id"]))
    if existing and not force and research_bundle_path(event).exists():
        return len(existing)
    if force:
        _clear_provider_rows(int(event["id"]), selected)
    stored_total = 0
    for provider in selected:
        if provider == "exa":
            stored_total += _collect_exa_research(
                event,
                limit=limit,
                min_accepted_sources=exa_min_accepted_sources,
            )
        elif provider == "google_news":
            stored_total += _collect_google_news_research(event, limit=limit)
        elif provider == "press_releases":
            stored_total += _collect_press_release_research(event, limit=limit)
        else:
            LOGGER.warning("Skipping unknown research provider: %s", provider)
    return stored_total


def _collect_exa_research(event: dict, limit: int = 8, min_accepted_sources: int = 8) -> int:
    if os.getenv("WEB_SEARCH_PROVIDER", "exa").lower() != "exa":
        return 0
    if not os.getenv("EXA_API_KEY"):
        LOGGER.info("Skipping Exa research for %s: EXA_API_KEY is not configured.", event["ticker"])
        return 0
    try:
        core_queries = _event_core_queries(event)
        expansion_queries = _event_expansion_queries(event)
        search_queries = list(core_queries)
        payloads = [_exa_search_payload(search_query, limit=limit) for search_query in core_queries]
        results = _dedupe_results(
            result
            for payload in payloads
            for result in payload.get("results", [])
        )
        accepted, rejected = _review_exa_results(event, results)
        expanded = len(accepted) < min_accepted_sources
        if expanded:
            expansion_payloads = [
                _exa_search_payload(search_query, limit=limit)
                for search_query in expansion_queries
            ]
            payloads.extend(expansion_payloads)
            search_queries.extend(expansion_queries)
    except Exception as exc:
        LOGGER.warning("Skipping Exa research for %s after error: %s", event["ticker"], exc)
        return 0
    results = _dedupe_results(
        result
        for payload in payloads
        for result in payload.get("results", [])
    )
    accepted, rejected = _review_exa_results(event, results)
    rows = [_result_to_row(event, result, provider="exa") for result in accepted]
    stored = upsert_research_sources(rows)
    aggregate_payload = {
        "results": results,
        "_request": {
            "queries": search_queries,
            "core_queries": core_queries,
            "expansion_queries": expansion_queries if expanded else [],
            "expanded": expanded,
            "min_accepted_sources": min_accepted_sources,
            "limit_per_query": limit,
        },
        "_provider_payloads": payloads,
    }
    _write_research_bundle(event, "exa", " | ".join(search_queries), aggregate_payload, accepted, rejected, stored)
    LOGGER.info("Stored %s Exa research sources for %s.", stored, event["ticker"])
    return stored


def _review_exa_results(event: dict, results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted = []
    rejected = []
    for result in results:
        allowed, reason = _result_allowed(event, result)
        item = dict(result)
        _enrich_result(item, event, "exa")
        item["review_status"] = "accepted" if allowed else "rejected"
        item["review_reason"] = reason
        if result.get("url") and allowed:
            accepted.append(item)
        else:
            rejected.append(item)
    return accepted, rejected


def _collect_google_news_research(event: dict, limit: int = 8) -> int:
    search_query = _google_news_query(event)
    try:
        payload = _google_news_payload(search_query, limit=limit)
    except Exception as exc:
        LOGGER.warning("Skipping Google News RSS for %s after error: %s", event["ticker"], exc)
        return 0
    return _store_reviewed_provider_results(event, "google_news", search_query, payload)


def _collect_press_release_research(event: dict, limit: int = 8) -> int:
    search_query = _press_release_query(event)
    try:
        payload = _google_news_payload(search_query, limit=limit)
    except Exception as exc:
        LOGGER.warning("Skipping press release search for %s after error: %s", event["ticker"], exc)
        return 0
    accepted = []
    rejected = []
    for result in payload.get("results", []):
        allowed, reason = _press_release_allowed(event, result)
        item = dict(result)
        _enrich_result(item, event, "press_releases")
        item["review_status"] = "accepted" if allowed else "rejected"
        item["review_reason"] = reason
        if result.get("url") and allowed:
            accepted.append(item)
        else:
            rejected.append(item)
    rows = [_result_to_row(event, result, provider="company_press_release") for result in accepted]
    stored = upsert_research_sources(rows)
    _write_research_bundle(
        event,
        "press_releases",
        search_query,
        payload,
        accepted,
        rejected,
        stored,
    )
    LOGGER.info("Stored %s press release candidate(s) for %s.", stored, event["ticker"])
    return stored


def _store_reviewed_provider_results(
    event: dict,
    provider: str,
    search_query: str,
    payload: dict[str, Any],
) -> int:
    accepted = []
    rejected = []
    for result in payload.get("results", []):
        allowed, reason = _result_allowed(event, result)
        item = dict(result)
        _enrich_result(item, event, provider)
        item["review_status"] = "accepted" if allowed else "rejected"
        item["review_reason"] = reason
        if result.get("url") and allowed:
            accepted.append(item)
        else:
            rejected.append(item)
    rows = [_result_to_row(event, result, provider=provider) for result in accepted]
    stored = upsert_research_sources(rows)
    _write_research_bundle(event, provider, search_query, payload, accepted, rejected, stored)
    LOGGER.info("Stored %s %s research source(s) for %s.", stored, provider, event["ticker"])
    return stored


def collect_research_for_events(
    limit: int = 5,
    force: bool = False,
    providers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, int]:
    rows = query(
        """
        SELECT *
        FROM events
        ORDER BY score DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    counts = {"eligible": len(rows), "researched": 0, "sources": 0, "errors": 0}
    for event in rows:
        try:
            sources = collect_event_research(event, force=force, providers=providers)
            if sources:
                counts["researched"] += 1
                counts["sources"] += sources
        except Exception:
            counts["errors"] += 1
    return counts


def research_for_event(event_id: int) -> list[dict]:
    rows = query(
        """
        SELECT *
        FROM research_sources
        WHERE event_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """,
        (event_id,),
    )
    for row in rows:
        try:
            row["highlights"] = json.loads(row.get("highlights_json") or "[]")
        except Exception:
            row["highlights"] = []
        metadata = _json_or_empty(row.get("metadata_json"), fallback={})
        row["metadata"] = metadata
        _apply_metadata_fields(row, metadata)
    return rows


def research_bundle_path(event: dict) -> Path:
    return research_dir(event["ticker"], event.get("event_date") or event.get("created_at") or "unknown", int(event["id"]))


def research_bundle_summaries(limit: int | None = None) -> list[dict[str, Any]]:
    root = research_dir("", "", 0).parent
    if not root.exists():
        return []
    summaries = []
    for path in sorted(root.iterdir(), reverse=True):
        if limit is not None and len(summaries) >= limit:
            break
        if not path.is_dir():
            continue
        manifest = _read_json(path / "manifest.json")
        if not manifest:
            continue
        summaries.append(_research_bundle_summary(path, manifest))
    return summaries


def research_bundle_detail(bundle_path: str | Path) -> dict[str, Any] | None:
    path = Path(bundle_path)
    manifest = _read_json(path / "manifest.json")
    if not manifest:
        return None
    review = _read_json(path / "review_results.json")
    event = _research_bundle_event_context(manifest)
    return {
        **_research_bundle_summary(path, manifest, review=review),
        "accepted": [_sanitize_review_item(item, event) for item in review.get("accepted", [])],
        "rejected": [_sanitize_review_item(item, event) for item in review.get("rejected", [])],
    }


def research_bundle_for_event(event: dict) -> dict[str, Any] | None:
    return research_bundle_detail(research_bundle_path(event))


def research_bundles() -> list[dict[str, Any]]:
    bundles = []
    for summary in research_bundle_summaries():
        detail = research_bundle_detail(summary["bundle_path"])
        if detail:
            bundles.append(detail)
    return bundles


def research_ready_for_event(event: dict) -> bool:
    manifest = _read_json(research_bundle_path(event) / "manifest.json")
    return bool(manifest.get("ready_for_script_generation"))


def approve_research_bundle(
    bundle_path: str | Path,
    *,
    approval_mode: str = "manual",
) -> dict[str, Any]:
    path = Path(bundle_path)
    manifest_path = path / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["ready_for_script_generation"] = True
    manifest["approval_mode"] = approval_mode
    manifest["automation_stage"] = (
        "research_auto_approved_for_script"
        if approval_mode == "auto_daily_research_candidate"
        else "research_approved_for_script"
    )
    _write_json(manifest_path, manifest)
    return manifest


def _exa_search_payload(search_query: str, limit: int = 8) -> dict[str, Any]:
    excluded = sorted(_excluded_domains())
    request_payload = _exa_request_payload(search_query, limit, excluded)
    response = requests.post(
        EXA_SEARCH_URL,
        headers={"Content-Type": "application/json", "x-api-key": os.environ["EXA_API_KEY"]},
        json=request_payload,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    payload["_request"] = request_payload
    return payload


def _exa_search(search_query: str, limit: int = 8) -> list[dict[str, Any]]:
    return _exa_search_payload(search_query, limit).get("results", [])


def _exa_request_payload(search_query: str, limit: int, excluded: list[str]) -> dict[str, Any]:
    return {
        "query": search_query,
        "type": "auto",
        "category": "news",
        "numResults": limit,
        "excludeDomains": excluded,
        "contents": {"highlights": True},
    }


def _event_queries(event: dict) -> list[str]:
    return [*_event_expansion_queries(event), *_event_core_queries(event)]


def _event_core_queries(event: dict) -> list[str]:
    reason = event.get("reason") or ""
    ticker = event["ticker"]
    company = _company_name(event)
    queries = [
        f"{ticker} why is stock moving today",
        f"{company} latest stock market news",
    ]
    if reason:
        queries = [f"{query} {reason}" for query in queries]
    return queries


def _event_expansion_queries(event: dict) -> list[str]:
    reason = event.get("reason") or ""
    ticker = event["ticker"]
    company = _company_name(event)
    queries = [
        f"Latest update on {ticker} stock",
        f"{company} earnings guidance filing analyst reaction",
    ]
    if reason:
        queries = [f"{query} {reason}" for query in queries]
    return queries


def _event_query(event: dict) -> str:
    return _event_queries(event)[0]


def _google_news_payload(search_query: str, limit: int = 8) -> dict[str, Any]:
    request_payload = {
        "q": search_query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
        "limit": limit,
    }
    response = requests.get(
        GOOGLE_NEWS_RSS_URL,
        params={key: value for key, value in request_payload.items() if key != "limit"},
        timeout=20,
    )
    response.raise_for_status()
    results = _parse_google_news_rss(response.text, limit=limit)
    return {"results": results, "_request": request_payload, "_raw_xml": response.text}


def _parse_google_news_rss(xml_text: str, limit: int = 8) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    items = []
    for item in root.findall("./channel/item")[:limit]:
        source = item.find("source")
        description = (item.findtext("description") or "").strip()
        items.append(
            {
                "title": _clean_text(item.findtext("title") or ""),
                "url": (item.findtext("link") or "").strip(),
                "source": _clean_text(source.text) if source is not None and source.text else None,
                "publishedDate": (item.findtext("pubDate") or "").strip(),
                "highlights": [_clean_text(description)] if description else [],
            }
        )
    return items


def _result_to_row(event: dict, result: dict[str, Any], provider: str) -> dict[str, Any]:
    highlights = [_clean_text(str(item)) for item in result.get("highlights") or []]
    classification = classify_source(event, {**result, "provider": provider})
    return {
        "event_id": int(event["id"]),
        "ticker": event["ticker"],
        "provider": provider,
        "title": result.get("title"),
        "url": result["url"],
        "source": result.get("author") or result.get("source"),
        "published_at": result.get("publishedDate"),
        "highlights_json": json.dumps(highlights),
        "metadata_json": json.dumps(_classification_metadata(classification), sort_keys=True),
    }


def _result_allowed(event: dict, result: dict[str, Any]) -> tuple[bool, str]:
    domain = _domain(result.get("url", ""))
    if domain in _excluded_domains():
        return False, f"excluded domain: {domain}"
    ticker = str(event["ticker"]).lower()
    company_terms = _company_terms(event)
    text = " ".join(
        [
            str(result.get("title") or ""),
            " ".join(str(item) for item in result.get("highlights") or []),
        ]
    ).lower()
    if ticker not in text and not any(term in text for term in company_terms):
        return False, "ticker/company not found in title/highlights"
    classification = classify_source(event, result)
    if classification["tier"] >= 4:
        return False, f"tier 4 discovery source is not script-grade: {classification['reason']}"
    return True, f"tier {classification['tier']} source matched ticker/company"


def _press_release_allowed(event: dict, result: dict[str, Any]) -> tuple[bool, str]:
    allowed, reason = _result_allowed(event, result)
    if not allowed:
        return allowed, reason
    age = _published_days_from_event(event, result)
    if age is not None and age > 180:
        return False, f"press-release candidate is stale: {age} days before event"
    classification = classify_source(event, result)
    if classification["tier"] == 1 and classification["quality"] in {"official", "wire"}:
        return True, f"tier 1 {classification['quality']} company source"
    text = " ".join(
        [
            str(result.get("title") or ""),
            str(result.get("source") or ""),
            " ".join(str(item) for item in result.get("highlights") or []),
            _domain(result.get("url", "")),
        ]
    ).lower()
    release_terms = ("press release", "announces", "announced", "reports", "reported", "investor")
    if not any(term in text for term in release_terms):
        return False, "not a press-release style result"
    return False, "press-release style result is not tier 1 official or wire"


def classify_source(event: dict, result: dict[str, Any]) -> dict[str, Any]:
    source = str(result.get("author") or result.get("source") or "")
    domain = _domain(result.get("url", ""))
    title = str(result.get("title") or "")
    highlights = " ".join(str(item) for item in result.get("highlights") or [])
    text = " ".join([source, domain, title, highlights]).lower()
    company_terms = _company_terms(event)
    if "sec.gov" in domain:
        return _source_quality(1, "official", "SEC filing or exhibit")
    if _is_company_official_source(text, domain, company_terms):
        return _source_quality(1, "official", "company investor relations or newsroom")
    if _is_wire_source(text, domain):
        if _wire_issuer_matches_company(text, company_terms):
            return _source_quality(
                1,
                "wire",
                "verified company-issued newswire release",
                is_official_company_release=True,
            )
        return _source_quality(
            3,
            "wire_candidate",
            "newswire source without clear issuer/company match",
            requires_confirmation=True,
        )
    if result.get("provider") in {"press_releases", "company_press_release"}:
        return _source_quality(4, "candidate", "unverified press-release candidate")
    if _is_cnn_business(text, domain):
        return _source_quality(3, "context", "business/mainstream context source", requires_confirmation=True)
    if _is_forbes_reported(text, domain):
        return _source_quality(3, "context", "reported mainstream business source", requires_confirmation=True)
    if any(term in text for term in TIER_2_SOURCE_TERMS) or _domain_matches(domain, TIER_2_DOMAINS):
        return _source_quality(2, "news", "reputable financial journalism or market data")
    if any(term in text for term in TIER_3_SOURCE_TERMS) or _domain_matches(domain, TIER_3_DOMAINS):
        return _source_quality(3, "context", "business/mainstream context or market commentary source", requires_confirmation=True)
    if any(term in text for term in TIER_4_SOURCE_TERMS) or _domain_matches(domain, TIER_4_DOMAINS):
        return _source_quality(4, "discovery", "discovery or low-confirmation source")
    return _source_quality(4, "discovery", "unclassified source")


def _source_quality(
    tier: int,
    quality: str,
    reason: str,
    *,
    is_official_company_release: bool = False,
    requires_confirmation: bool | None = None,
) -> dict[str, Any]:
    if requires_confirmation is None:
        requires_confirmation = tier >= 3
    return {
        "quality": quality,
        "tier": tier,
        "source_tier": tier,
        "tier_label": SOURCE_TIER_LABELS[tier],
        "source_tier_label": SOURCE_TIER_LABELS[tier],
        "rank": tier,
        "reason": reason,
        "classification_reason": reason,
        "claim_use_policy": CLAIM_USE_POLICIES[tier],
        "is_official_company_release": is_official_company_release,
        "requires_confirmation": requires_confirmation,
    }


def _classification_metadata(classification: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_tier": classification.get("source_tier", classification.get("tier")),
        "source_tier_label": classification.get("source_tier_label", classification.get("tier_label")),
        "claim_use_policy": classification.get("claim_use_policy"),
        "is_official_company_release": bool(classification.get("is_official_company_release", False)),
        "requires_confirmation": bool(classification.get("requires_confirmation", False)),
        "classification_reason": classification.get("classification_reason", classification.get("reason")),
    }


def _apply_metadata_fields(row: dict[str, Any], metadata: dict[str, Any]) -> None:
    for key in (
        "source_tier",
        "source_tier_label",
        "claim_use_policy",
        "is_official_company_release",
        "requires_confirmation",
        "classification_reason",
    ):
        if key in metadata:
            row[key] = metadata[key]


def _is_company_official_source(text: str, domain: str, company_terms: tuple[str, ...]) -> bool:
    if any(term in text for term in OFFICIAL_SOURCE_TERMS) and any(
        term in text for term in company_terms
    ):
        return True
    official_subdomains = ("investor", "ir", "news", "newsroom", "press", "media")
    if _domain_contains_company_term(domain, company_terms) and any(
        label in domain.split(".") for label in official_subdomains
    ):
        return True
    official_terms = ("investor relations", "newsroom")
    return any(term in text for term in official_terms) and any(
        term.replace(" ", "") in domain.replace("-", "") or term in text for term in company_terms
    )


def _is_wire_source(text: str, domain: str) -> bool:
    return any(term in text for term in WIRE_SOURCE_TERMS) or _domain_matches(domain, WIRE_DOMAINS)


def _wire_issuer_matches_company(text: str, company_terms: tuple[str, ...]) -> bool:
    release_terms = (
        "announces",
        "announced",
        "reports",
        "reported",
        "launches",
        "prices",
        "declares",
        "to acquire",
        "completes",
        "investor relations",
    )
    return any(term in text for term in company_terms) and any(term in text for term in release_terms)


def _is_cnn_business(text: str, domain: str) -> bool:
    return _domain_matches(domain, ("cnn.com",)) and ("cnn business" in text or "/business" in text)


def _is_forbes_reported(text: str, domain: str) -> bool:
    return _domain_matches(domain, ("forbes.com",)) and "contributor" not in text


def _domain_contains_company_term(domain: str, company_terms: tuple[str, ...]) -> bool:
    normalized_domain = re.sub(r"[^a-z0-9]", "", domain.lower())
    for term in company_terms:
        normalized_term = re.sub(r"[^a-z0-9]", "", term.lower())
        if len(normalized_term) >= 4 and normalized_term in normalized_domain:
            return True
    return False


def _domain_matches(domain: str, candidates: tuple[str, ...]) -> bool:
    return any(domain == candidate or domain.endswith(f".{candidate}") for candidate in candidates)


def _enrich_result(item: dict[str, Any], event: dict, provider: str) -> None:
    item["provider"] = provider
    item["title"] = _clean_text(str(item.get("title") or ""))
    item["highlights"] = [_clean_text(str(value)) for value in item.get("highlights") or []]
    item["source_quality"] = classify_source(event, item)
    item.update(_classification_metadata(item["source_quality"]))


def _write_research_bundle(
    event: dict,
    provider: str,
    search_query: str,
    raw_payload: dict[str, Any],
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    stored: int,
) -> None:
    target = research_bundle_path(event)
    target.mkdir(parents=True, exist_ok=True)
    request_payload = raw_payload.get("_request") or {"query": search_query}
    raw_to_write = {
        key: value for key, value in raw_payload.items() if key not in {"_request", "_raw_xml"}
    }
    provider_prefix = _provider_file_prefix(provider)
    if provider == "exa":
        _write_json(target / "request.json", request_payload)
        _write_json(target / "raw_response.json", raw_to_write)
    _write_json(target / f"{provider_prefix}_request.json", request_payload)
    _write_json(target / f"{provider_prefix}_raw_response.json", raw_to_write)
    if "_raw_xml" in raw_payload:
        (target / f"{provider_prefix}_raw.xml").write_text(
            raw_payload["_raw_xml"], encoding="utf-8"
        )
    _write_json(
        target / f"{provider_prefix}_review_results.json",
        {"accepted": accepted, "rejected": rejected},
    )
    _write_aggregate_review(target)
    manifest = _read_json(target / "manifest.json")
    manifest.update(
        {
            "event_id": int(event["id"]),
            "ticker": event["ticker"],
            "date": str(event.get("event_date") or event.get("created_at") or "unknown")[:10],
            "automation_stage": manifest.get("automation_stage") or "research_ready_for_review",
            "ready_for_script_generation": bool(manifest.get("ready_for_script_generation", False)),
        }
    )
    manifest.setdefault("provider_queries", {})
    manifest["provider_queries"][provider] = search_query
    manifest["provider_counts"] = _provider_counts(target)
    aggregate = _read_json(target / "review_results.json")
    manifest["accepted_count"] = len(aggregate.get("accepted", []))
    manifest["rejected_count"] = len(aggregate.get("rejected", []))
    manifest["stored_count"] = sum(
        int(item.get("stored_count", 0)) for item in manifest["provider_counts"].values()
    )
    _write_json(target / "manifest.json", manifest)
    _write_markdown(target / "review.md", manifest, aggregate.get("accepted", []), aggregate.get("rejected", []))


def _provider_manifest(
    provider: str,
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    stored: int,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "stored_count": stored,
    }


def _write_aggregate_review(target: Path) -> None:
    accepted = []
    rejected = []
    for path in sorted(target.glob("*_review_results.json")):
        if path.name == "review_results.json":
            continue
        provider = path.name.removesuffix("_review_results.json")
        payload = _read_json(path)
        accepted.extend(_tag_provider_items(payload.get("accepted", []), provider))
        rejected.extend(_tag_provider_items(payload.get("rejected", []), provider))
    _write_json(target / "review_results.json", {"accepted": accepted, "rejected": rejected})


def _provider_counts(target: Path) -> dict[str, dict[str, Any]]:
    counts = {}
    for path in sorted(target.glob("*_review_results.json")):
        if path.name == "review_results.json":
            continue
        provider = path.name.removesuffix("_review_results.json")
        payload = _read_json(path)
        accepted = payload.get("accepted", [])
        rejected = payload.get("rejected", [])
        counts[provider] = _provider_manifest(provider, accepted, rejected, len(accepted))
    return counts


def _tag_provider_items(items: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    tagged = []
    for item in items:
        copy = dict(item)
        copy.setdefault("provider", provider)
        tagged.append(copy)
    return tagged


def _research_bundle_summary(
    path: Path,
    manifest: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    accepted_count = manifest.get("accepted_count")
    rejected_count = manifest.get("rejected_count")
    if accepted_count is None or rejected_count is None:
        review = review if review is not None else _read_json(path / "review_results.json")
        accepted_count = len(review.get("accepted", []))
        rejected_count = len(review.get("rejected", []))
    return {
        "bundle_path": str(path),
        "manifest": manifest,
        "accepted_count": int(accepted_count or 0),
        "rejected_count": int(rejected_count or 0),
        "provider_counts": manifest.get("provider_counts") or {},
        "ready_for_script_generation": bool(manifest.get("ready_for_script_generation", False)),
    }


def _sanitize_review_item(item: dict[str, Any], event: dict[str, Any] | None = None) -> dict[str, Any]:
    copy = dict(item)
    for key in ("title", "source", "author", "review_reason", "publishedDate", "published_at"):
        if copy.get(key):
            copy[key] = _clean_text(str(copy[key]))
    copy["highlights"] = [_clean_text(str(value)) for value in copy.get("highlights") or []]
    if event:
        if not (copy.get("source_quality") or {}).get("tier"):
            copy["source_quality"] = classify_source(event, copy)
        copy.update(_classification_metadata(copy["source_quality"]))
        current_status, current_reason = _current_review_decision(event, copy)
        copy["current_review_status"] = current_status
        copy["current_review_reason"] = current_reason
    return copy


def _current_review_decision(event: dict[str, Any], item: dict[str, Any]) -> tuple[str, str]:
    provider = str(item.get("provider") or "")
    if provider in {"press_releases", "company_press_release"}:
        allowed, reason = _press_release_allowed(event, item)
    else:
        allowed, reason = _result_allowed(event, item)
    return ("accepted" if allowed else "rejected", reason)


def _research_bundle_event_context(manifest: dict[str, Any]) -> dict[str, Any]:
    ticker = str(manifest.get("ticker") or "")
    return {
        "id": manifest.get("event_id") or 0,
        "ticker": ticker,
        "company": manifest.get("company") or _company_name_for_ticker(ticker) or ticker,
        "event_date": manifest.get("date"),
    }


def _company_name_for_ticker(ticker: str) -> str | None:
    if not ticker:
        return None
    try:
        rows = query("SELECT name FROM companies WHERE ticker = ?", (ticker,))
    except sqlite3.Error:
        return None
    return str(rows[0]["name"]) if rows else None


def _write_markdown(
    path: Path,
    manifest: dict[str, Any],
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> None:
    lines = [
        f"# {manifest['ticker']} Research Review",
        "",
        f"- Stage: `{manifest.get('automation_stage', 'unknown')}`",
        f"- Accepted: {len(accepted)}",
        f"- Rejected: {len(rejected)}",
        "",
        "## Accepted",
    ]
    for item in accepted:
        lines.extend(_markdown_result(item))
    lines.append("## Rejected")
    for item in rejected:
        lines.extend(_markdown_result(item))
    path.write_text("\n".join(lines), encoding="utf-8")


def _markdown_result(item: dict[str, Any]) -> list[str]:
    highlights = item.get("highlights") or []
    quality = item.get("source_quality") or {}
    tier = quality.get("tier", "unknown")
    tier_label = str(quality.get("tier_label") or "unknown").replace("_", " ")
    lines = [
        "",
        f"### {item.get('title') or 'Untitled'}",
        f"- URL: {item.get('url')}",
        f"- Provider: {item.get('provider') or 'unknown'}",
        f"- Tier: {tier} - {tier_label}",
        f"- Quality: {quality.get('quality', 'unknown')}",
        f"- Source: {item.get('author') or item.get('source') or 'Unknown'}",
        f"- Published: {item.get('publishedDate') or item.get('published_at') or 'Unknown'}",
        f"- Review: {item.get('review_status')} - {item.get('review_reason')}",
    ]
    for highlight in highlights[:3]:
        lines.append(f"- Highlight: {highlight}")
    return lines


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _json_or_empty(value: str | None, fallback: Any | None = None) -> Any:
    if fallback is None:
        fallback = {}
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _excluded_domains() -> set[str]:
    configured = {
        item.strip().lower()
        for item in os.getenv("EXA_EXCLUDE_DOMAINS", "").split(",")
        if item.strip()
    }
    return DEFAULT_EXCLUDED_DOMAINS | configured


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        ]
    )
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse((parsed.scheme.lower() or "https", host, path, "", query, ""))


def _dedupe_results(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for result in results:
        url = result.get("url")
        if not url:
            continue
        key = _canonical_url(str(url))
        item = dict(result)
        item["url"] = key
        existing = deduped.get(key)
        if not existing:
            deduped[key] = item
            continue
        existing_highlights = list(existing.get("highlights") or [])
        for highlight in item.get("highlights") or []:
            if highlight not in existing_highlights:
                existing_highlights.append(highlight)
        existing["highlights"] = existing_highlights
        if not existing.get("publishedDate") and item.get("publishedDate"):
            existing["publishedDate"] = item["publishedDate"]
        if not existing.get("author") and item.get("author"):
            existing["author"] = item["author"]
        if not existing.get("source") and item.get("source"):
            existing["source"] = item["source"]
    return list(deduped.values())


def _clear_provider_rows(event_id: int, providers: tuple[str, ...]) -> None:
    db_providers = []
    for provider in providers:
        if provider == "press_releases":
            db_providers.append("company_press_release")
        else:
            db_providers.append(provider)
    for provider in db_providers:
        execute("DELETE FROM research_sources WHERE event_id = ? AND provider = ?", (event_id, provider))


def _research_providers(providers: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if providers is None:
        configured = os.getenv("RESEARCH_PROVIDERS", "")
        providers = configured.split(",") if configured else DEFAULT_RESEARCH_PROVIDERS
    normalized = []
    for provider in providers:
        value = provider.strip().lower().replace("-", "_")
        if value == "all":
            normalized.extend(ALL_RESEARCH_PROVIDERS)
        elif value == "press_release":
            normalized.append("press_releases")
        elif value:
            normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def _provider_file_prefix(provider: str) -> str:
    return provider.replace("-", "_")


def _google_news_query(event: dict) -> str:
    company = _company_name(event)
    reason = event.get("reason") or ""
    return f"{event['ticker']} {company} stock news {reason}".strip()


def _press_release_query(event: dict) -> str:
    company = _company_name(event)
    return f'"{company}" OR {event["ticker"]} press release announces reports investor relations'


def _company_name(event: dict) -> str:
    if event.get("company"):
        return str(event["company"])
    rows = query("SELECT name FROM companies WHERE ticker = ?", (event["ticker"],))
    if rows:
        return str(rows[0]["name"])
    return str(event["ticker"])


def _company_terms(event: dict) -> tuple[str, ...]:
    company = _company_name(event).lower()
    terms = [company]
    for token in company.replace(",", " ").split():
        clean = token.strip().lower()
        if len(clean) >= 4 and clean not in {"inc", "corp", "corporation", "company", "class"}:
            terms.append(clean)
    return tuple(dict.fromkeys(terms))


def _clean_text(value: str) -> str:
    text = value
    for _ in range(2):
        text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\ba\s+href=(?:\"[^\"]*\"|'[^']*'|\S+)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\btarget=(?:\"[^\"]*\"|'[^']*'|\S+)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfont\s+color=(?:\"[^\"]*\"|'[^']*'|\S+)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"/(?:a|font)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s*\[\s*(?:\.{3}|…)\s*\]\s*-?\s*", " ", text)
    text = re.sub(r"\.\s*-\s*", ". ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _published_days_from_event(event: dict, result: dict[str, Any]) -> int | None:
    event_date = str(event.get("event_date") or event.get("created_at") or "")[:10]
    published = result.get("publishedDate") or result.get("published_at")
    if not event_date or not published:
        return None
    try:
        event_dt = datetime.fromisoformat(event_date).date()
        published_dt = parsedate_to_datetime(str(published)).date()
    except (TypeError, ValueError):
        return None
    return (event_dt - published_dt).days
