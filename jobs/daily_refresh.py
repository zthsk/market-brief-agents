from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from media_engine.script_manifest import prepare_script_manifest
from models.database import init_db, query, upsert_daily_candidates, upsert_event
from services.company_universe import YAHOO_SCREENER_UNIVERSES, collect_yahoo_screener_companies, seed_companies
from services.logging_utils import get_logger
from services.market_calendar import is_nyse_trading_day
from services.market_data import collect_news, collect_prices, universe_tickers
from services.research_digest import (
    DEFAULT_DIGEST_BATCH_SIZE,
    build_manifest_digest,
    build_manifest_digests_for_paths,
)
from services.sec_filings import collect_filings
from services.web_research import approve_research_bundle, collect_event_research, research_bundle_path, research_for_event


LOGGER = get_logger(__name__)
CORE_UNIVERSES = ("sp500",)
EXTENDED_UNIVERSES = tuple(YAHOO_SCREENER_UNIVERSES.values())
ALL_REFRESH_UNIVERSES = (*CORE_UNIVERSES, *EXTENDED_UNIVERSES)
BUCKET_ORDER = (
    "top_gainers",
    "top_losers",
    "top_unusual_volume",
    "most_actives",
    "small_cap_gainers",
    "most_shorted_stocks",
)
DEFAULT_RESEARCH_BUCKET_QUOTAS = {
    "top_gainers": 8,
    "top_losers": 8,
    "top_unusual_volume": 6,
    "most_actives": 4,
    "small_cap_gainers": 2,
    "most_shorted_stocks": 2,
}
RESEARCH_BUCKET_QUOTAS_20 = {
    "top_gainers": 6,
    "top_losers": 6,
    "top_unusual_volume": 4,
    "most_actives": 2,
    "small_cap_gainers": 1,
    "most_shorted_stocks": 1,
}
DEFAULT_VIDEO_BUCKET_CAPS = {
    "top_gainers": 2,
    "top_losers": 2,
    "top_unusual_volume": 2,
    "most_actives": 1,
    "small_cap_gainers": 1,
    "most_shorted_stocks": 1,
}


def daily_market_refresh(
    extended_size: int = 100,
    top_movers: int = 80,
    research_limit: int = 30,
    video_limit: int = 5,
    min_event_score: int = 40,
    min_video_score: int = 70,
    force_research: bool = False,
    refresh_stale_research_after_hours: int = 24,
    force_if_no_tier1_or_tier2_sources: bool = True,
    create_local_digests: bool = True,
    gemini_digest_video_ready: bool = True,
    gemini_digest_research_ready_batch: bool = True,
    digest_batch_size: int = DEFAULT_DIGEST_BATCH_SIZE,
    today: date | None = None,
) -> dict[str, Any]:
    init_db()
    run_date = today or date.today()
    if not is_nyse_trading_day(run_date):
        return {
            "date": run_date.isoformat(),
            "skipped": True,
            "reason": "not_nyse_trading_day",
        }

    sp500_count = seed_companies(use_sample_on_error=False)
    yahoo_counts = collect_yahoo_screener_companies(size=extended_size)
    tickers = universe_tickers(ALL_REFRESH_UNIVERSES)
    prices = collect_prices(tickers)
    report = movers_report(limit=top_movers)
    buckets = eligible_mover_buckets(report, min_event_score=min_event_score)
    event_map = upsert_mover_events_from_buckets(buckets, report.get("latest_date"))
    research_candidates = select_balanced_research_candidates(
        buckets,
        quotas=_research_bucket_quotas(research_limit),
        limit=research_limit,
    )
    _attach_event_ids(research_candidates, event_map)
    _persist_candidates(
        research_candidates,
        report.get("latest_date"),
        stage="research_selected",
        decision="research_only",
    )
    research_tickers = [candidate["ticker"] for candidate in research_candidates]
    news = collect_news(research_tickers)
    filings = collect_filings(research_tickers)
    research = collect_research_for_candidates(
        research_candidates,
        force_research=force_research,
        refresh_stale_research_after_hours=refresh_stale_research_after_hours,
        force_if_no_tier1_or_tier2_sources=force_if_no_tier1_or_tier2_sources,
    )
    auto_approval = auto_approve_research_candidates(research_candidates)
    video_candidates = rank_video_candidates(
        research_candidates,
        limit=video_limit,
        min_video_score=min_video_score,
        bucket_caps=DEFAULT_VIDEO_BUCKET_CAPS,
    )
    _persist_video_candidates(video_candidates, report.get("latest_date"))
    manifests = prepare_manifests_for_candidates(research_candidates)
    digests = build_candidate_digests(
        research_candidates,
        video_candidates,
        manifest_paths_by_event_id=manifests["paths_by_event_id"],
        create_local_digests=create_local_digests,
        gemini_digest_video_ready=gemini_digest_video_ready,
        gemini_digest_research_ready_batch=gemini_digest_research_ready_batch,
        digest_batch_size=digest_batch_size,
    )
    result = {
        "date": run_date.isoformat(),
        "skipped": False,
        "sp500_companies": sp500_count,
        "yahoo_screener": yahoo_counts,
        "refresh_tickers": len(tickers),
        "prices": prices,
        "news": news,
        "filings": filings,
        "events": len(event_map),
        "research": research,
        "auto_approval": auto_approval,
        "manifests": manifests,
        "digests": digests,
        "research_candidates": research_candidates,
        "video_candidates": video_candidates,
        "report": movers_report(limit=top_movers),
    }
    LOGGER.info("Daily market refresh completed: %s", result)
    return result


