from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models.database import (
    init_db,
    insert_script,
    project_status,
    query,
    update_event_analysis,
)
from media_engine.script_manifest import prepare_script_manifest
from services.asset_generator import generate_assets
from services.audio_generator import generate_audio
from services.company_universe import seed_companies
from services.earnings import collect_earnings
from services.event_detector import detect_events
from services.market_data import collect_news, collect_prices, company_tickers
from services.script_generator import generate_script_from_manifest_path
from services.sec_filings import collect_filings
from services.story_analyzer import analyze_story
from services.video_renderer import render_video
from services.logging_utils import get_logger
from services.web_research import (
    collect_event_research,
    research_bundle_path,
    research_for_event,
    research_ready_for_event,
)


LOGGER = get_logger(__name__)


def run_pipeline(limit: int | None = None, skip_video: bool = False) -> dict[str, int]:
    init_db()
    if not company_tickers(limit=1):
        seed_companies()
    tickers = company_tickers(limit)
    counts = {
        "prices": collect_prices(tickers),
        "news": collect_news(tickers),
        "filings": collect_filings(tickers),
        "earnings": collect_earnings(tickers),
        "events": detect_events(),
        "analysis": 0,
        "research": 0,
        "scripts": 0,
        "audio": 0,
        "videos": 0,
        "errors": 0,
        "research_pending_review": 0,
    }
    _generate_pending(counts, limit=10, skip_video=skip_video)
    LOGGER.info("Pipeline finished with counts: %s", counts)
    return counts


def pending_events() -> list[dict]:
    return query(
        """
        SELECT e.*
        FROM events e
        LEFT JOIN scripts s ON s.event_id = e.id
        WHERE s.id IS NULL
        ORDER BY e.score DESC, e.created_at DESC
        LIMIT 10
        """
    )


