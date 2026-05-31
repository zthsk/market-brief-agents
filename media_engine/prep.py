from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from media_engine.paths import review_bundle_dir
from media_engine.captions import captions_for_scenes, captions_to_srt
from media_engine.scene_builder import build_scenes
from media_engine.story_builder import build_story
from media_engine.story_schema import Story
from media_engine.templates.news_studio import render_thumbnail
from models.database import query


def prepare_event_story(
    event_id: int,
    *,
    template: str = "news-studio",
    max_duration: int = 75,
) -> dict[str, Any]:
    if template not in {"news-studio", "news_studio"}:
        raise ValueError(f"Unsupported video template: {template}")
    event = query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not event:
        raise ValueError(f"No event found for id {event_id}")
    event_row = event[0]
    script_stub = {"title": event_row.get("reason") or event_row["ticker"], "event_id": event_id}
    research = query("SELECT * FROM research_sources WHERE event_id = ?", (event_id,))
    chart_path = _chart_path(event_id)
    story = build_story(event_row, script_stub, research)
    return write_prepared_bundle(
        story,
        event_id=event_id,
        chart_path=chart_path,
        template=template,
        max_duration=max_duration,
    )


def prepare_top_events(
    limit: int = 5,
    *,
    template: str = "news-studio",
    max_duration: int = 75,
) -> dict[str, int]:
    rows = query(
        """
        SELECT e.*
        FROM events e
        LEFT JOIN scripts s ON s.event_id = e.id
        WHERE s.id IS NULL
        ORDER BY e.score DESC, e.created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    counts = {"eligible": len(rows), "prepared": 0, "errors": 0}
    for event in rows:
        try:
            prepare_event_story(int(event["id"]), template=template, max_duration=max_duration)
            counts["prepared"] += 1
        except Exception:
            counts["errors"] += 1
    return counts


def write_prepared_bundle(
    story: Story,
    *,
    event_id: int,
    chart_path: str | None,
    template: str = "news-studio",
    max_duration: int = 75,
    status: str = "prepared",
) -> dict[str, Any]:
    scenes = build_scenes(story, chart_path=chart_path, max_duration=max_duration)
    captions = captions_for_scenes(scenes)
    bundle = review_bundle_dir(story.ticker, story.date, event_id)
    bundle.mkdir(parents=True, exist_ok=True)
    _write_json(bundle / "story.json", story.model_dump())
    _write_json(bundle / "scenes.json", [scene.model_dump() for scene in scenes])
    (bundle / "captions.srt").write_text(captions_to_srt(captions), encoding="utf-8")
    render_thumbnail(bundle / "thumbnail.png", story, chart_path)
    manifest = {
        "event_id": event_id,
        "ticker": story.ticker,
        "date": story.date,
        "template": template,
        "status": status,
        "automation_stage": "story_prepared",
        "needs_tts": True,
        "needs_render": True,
        "ready_for_posting": False,
        "content_duration_estimate_sec": round(sum(scene.duration for scene in scenes), 2),
        "fixed_outro_expected": False,
    }
    _write_json(bundle / "manifest.json", manifest)
    return {
        "bundle_path": str(bundle),
        "story_path": str(bundle / "story.json"),
        "scenes_path": str(bundle / "scenes.json"),
        "captions_path": str(bundle / "captions.srt"),
        "thumbnail_path": str(bundle / "thumbnail.png"),
        "manifest": manifest,
    }


def update_prepared_story(
    bundle_path: str | Path,
    *,
    hook: str | None = None,
    takeaway: str | None = None,
    status: str = "edited",
) -> dict[str, Any]:
    bundle = Path(bundle_path)
    story_path = bundle / "story.json"
    manifest_path = bundle / "manifest.json"
    story = Story.model_validate(json.loads(story_path.read_text(encoding="utf-8")))
    if hook is not None:
        story.hook = hook
    if takeaway is not None:
        story.takeaway = takeaway
    manifest = _read_json(manifest_path)
    return write_prepared_bundle(
        story,
        event_id=int(manifest["event_id"]),
        chart_path=_chart_path(int(manifest["event_id"])),
        template=manifest.get("template", "news-studio"),
        max_duration=75,
        status=status,
    )


def _chart_path(event_id: int) -> str | None:
    rows = query(
        "SELECT file_path FROM assets WHERE event_id = ? AND asset_type = 'chart' LIMIT 1",
        (event_id,),
    )
    return rows[0]["file_path"] if rows else None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