def movers_report(date_value: str = "latest", limit: int = 25) -> dict[str, Any]:
    init_db()
    latest_date = _resolve_price_date(date_value)
    if not latest_date:
        return {
            "latest_date": None,
            "coverage": _coverage(None),
            "bucket_counts": {},
            "candidate_decisions": [],
            **{bucket: [] for bucket in BUCKET_ORDER},
        }
    buckets = {
        "top_gainers": _price_rows(latest_date, "change_percent DESC", limit),
        "top_losers": _price_rows(latest_date, "change_percent ASC", limit),
        "top_unusual_volume": _price_rows(latest_date, "volume_ratio DESC", limit),
        "most_actives": _price_rows(
            latest_date,
            "volume DESC",
            limit,
            universe="yahoo_most_actives",
        ),
        "small_cap_gainers": _price_rows(
            latest_date,
            "change_percent DESC",
            limit,
            universe="yahoo_small_cap_gainers",
        ),
        "most_shorted_stocks": _price_rows(
            latest_date,
            "ABS(change_percent) DESC",
            limit,
            universe="yahoo_most_shorted",
        ),
    }
    return {
        "latest_date": latest_date,
        "coverage": _coverage(latest_date),
        "bucket_counts": {bucket: len(rows) for bucket, rows in buckets.items()},
        "candidate_decisions": _candidate_decisions(latest_date),
        **buckets,
    }


def eligible_mover_buckets(
    report: dict[str, Any],
    min_event_score: int = 40,
) -> dict[str, list[dict[str, Any]]]:
    buckets = {}
    for bucket in BUCKET_ORDER:
        bucket_rows = []
        for rank, row in enumerate(report.get(bucket, []), start=1):
            candidate = dict(row)
            candidate["event_score"] = event_score(candidate)
            if candidate["event_score"] < min_event_score:
                continue
            candidate["rank_in_bucket"] = rank
            candidate["bucket"] = bucket
            bucket_rows.append(candidate)
        buckets[bucket] = bucket_rows
    return buckets