def event_context(event: dict) -> dict:
    ticker = event["ticker"]
    price_rows = query(
        "SELECT * FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    )
    news = query(
        "SELECT * FROM news WHERE ticker = ? ORDER BY published_at DESC LIMIT 5",
        (ticker,),
    )
    filings = query(
        "SELECT * FROM sec_filings WHERE ticker = ? ORDER BY filing_date DESC LIMIT 5",
        (ticker,),
    )
    return {
        "price": price_rows[0] if price_rows else {},
        "news": news,
        "filings": filings,
        "research": research_for_event(int(event["id"])),
    }


def generate_for_events(limit: int = 10, skip_video: bool = False) -> dict[str, int]:
    init_db()
    counts = {
        "analysis": 0,
        "research": 0,
        "scripts": 0,
        "audio": 0,
        "videos": 0,
        "errors": 0,
        "research_pending_review": 0,
    }
    _generate_pending(counts, limit=limit, skip_video=skip_video)
    LOGGER.info("Content generation finished with counts: %s", counts)
    return counts


def render_existing_videos(
    limit: int | None = None,
    approved_only: bool = False,
    template: str = "news-studio",
    max_duration: int = 75,
    captions: bool = True,
    renderer: str | None = None,
) -> dict[str, Any]:
    init_db()
    counts = {"eligible": 0, "videos": 0, "errors": 0}
    render_details = []
    for script_row in scripts_without_videos(limit=limit, approved_only=approved_only):
        counts["eligible"] += 1
        result = _render_script_row(
            script_row,
            template=template,
            max_duration=max_duration,
            captions=captions,
            renderer=renderer,
        )
        if result["ok"]:
            counts["videos"] += 1
            if result.get("bundle_path"):
                render_details.append(result)
        else:
            counts["errors"] += 1
    if render_details:
        counts["renders"] = render_details
        last = render_details[-1]
        counts["last_video"] = last.get("video_path")
        counts["last_bundle"] = last.get("bundle_path")
        counts["last_ready_for_posting"] = last.get("ready_for_posting")
    LOGGER.info("Existing video render finished with counts: %s", counts)
    return counts


def render_script_video(
    script_id: int,
    template: str = "news-studio",
    max_duration: int = 75,
    captions: bool = True,
    renderer: str | None = None,
    force: bool = False,
) -> dict[str, int]:
    init_db()
    if force:
        rows = query("SELECT s.* FROM scripts s WHERE s.id = ?", (script_id,))
    else:
        rows = query(
            """
            SELECT s.*
            FROM scripts s
            LEFT JOIN videos v ON v.script_id = s.id
            WHERE s.id = ? AND v.id IS NULL
            """,
            (script_id,),
        )
    if not rows:
        return {"eligible": 0, "videos": 0, "errors": 0}
    result = _render_script_row(
        rows[0],
        template=template,
        max_duration=max_duration,
        captions=captions,
        renderer=renderer,
    )
    if result["ok"]:
        return {"eligible": 1, "videos": 1, "errors": 0}
    return {"eligible": 1, "videos": 0, "errors": 1}


def scripts_without_videos(limit: int | None = None, approved_only: bool = False) -> list[dict]:
    status_filter = "AND s.status = 'approved'" if approved_only else "AND s.status IN ('draft', 'approved')"
    sql = f"""
        SELECT s.*
        FROM scripts s
        LEFT JOIN videos v ON v.script_id = s.id
        WHERE v.id IS NULL
        {status_filter}
        ORDER BY s.created_at DESC
    """
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    return query(sql, params)


def _generate_pending(counts: dict[str, int], limit: int, skip_video: bool = False) -> None:
    for event in pending_events()[:limit]:
        event_id = int(event["id"])
        try:
            counts["research"] += collect_event_research(event)
            if research_review_blocks_generation(event):
                counts["research_pending_review"] += 1
                LOGGER.info(
                    "Skipping script generation for event %s until research is reviewed.",
                    event_id,
                )
                continue
            context = event_context(event)
            analysis = analyze_story(
                event["ticker"],
                context["price"],
                context["news"],
                context["filings"],
                context["research"],
            )
            update_event_analysis(event_id, analysis)
            counts["analysis"] += 1
            manifest_result = prepare_script_manifest(event_id)
            if not manifest_result["manifest"]["ready_for_gemini_script"]:
                counts["research_pending_review"] += 1
                LOGGER.info(
                    "Skipping script generation for event %s until script manifest is ready.",
                    event_id,
                )
                continue
            script_result = generate_script_from_manifest_path(manifest_result["manifest_path"])
            script_id = insert_script(event_id, script_result["db_fields"])
            counts["scripts"] += 1
            if generate_audio(script_id, script_result["db_fields"]["script"]):
                counts["audio"] += 1
            assets = _assets_for_event(event, analysis)
            if not skip_video:
                script_row = query("SELECT * FROM scripts WHERE id = ?", (script_id,))[0]
                if render_video(script_row, assets):
                    counts["videos"] += 1
        except Exception as exc:
            LOGGER.exception("Skipping event %s after generation error: %s", event_id, exc)
            counts["errors"] += 1


def _render_script_row(
    script_row: dict,
    template: str = "news-studio",
    max_duration: int = 75,
    captions: bool = True,
    renderer: str | None = None,
) -> dict:
    try:
        event = query("SELECT * FROM events WHERE id = ?", (script_row["event_id"],))[0]
        analysis = json.loads(event["analysis_json"] or "{}")
        assets = _assets_for_event(event, analysis)
        video_path = render_video(
            script_row,
            assets,
            template=template,
            max_duration=max_duration,
            captions=captions,
            renderer=renderer,
        )
        if not video_path:
            return {"ok": False}
        return {"ok": True, **_render_status(event, str(video_path))}
    except Exception as exc:
        LOGGER.exception("Skipping script %s after render error: %s", script_row["id"], exc)
        return {"ok": False, "error": str(exc)}


def _render_status(event: dict, video_path: str) -> dict:
    from media_engine.paths import review_bundle_dir

    event_date = str(event.get("event_date") or event.get("created_at") or "unknown")[:10]
    bundle = review_bundle_dir(event["ticker"], event_date, int(event["id"]))
    quality = _read_json(bundle / "quality_report.json")
    sync = _read_json(bundle / "sync_report.json")
    manifest = _read_json(bundle / "manifest.json")
    template_selection = _read_json(bundle / "template_selection.json")
    if not (quality or sync):
        return {"video_path": video_path}
    warnings = len(quality.get("warnings") or []) + len(sync.get("warnings") or [])
    return {
        "video_path": video_path,
        "bundle_path": str(bundle),
        "sync_passed": sync.get("passed"),
        "quality_passed": quality.get("passed"),
        "warnings": warnings,
        "ready_for_posting": manifest.get("ready_for_posting", False),
        "renderer": manifest.get("renderer"),
        "template_id": manifest.get("video_template_id") or template_selection.get("selected_template_id"),
        "template_name": manifest.get("video_template_name") or template_selection.get("template_name"),
        "story_type": manifest.get("template_story_type") or template_selection.get("story_type"),
        "template_reason": manifest.get("template_selection_reason")
        or template_selection.get("reason"),
    }


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _assets_for_event(event: dict, analysis: dict) -> dict[str, str]:
    existing = {
        row["asset_type"]: row["file_path"]
        for row in query("SELECT asset_type, file_path FROM assets WHERE event_id = ?", (event["id"],))
    }
    if {"chart", "company", "headline", "summary"}.issubset(existing):
        return existing
    return generate_assets(event, analysis)


def research_review_blocks_generation(event: dict) -> bool:
    bundle = research_bundle_path(event)
    return bundle.exists() and not research_ready_for_event(event)


def status_report() -> dict:
    return project_status()


def as_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
