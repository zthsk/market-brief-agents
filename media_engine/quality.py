from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from media_engine.probe import probe_media
from media_engine.schemas.production import SyncReport
from media_engine.story_schema import Caption, QualityReport, Scene, Story


def build_quality_report(
    video_path: Path,
    story: Story,
    scenes: list[Scene],
    captions: list[Caption],
    max_duration: int,
    content_video_path: Path | None = None,
    production_plan_path: Path | None = None,
    contact_sheet_path: Path | None = None,
    captions_path: Path | None = None,
    template_selection_path: Path | None = None,
    outro_path: Path | None = None,
    sync_report: SyncReport | dict[str, Any] | None = None,
) -> QualityReport:
    duration, resolution, has_audio = probe_media(video_path)
    content_duration = (
        probe_media(content_video_path)[0] if content_video_path else sum(scene.duration for scene in scenes)
    )
    warnings: list[str] = []
    critical_warnings: list[str] = []
    if not video_path.exists():
        critical_warnings.append("Final video file is missing.")
    if content_duration > max_duration:
        warnings.append(f"Content duration {content_duration:.1f}s exceeds {max_duration}s target.")
    if max_duration >= 60 and content_duration < 60:
        warnings.append(f"Content duration {content_duration:.1f}s is below the 60s story target.")
    if resolution != "1080x1920":
        critical_warnings.append(f"Resolution is {resolution}, expected 1080x1920.")
    if not captions:
        critical_warnings.append("Captions are missing.")
    if captions_path is not None and not captions_path.exists():
        critical_warnings.append("Captions artifact is missing.")
    has_chart_scene = any(
        scene.scene_type == "chart"
        or any(item in scene.visual_requirements for item in ("stock_chart", "price_move", "volume_chart"))
        for scene in scenes
    )
    if not has_chart_scene:
        warnings.append("Chart scene is missing.")
    if not story.disclaimer:
        critical_warnings.append("Disclaimer is missing.")
    if not has_audio:
        critical_warnings.append("Audio is missing.")
    plan_payload = _read_json(production_plan_path)
    has_production_plan = bool(plan_payload)
    template_payload = _read_json(template_selection_path)
    has_template_selection = bool(template_payload)
    template_id = plan_payload.get("template_id") or template_payload.get("selected_template_id")
    if production_plan_path is not None and not has_production_plan:
        critical_warnings.append("Production plan is missing.")
    if template_selection_path is not None and not has_template_selection:
        critical_warnings.append("Template selection artifact is missing.")
    plan_scenes = [scene for scene in plan_payload.get("scenes", []) if isinstance(scene, dict)]
    scene_audio_count = sum(
        1
        for scene in plan_scenes
        if scene.get("measured_audio_duration") and scene.get("audio_path")
    )
    has_scene_audio = scene_audio_count > 0
    audio_mode = str(plan_payload.get("audio_mode") or "estimated")
    has_continuous_narration = _has_continuous_narration(plan_payload)
    if has_production_plan and audio_mode == "continuous_audio" and not has_continuous_narration:
        critical_warnings.append("Continuous narration audio is missing.")
    elif has_production_plan and not has_scene_audio and not has_continuous_narration:
        critical_warnings.append("Narration scene audio is missing.")
    elif (
        has_production_plan
        and audio_mode == "scene_audio"
        and scene_audio_count < len(plan_scenes)
    ):
        critical_warnings.append("Narration scene audio is incomplete.")
    has_contact_sheet = bool(contact_sheet_path and contact_sheet_path.exists())
    if contact_sheet_path is not None and not has_contact_sheet:
        critical_warnings.append("Contact sheet is missing.")
    has_outro = bool(outro_path and outro_path.exists())
    if outro_path is not None and not has_outro:
        critical_warnings.append("Fixed outro video is missing.")
    sync_passed = _sync_passed(sync_report)
    if sync_report is not None and not sync_passed:
        critical_warnings.append("Sync report did not pass.")
    max_scene = max((scene.duration for scene in scenes), default=0)
    if any(scene.duration > 7.0 and scene.scene_type != "chart" for scene in scenes):
        warnings.append("A non-chart scene is longer than 7 seconds.")
    for scene in scenes:
        for warning in scene.template_warnings:
            warnings.append(f"Template warning: {warning}")
    for scene in scenes:
        if _word_count(scene.headline) > 12 or _word_count(scene.subheadline) > 12:
            warnings.append(f"Scene text is too long: {scene.scene_type}.")
            break
    all_warnings = [*critical_warnings, *warnings]
    return QualityReport(
        passed=not critical_warnings,
        duration_sec=duration,
        content_duration_sec=content_duration,
        final_duration_sec=duration,
        resolution=resolution,
        has_hook=bool(scenes and scenes[0].scene_type == "hook"),
        has_captions=bool(captions),
        has_disclaimer=bool(story.disclaimer),
        has_chart=has_chart_scene,
        has_audio=has_audio,
        max_scene_duration=max_scene,
        has_production_plan=has_production_plan,
        has_contact_sheet=has_contact_sheet,
        has_outro=has_outro,
        has_template_selection=has_template_selection,
        template_id=template_id,
        sync_passed=sync_passed,
        ready_for_posting=False,
        critical_warnings=critical_warnings,
        warnings=all_warnings,
    )


def write_quality_report(path: Path, report: QualityReport) -> None:
    path.write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")


def _sync_passed(sync_report: SyncReport | dict[str, Any] | None) -> bool:
    if sync_report is None:
        return False
    if isinstance(sync_report, SyncReport):
        return sync_report.passed
    return bool(sync_report.get("passed"))


def _read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _has_continuous_narration(plan_payload: dict[str, Any]) -> bool:
    if plan_payload.get("audio_mode") != "continuous_audio":
        return False
    duration = plan_payload.get("narration_audio_duration")
    path = plan_payload.get("narration_audio_path")
    if not duration or not path:
        return False
    return Path(str(path)).exists()


def _word_count(value: str) -> int:
    return len(str(value or "").split())