def select_balanced_research_candidates(
    buckets: dict[str, list[dict[str, Any]]],
    quotas: dict[str, int],
    limit: int,
) -> list[dict[str, Any]]:
    by_ticker = _bucket_candidates_by_ticker(buckets)
    selected: list[dict[str, Any]] = []
    selected_tickers: set[str] = set()
    for bucket in BUCKET_ORDER:
        quota = quotas.get(bucket, 0)
        if quota <= 0:
            continue
        rows = sorted(buckets.get(bucket, []), key=lambda item: item["event_score"], reverse=True)
        used = 0
        for row in rows:
            ticker = row["ticker"]
            if ticker in selected_tickers:
                continue
            selected.append(_selection_payload(by_ticker[ticker], bucket))
            selected_tickers.add(ticker)
            used += 1
            if used >= quota or len(selected) >= limit:
                break
        if len(selected) >= limit:
            return selected[:limit]
    remaining = [
        candidate
        for ticker, candidate in by_ticker.items()
        if ticker not in selected_tickers
    ]
    remaining.sort(key=lambda item: item["event_score"], reverse=True)
    for candidate in remaining:
        selected.append(_selection_payload(candidate, candidate["primary_bucket"]))
        if len(selected) >= limit:
            break
    return selected[:limit]


def rank_video_candidates(
    researched_events: list[dict[str, Any]],
    limit: int,
    min_video_score: int,
    bucket_caps: dict[str, int],
) -> list[dict[str, Any]]:
    ranked = []
    for candidate in researched_events:
        enriched = dict(candidate)
        source_stats = _source_stats(int(candidate["event_id"])) if candidate.get("event_id") else {}
        enriched.update(source_stats)
        enriched.update(_video_decision(enriched, min_video_score))
        ranked.append(enriched)
    ranked.sort(key=lambda item: item["video_score"], reverse=True)
    selected = []
    bucket_counts = {bucket: 0 for bucket in BUCKET_ORDER}
    for candidate in ranked:
        if candidate["decision"] != "video_ready":
            continue
        primary = candidate.get("primary_bucket") or "top_gainers"
        cap = bucket_caps.get(primary, limit)
        if bucket_counts.get(primary, 0) >= cap:
            continue
        selected.append(candidate)
        bucket_counts[primary] = bucket_counts.get(primary, 0) + 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for candidate in ranked:
            if candidate in selected or candidate["decision"] == "video_ready":
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
    return selected[:limit]


def collect_research_for_candidates(
    candidates: list[dict[str, Any]],
    force_research: bool = False,
    refresh_stale_research_after_hours: int = 24,
    force_if_no_tier1_or_tier2_sources: bool = True,
) -> int:
    total = 0
    for candidate in candidates:
        event_id = candidate.get("event_id")
        if not event_id:
            continue
        event = query("SELECT * FROM events WHERE id = ?", (event_id,))[0]
        force = _research_force_needed(
            event,
            force_research=force_research,
            refresh_stale_research_after_hours=refresh_stale_research_after_hours,
            force_if_no_tier1_or_tier2_sources=force_if_no_tier1_or_tier2_sources,
        )
        existing = research_for_event(int(event_id))
        if existing and not force:
            continue
        total += collect_event_research(event, force=force)
    return total


def auto_approve_research_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"eligible": len(candidates), "approved": 0, "missing_bundle": 0, "errors": 0}
    for candidate in candidates:
        event_id = candidate.get("event_id")
        if not event_id:
            result["missing_bundle"] += 1
            continue
        event = query("SELECT * FROM events WHERE id = ?", (event_id,))[0]
        bundle_path = research_bundle_path(event)
        if not bundle_path.exists():
            result["missing_bundle"] += 1
            continue
        try:
            approve_research_bundle(
                bundle_path,
                approval_mode="auto_daily_research_candidate",
            )
            result["approved"] += 1
        except Exception:
            result["errors"] += 1
    return result


def prepare_manifests_for_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "eligible": len(candidates),
        "prepared": 0,
        "not_ready": 0,
        "errors": 0,
        "paths_by_event_id": {},
    }
    for candidate in candidates:
        event_id = candidate.get("event_id")
        if not event_id:
            result["not_ready"] += 1
            continue
        try:
            prepared = prepare_script_manifest(int(event_id))
            if prepared["manifest"].get("ready_for_gemini_script"):
                result["prepared"] += 1
                result["paths_by_event_id"][int(event_id)] = prepared["manifest_path"]
            else:
                result["not_ready"] += 1
        except Exception:
            result["errors"] += 1
    return result


