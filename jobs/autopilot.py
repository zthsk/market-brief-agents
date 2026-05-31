from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from jobs.daily_refresh import DEFAULT_DIGEST_BATCH_SIZE, daily_market_refresh
from jobs.pipeline import _render_script_row
from media_engine.paths import OUTPUT_ROOT, review_bundle_dir
from media_engine.script_manifest import prepare_script_manifest
from models.database import init_db, insert_script, query
from services.audio_generator import generate_audio_result
from services.logging_utils import get_logger
from services.market_calendar import is_nyse_trading_day
from services.script_generator import generate_script_from_manifest_path


LOGGER = get_logger(__name__)
AUTOPILOT_ROOT = OUTPUT_ROOT / "autopilot"


def run_weekday_autopilot(
    *,
    video_limit: int = 3,
    renderer: str = "remotion",
    today: date | str | None = None,
    extended_size: int = 100,
    top_movers: int = 80,
    research_limit: int = 30,
    min_event_score: int = 40,
    min_video_score: int = 70,
    force_research: bool = False,
    refresh_stale_research_after_hours: int = 24,
    force_if_no_tier1_or_tier2_sources: bool = True,
    force_script: bool = False,
    force_render: bool = False,
    skip_tts: bool = False,
    skip_render: bool = False,
    gemini_digests: bool = False,
    digest_batch_size: int = DEFAULT_DIGEST_BATCH_SIZE,
) -> dict[str, Any]:
    init_db()
    run_date = _coerce_date(today)
    settings = {
        "video_limit": video_limit,
        "renderer": renderer,
        "extended_size": extended_size,
        "top_movers": top_movers,
        "research_limit": research_limit,
        "min_event_score": min_event_score,
        "min_video_score": min_video_score,
        "force_research": force_research,
        "force_script": force_script,
        "force_render": force_render,
        "skip_tts": skip_tts,
        "skip_render": skip_render,
        "gemini_digests": gemini_digests,
        "digest_batch_size": digest_batch_size,
    }
    if not is_nyse_trading_day(run_date):
        return _write_run_artifact(
            run_date,
            {
                "date": run_date.isoformat(),
                "skipped": True,
                "reason": "not_nyse_trading_day",
                "settings": settings,
                "selected_video_candidates": [],
                "candidate_results": [],
                "produced_videos": 0,
                "ready_to_upload": 0,
                "errors": 0,
                "error_details": [],
            },
        )

    market_refresh = daily_market_refresh(
        extended_size=extended_size,
        top_movers=top_movers,
        research_limit=research_limit,
        video_limit=video_limit,
        min_event_score=min_event_score,
        min_video_score=min_video_score,
        force_research=force_research,
        refresh_stale_research_after_hours=refresh_stale_research_after_hours,
        force_if_no_tier1_or_tier2_sources=force_if_no_tier1_or_tier2_sources,
        create_local_digests=True,
        gemini_digest_video_ready=gemini_digests,
        gemini_digest_research_ready_batch=gemini_digests,
        digest_batch_size=digest_batch_size,
        today=run_date,
    )
    if market_refresh.get("skipped"):
        return _write_run_artifact(
            run_date,
            {
                "date": run_date.isoformat(),
                "skipped": True,
                "reason": market_refresh.get("reason", "market_refresh_skipped"),
                "settings": settings,
                "market_refresh": market_refresh,
                "selected_video_candidates": [],
                "candidate_results": [],
                "produced_videos": 0,
                "ready_to_upload": 0,
                "errors": 0,
                "error_details": [],
            },
        )

    selected = _selected_video_candidates(market_refresh, video_limit)
    manifest_paths = (market_refresh.get("manifests") or {}).get("paths_by_event_id") or {}
    candidate_results = []
    error_details = []

    for candidate in selected:
        result = _process_candidate(
            candidate,
            manifest_paths_by_event_id=manifest_paths,
            renderer=renderer,
            force_script=force_script,
            force_render=force_render,
            skip_tts=skip_tts,
            skip_render=skip_render,
        )
        candidate_results.append(result)
        error_details.extend(
            {"ticker": result.get("ticker"), "event_id": result.get("event_id"), "error": error}
            for error in result.get("errors", [])
        )

    payload = {
        "date": run_date.isoformat(),
        "skipped": False,
        "settings": settings,
        "market_refresh": market_refresh,
        "selected_video_candidates": selected,
        "selected_count": len(selected),
        "candidate_results": candidate_results,
        "produced_videos": sum(1 for item in candidate_results if item.get("render", {}).get("status") == "rendered"),
        "ready_to_upload": sum(1 for item in candidate_results if item.get("render", {}).get("ready_for_posting")),
        "errors": len(error_details),
        "error_details": error_details,
    }
    LOGGER.info("Weekday autopilot completed: %s", payload)
    return _write_run_artifact(run_date, payload)