def build_candidate_digests(
    research_candidates: list[dict[str, Any]],
    video_candidates: list[dict[str, Any]],
    *,
    manifest_paths_by_event_id: dict[int, str] | None = None,
    create_local_digests: bool = True,
    gemini_digest_video_ready: bool = False,
    gemini_digest_research_ready_batch: bool = False,
    digest_batch_size: int = DEFAULT_DIGEST_BATCH_SIZE,
) -> dict[str, Any]:
    manifest_paths_by_event_id = manifest_paths_by_event_id or {}
    research_paths = _candidate_manifest_paths(research_candidates, manifest_paths_by_event_id)
    video_ready_paths = _candidate_manifest_paths(
        [candidate for candidate in video_candidates if candidate.get("decision") == "video_ready"],
        manifest_paths_by_event_id,
    )
    other_research_paths = [path for path in research_paths if path not in set(video_ready_paths)]
    result: dict[str, Any] = {
        "research_ready_manifests": len(research_paths),
        "video_ready_manifests": len(video_ready_paths),
        "skipped_unapproved_or_not_ready": max(0, len(research_candidates) - len(research_paths)),
        "local": {"digests": 0, "paths": []},
        "video_ready_gemini": {"digests": 0, "paths": []},
        "research_ready_batch_gemini": {"digests": 0, "paths": []},
    }

    if create_local_digests:
        local = build_manifest_digests_for_paths(
            research_paths,
            use_gemini=False,
            batch_size=digest_batch_size,
            force=False,
        )
        result["local"] = {"digests": local["digests"], "paths": local["paths"]}

    if gemini_digest_video_ready and video_ready_paths:
        video_paths = []
        video_digests = 0
        with ThreadPoolExecutor(max_workers=min(5, len(video_ready_paths))) as executor:
            futures = [
                executor.submit(build_manifest_digest, path, use_gemini=True, force=False)
                for path in video_ready_paths
            ]
            for future in as_completed(futures):
                digest = future.result()
                if digest.get("provider") == "gemini":
                    video_digests += 1
                    video_paths.append(str(Path(digest["bundle_path"]) / "research_digest.json"))
        result["video_ready_gemini"] = {"digests": video_digests, "paths": video_paths}

    if gemini_digest_research_ready_batch and other_research_paths:
        batch = build_manifest_digests_for_paths(
            other_research_paths,
            use_gemini=True,
            batch_size=digest_batch_size,
            force=False,
        )
        result["research_ready_batch_gemini"] = {
            "digests": batch["digests"],
            "paths": batch["paths"],
        }
    return result


def upsert_mover_events_from_buckets(
    buckets: dict[str, list[dict[str, Any]]],
    event_date: str | None,
) -> dict[str, int]:
    if not event_date:
        return {}
    event_map = {}
    for candidate in _unique_bucket_candidates(buckets):
        change = float(candidate.get("change_percent") or 0)
        volume_ratio = float(candidate.get("volume_ratio") or 0)
        direction = "up" if change >= 0 else "down"
        reason = (
            f"{candidate['ticker']} moved {direction} {abs(change):.1f}%"
            f" with {volume_ratio:.1f}x average volume"
        )
        upsert_event(candidate["ticker"], "daily_mover", event_date, candidate["event_score"], reason)
        rows = query(
            "SELECT id FROM events WHERE ticker = ? AND event_type = 'daily_mover' AND event_date = ?",
            (candidate["ticker"], event_date),
        )
        if rows:
            event_map[candidate["ticker"]] = int(rows[0]["id"])
    return event_map


def _candidate_bundle_paths(candidates: list[dict[str, Any]]) -> list[str]:
    paths = []
    for candidate in candidates:
        event_id = candidate.get("event_id")
        if not event_id:
            continue
        rows = query("SELECT * FROM events WHERE id = ?", (event_id,))
        if not rows:
            continue
        path = research_bundle_path(rows[0])
        if path.exists():
            paths.append(str(path))
    return list(dict.fromkeys(paths))


def _candidate_manifest_paths(
    candidates: list[dict[str, Any]],
    manifest_paths_by_event_id: dict[int, str],
) -> list[str]:
    paths = []
    for candidate in candidates:
        event_id = candidate.get("event_id")
        if not event_id:
            continue
        path = manifest_paths_by_event_id.get(int(event_id))
        if path and Path(path).exists():
            paths.append(str(path))
    return list(dict.fromkeys(paths))


def event_score(row: dict[str, Any]) -> int:
    change = abs(float(row.get("change_percent") or 0))
    volume_ratio = float(row.get("volume_ratio") or 0)
    return min(100, int(change * 8 + max(0, volume_ratio - 1) * 10))


def _resolve_price_date(date_value: str) -> str | None:
    if date_value == "latest":
        return query("SELECT MAX(date) AS date FROM daily_prices")[0]["date"]
    rows = query("SELECT 1 FROM daily_prices WHERE date = ? LIMIT 1", (date_value,))
    return date_value if rows else None


def _coverage(price_date: str | None) -> dict[str, int]:
    total_companies = query("SELECT COUNT(DISTINCT ticker) AS count FROM companies")[0]["count"]
    sp500_total = query(
        "SELECT COUNT(DISTINCT ticker) AS count FROM universe_memberships WHERE universe = 'sp500'"
    )[0]["count"]
    extended_total = query(
        """
        SELECT COUNT(DISTINCT ticker) AS count
        FROM universe_memberships
        WHERE universe LIKE 'yahoo_%'
        """
    )[0]["count"]
    coverage = {
        "total_companies": total_companies,
        "sp500_total": sp500_total,
        "extended_total": extended_total,
        "latest_price_tickers": 0,
        "sp500_price_tickers": 0,
        "extended_price_tickers": 0,
    }
    if not price_date:
        return coverage
    coverage.update(
        {
            "latest_price_tickers": query(
                "SELECT COUNT(DISTINCT ticker) AS count FROM daily_prices WHERE date = ?",
                (price_date,),
            )[0]["count"],
            "sp500_price_tickers": query(
                """
                SELECT COUNT(DISTINCT p.ticker) AS count
                FROM daily_prices p
                JOIN universe_memberships u ON u.ticker = p.ticker
                WHERE p.date = ? AND u.universe = 'sp500'
                """,
                (price_date,),
            )[0]["count"],
            "extended_price_tickers": query(
                """
                SELECT COUNT(DISTINCT p.ticker) AS count
                FROM daily_prices p
                JOIN universe_memberships u ON u.ticker = p.ticker
                WHERE p.date = ? AND u.universe LIKE 'yahoo_%'
                """,
                (price_date,),
            )[0]["count"],
        }
    )
    return coverage


def _price_rows(
    price_date: str,
    order_by: str,
    limit: int,
    universe: str | None = None,
) -> list[dict[str, Any]]:
    universe_filter = "AND u_filter.universe = ?" if universe else ""
    params: list[Any] = [price_date]
    if universe:
        params.append(universe)
    params.append(limit)
    return query(
        f"""
        SELECT
            p.ticker,
            c.name,
            p.current_price,
            p.close,
            p.change_percent,
            p.volume,
            p.average_volume,
            CASE
                WHEN p.average_volume > 0 THEN CAST(p.volume AS REAL) / p.average_volume
                ELSE 0
            END AS volume_ratio,
            GROUP_CONCAT(DISTINCT u.universe) AS universes
        FROM daily_prices p
        LEFT JOIN companies c ON c.ticker = p.ticker
        LEFT JOIN universe_memberships u ON u.ticker = p.ticker
        LEFT JOIN universe_memberships u_filter ON u_filter.ticker = p.ticker
        WHERE p.date = ? AND p.change_percent IS NOT NULL
        {universe_filter}
        GROUP BY p.ticker
        ORDER BY {order_by}
        LIMIT ?
        """,
        params,
    )