def _selected_video_candidates(market_refresh: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    candidates = [
        dict(candidate)
        for candidate in market_refresh.get("video_candidates", [])
        if candidate.get("decision") == "video_ready" and candidate.get("event_id")
    ]
    return candidates[: max(0, limit)]


def _coerce_date(value: date | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _process_candidate(
    candidate: dict[str, Any],
    *,
    manifest_paths_by_event_id: dict[Any, str],
    renderer: str,
    force_script: bool,
    force_render: bool,
    skip_tts: bool,
    skip_render: bool,
) -> dict[str, Any]:
    event_id = int(candidate["event_id"])
    result: dict[str, Any] = {
        "ticker": candidate.get("ticker"),
        "event_id": event_id,
        "decision": candidate.get("decision"),
        "script": {},
        "tts": {},
        "render": {},
        "errors": [],
    }
    try:
        script = _script_for_event(
            event_id,
            manifest_paths_by_event_id=manifest_paths_by_event_id,
            force_script=force_script,
        )
        result["script"] = {
            "id": script["id"],
            "status": "generated" if script.get("_autopilot_generated") else "reused",
        }
    except Exception as exc:
        result["errors"].append(f"script_generation_failed: {exc}")
        return result

    script_id = int(script["id"])
    audio_status = _ensure_audio(script_id, script, skip_tts=skip_tts)
    result["tts"] = audio_status
    if not audio_status.get("ok"):
        result["errors"].append(str(audio_status.get("error") or "tts_missing"))
        return result

    script = _script_by_id(script_id) or script
    existing_render = _valid_render_for_script(script, renderer=renderer)
    if skip_render:
        result["render"] = {"status": "skipped", **existing_render}
        return result
    if existing_render.get("valid") and not force_render:
        result["render"] = {"status": "reused", **existing_render}
        return result

    render_result = _render_script_row(script, renderer=renderer)
    if not render_result.get("ok"):
        error = render_result.get("error") or "render_failed"
        result["render"] = {"status": "failed", "error": error, **existing_render}
        result["errors"].append(str(error))
        return result
    result["render"] = {
        "status": "rendered",
        "video_path": render_result.get("video_path"),
        "bundle_path": render_result.get("bundle_path"),
        "renderer": render_result.get("renderer"),
        "ready_for_posting": bool(render_result.get("ready_for_posting")),
        "sync_passed": render_result.get("sync_passed"),
        "quality_passed": render_result.get("quality_passed"),
        "warnings": render_result.get("warnings", 0),
    }
    return result


def _script_for_event(
    event_id: int,
    *,
    manifest_paths_by_event_id: dict[Any, str],
    force_script: bool,
) -> dict[str, Any]:
    if not force_script:
        existing = _latest_script_for_event(event_id)
        if existing:
            return existing
    manifest_path = _manifest_path_for_event(event_id, manifest_paths_by_event_id)
    script_result = generate_script_from_manifest_path(manifest_path)
    script_id = insert_script(script_result["event_id"], script_result["db_fields"])
    script = _script_by_id(script_id)
    if not script:
        raise RuntimeError(f"script row {script_id} was not created")
    script["_autopilot_generated"] = True
    return script


def _manifest_path_for_event(event_id: int, manifest_paths_by_event_id: dict[Any, str]) -> str:
    path = manifest_paths_by_event_id.get(event_id) or manifest_paths_by_event_id.get(str(event_id))
    if path and Path(path).exists():
        return str(path)
    prepared = prepare_script_manifest(event_id)
    if not prepared["manifest"].get("ready_for_gemini_script"):
        raise RuntimeError("script manifest is not ready")
    return str(prepared["manifest_path"])


def _ensure_audio(script_id: int, script: dict[str, Any], *, skip_tts: bool) -> dict[str, Any]:
    existing = script.get("audio_path")
    if existing and Path(existing).exists():
        return {"ok": True, "status": "reused", "path": existing}
    if skip_tts:
        return {"ok": False, "status": "skipped", "error": "tts_skipped_and_audio_missing"}
    audio = generate_audio_result(script_id, script.get("script") or "")
    return {
        "ok": audio.ok,
        "status": "generated" if audio.ok else "failed",
        "path": audio.path,
        "provider": audio.provider,
        "error": audio.error,
        "retryable": audio.retryable,
        "status_code": audio.status_code,
    }


def _valid_render_for_script(script: dict[str, Any], *, renderer: str) -> dict[str, Any]:
    video_rows = query("SELECT * FROM videos WHERE script_id = ?", (script["id"],))
    if not video_rows:
        return {"valid": False, "reason": "missing_video_row"}
    video = video_rows[0]
    video_path = Path(video["video_path"])
    if not video_path.exists():
        return {"valid": False, "video_path": video["video_path"], "reason": "missing_video_file"}

    event = _event_for_script(script)
    bundle = review_bundle_dir(
        event["ticker"],
        str(event.get("event_date") or event.get("created_at") or "unknown")[:10],
        int(event["id"]),
    )
    manifest = _read_json(bundle / "manifest.json")
    quality = _read_json(bundle / "quality_report.json")
    sync = _read_json(bundle / "sync_report.json")
    expected_renderer = "remotion" if str(renderer).strip().lower() == "remotion" else "python"
    valid = bool(
        manifest.get("renderer") == expected_renderer
        and manifest.get("ready_for_posting")
        and quality.get("passed")
        and sync.get("passed")
    )
    return {
        "valid": valid,
        "video_path": video["video_path"],
        "bundle_path": str(bundle),
        "renderer": manifest.get("renderer"),
        "ready_for_posting": manifest.get("ready_for_posting", False),
        "quality_passed": quality.get("passed"),
        "sync_passed": sync.get("passed"),
        "reason": None if valid else "render_manifest_not_ready",
    }


def _latest_script_for_event(event_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT *
        FROM scripts
        WHERE event_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (event_id,),
    )
    return rows[0] if rows else None


def _script_by_id(script_id: int) -> dict[str, Any] | None:
    rows = query("SELECT * FROM scripts WHERE id = ?", (script_id,))
    return rows[0] if rows else None


def _event_for_script(script: dict[str, Any]) -> dict[str, Any]:
    rows = query("SELECT * FROM events WHERE id = ?", (script["event_id"],))
    if not rows:
        raise RuntimeError(f"No event found for script {script['id']}")
    return rows[0]


def _write_run_artifact(run_date: date, payload: dict[str, Any]) -> dict[str, Any]:
    day_dir = AUTOPILOT_ROOT / run_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    run_path = day_dir / "run.json"
    latest_path = AUTOPILOT_ROOT / "latest.json"
    payload = {
        **payload,
        "artifacts": {
            **payload.get("artifacts", {}),
            "run": str(run_path),
            "latest": str(latest_path),
            "run_json": str(run_path),
            "latest_json": str(latest_path),
        },
    }
    _write_json(run_path, payload)
    _write_json(latest_path, payload)
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