def _research_bucket_quotas(research_limit: int) -> dict[str, int]:
    if research_limit <= 20:
        return RESEARCH_BUCKET_QUOTAS_20
    return DEFAULT_RESEARCH_BUCKET_QUOTAS


def _bucket_candidates_by_ticker(
    buckets: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for bucket in BUCKET_ORDER:
        for row in buckets.get(bucket, []):
            ticker = row["ticker"]
            candidate = by_ticker.setdefault(
                ticker,
                {
                    **row,
                    "bucket_memberships": [],
                    "rank_by_bucket": {},
                    "primary_bucket": bucket,
                },
            )
            candidate["bucket_memberships"].append(bucket)
            candidate["rank_by_bucket"][bucket] = row["rank_in_bucket"]
            if row["event_score"] > candidate["event_score"]:
                candidate.update(row)
                candidate["primary_bucket"] = bucket
    return by_ticker


def _selection_payload(candidate: dict[str, Any], primary_bucket: str) -> dict[str, Any]:
    memberships = list(dict.fromkeys(candidate.get("bucket_memberships") or [primary_bucket]))
    rank = int(candidate.get("rank_by_bucket", {}).get(primary_bucket, candidate.get("rank_in_bucket", 0)))
    return {
        **candidate,
        "primary_bucket": primary_bucket,
        "bucket_memberships": memberships,
        "rank_in_bucket": rank,
        "selection_reason": (
            f"Selected from {primary_bucket} with event score {candidate['event_score']} "
            f"and memberships: {', '.join(memberships)}."
        ),
    }


def _unique_bucket_candidates(buckets: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_ticker = _bucket_candidates_by_ticker(buckets)
    return sorted(by_ticker.values(), key=lambda item: item["event_score"], reverse=True)


def _attach_event_ids(candidates: list[dict[str, Any]], event_map: dict[str, int]) -> None:
    for candidate in candidates:
        candidate["event_id"] = event_map.get(candidate["ticker"])


def _persist_candidates(
    candidates: list[dict[str, Any]],
    event_date: str | None,
    stage: str,
    decision: str,
) -> None:
    if not event_date:
        return
    rows = [_candidate_db_row(candidate, event_date, stage=stage, decision=decision) for candidate in candidates]
    upsert_daily_candidates(rows)


def _persist_video_candidates(candidates: list[dict[str, Any]], event_date: str | None) -> None:
    if not event_date:
        return
    rows = [
        _candidate_db_row(
            candidate,
            event_date,
            stage="video_ranked",
            decision=candidate["decision"],
        )
        for candidate in candidates
    ]
    upsert_daily_candidates(rows)


def _candidate_db_row(
    candidate: dict[str, Any],
    event_date: str,
    stage: str,
    decision: str,
) -> dict[str, Any]:
    return {
        "ticker": candidate["ticker"],
        "event_id": candidate.get("event_id"),
        "event_date": event_date,
        "event_score": int(candidate.get("event_score") or 0),
        "video_score": int(candidate.get("video_score") or 0),
        "decision": decision,
        "primary_bucket": candidate.get("primary_bucket"),
        "bucket_memberships_json": json.dumps(candidate.get("bucket_memberships") or []),
        "selection_reason": candidate.get("selection_reason"),
        "rank_in_bucket": candidate.get("rank_in_bucket"),
        "candidate_stage": stage,
        "catalyst_confidence": candidate.get("catalyst_confidence"),
        "source_quality": candidate.get("source_quality"),
    }


def _candidate_decisions(event_date: str) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT ticker, event_id, event_score, video_score, decision, primary_bucket,
               bucket_memberships_json, selection_reason, candidate_stage,
               catalyst_confidence, source_quality
        FROM daily_candidates
        WHERE event_date = ?
        ORDER BY video_score DESC, event_score DESC
        """,
        (event_date,),
    )
    for row in rows:
        row["bucket_memberships"] = json.loads(row.pop("bucket_memberships_json") or "[]")
    return rows


def _source_stats(event_id: int) -> dict[str, Any]:
    rows = query("SELECT provider, metadata_json, title FROM research_sources WHERE event_id = ?", (event_id,))
    tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for row in rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        tier = int(metadata.get("source_tier") or 4)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    has_filing = bool(query("SELECT 1 FROM events WHERE id = ? AND reason LIKE '%filing%' LIMIT 1", (event_id,)))
    source_quality = "high" if tier_counts[1] or tier_counts[2] else "low" if tier_counts[4] else "medium"
    return {
        "tier1_sources": tier_counts[1],
        "tier2_sources": tier_counts[2],
        "tier3_sources": tier_counts[3],
        "tier4_sources": tier_counts[4],
        "has_tier1_or_tier2": bool(tier_counts[1] or tier_counts[2]),
        "has_research": bool(rows),
        "has_filing_signal": has_filing,
        "source_quality": source_quality,
    }


def _video_decision(candidate: dict[str, Any], min_video_score: int) -> dict[str, Any]:
    score = int(candidate.get("event_score") or 0)
    volume_ratio = float(candidate.get("volume_ratio") or 0)
    has_citable = bool(candidate.get("has_tier1_or_tier2"))
    has_filing = bool(candidate.get("has_filing_signal"))
    has_catalyst = has_citable or has_filing
    score += min(20, int(candidate.get("tier1_sources", 0)) * 8 + int(candidate.get("tier2_sources", 0)) * 4)
    score += 8 if has_catalyst else -18
    score += 6 if volume_ratio >= 2 else 0
    score += _audience_interest_bonus(candidate)
    if not candidate.get("has_research"):
        score -= 20
    if not has_citable and not has_filing:
        score -= 15
    score = max(0, min(100, score))
    if int(candidate.get("event_score") or 0) < 40:
        decision = "skip_weak_move"
    elif not has_catalyst:
        decision = "needs_manual_review" if score >= min_video_score else "skip_no_clear_catalyst"
    elif not has_citable and not has_filing:
        decision = "skip_low_quality_sources"
    elif score >= min_video_score:
        decision = "video_ready"
    else:
        decision = "research_only"
    confidence = "high" if candidate.get("tier1_sources") else "medium" if has_citable else "low"
    return {
        "video_score": score,
        "decision": decision,
        "catalyst_confidence": confidence,
        "selection_reason": _video_selection_reason(candidate, score, decision),
    }


def _audience_interest_bonus(candidate: dict[str, Any]) -> int:
    universes = set(str(candidate.get("universes") or "").split(","))
    bonus = 0
    if "sp500" in universes:
        bonus += 6
    if "yahoo_most_actives" in universes:
        bonus += 6
    if "yahoo_most_shorted" in universes:
        bonus += 4
    return bonus


def _video_selection_reason(candidate: dict[str, Any], score: int, decision: str) -> str:
    return (
        f"{decision} with video score {score}; event score {candidate.get('event_score')}, "
        f"source quality {candidate.get('source_quality', 'unknown')}, "
        f"catalyst confidence {candidate.get('catalyst_confidence', 'unknown')}."
    )


def _research_force_needed(
    event: dict[str, Any],
    force_research: bool,
    refresh_stale_research_after_hours: int,
    force_if_no_tier1_or_tier2_sources: bool,
) -> bool:
    if force_research:
        return True
    bundle = research_bundle_path(event)
    if _research_is_stale(bundle, refresh_stale_research_after_hours):
        return True
    if force_if_no_tier1_or_tier2_sources and _has_research_without_citable_sources(int(event["id"])):
        return True
    return False


def _research_is_stale(bundle: Path, stale_hours: int) -> bool:
    manifest = bundle / "manifest.json"
    if stale_hours <= 0 or not manifest.exists():
        return False
    modified = datetime.fromtimestamp(manifest.stat().st_mtime)
    return datetime.now() - modified > timedelta(hours=stale_hours)


def _has_research_without_citable_sources(event_id: int) -> bool:
    rows = query("SELECT metadata_json FROM research_sources WHERE event_id = ?", (event_id,))
    if not rows:
        return False
    for row in rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        if int(metadata.get("source_tier") or 4) <= 2:
            return False
    return True
