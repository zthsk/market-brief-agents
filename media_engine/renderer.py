from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from media_engine.paths import (
    AUDIO_ROOT,
    OUTRO_PATH,
    RENDER_ROOT,
    SCRIPT_MANIFEST_ROOT,
    SCRIPT_ROOT,
    VIDEO_ROOT,
    artifact_stem_for_event,
    review_bundle_dir,
)
from media_engine.captions import caption_beats_to_json, captions_to_srt
from media_engine.chart_animation import render_price_chart_animation
from media_engine.probe import media_duration, media_has_audio
from media_engine.production import (
    apply_production_plan_to_scenes,
    build_production_plan,
    build_sync_report,
    captions_from_plan,
    estimated_audio_duration,
    motion_preset,
)
from media_engine.quality import build_quality_report, write_quality_report
from media_engine.remotion import (
    build_remotion_render_input,
    render_remotion_video,
    video_renderer_mode,
    write_remotion_input,
)
from media_engine.scene_builder import build_scenes
from media_engine.scene_assets import render_scene_assets
from media_engine.schemas.templates import TemplateSelectionResult, VideoTemplate
from media_engine.schemas.production import ProductionPlan, SyncReport
from media_engine.script_schema import GeneratedScriptPackage
from media_engine.story_builder import build_story
from media_engine.story_schema import Scene
from media_engine.templates.news_studio import render_scene_frame, render_thumbnail
from media_engine.templates.selector import (
    apply_template_to_scenes,
    build_template_context,
    load_video_template,
    record_template_usage,
    select_video_template,
)
from models.database import execute, query
from services.config import load_env_file
from services.gemini import gemini_configured, generate_tts_wav
from services.logging_utils import get_logger
from services.voice_profile import briefing_tts_prompt, scene_transcript


LOGGER = get_logger(__name__)
BACKGROUND_MUSIC_VOLUME = 0.4


def render_video_bundle(
    script_row: dict,
    assets: dict[str, str] | None = None,
    *,
    template: str = "news-studio",
    max_duration: int = 75,
    captions: bool = True,
    renderer: str | None = None,
) -> dict | None:
    load_env_file()
    if template not in {"news-studio", "news_studio"}:
        raise ValueError(f"Unsupported video template: {template}")
    if shutil.which("ffmpeg") is None:
        LOGGER.warning("Skipping video for script %s: ffmpeg is not installed.", script_row["id"])
        return None
    script_id = int(script_row["id"])
    event = query("SELECT * FROM events WHERE id = ?", (script_row["event_id"],))[0]
    artifact_stem = artifact_stem_for_event(event)
    assets = assets or {
        row["asset_type"]: row["file_path"]
        for row in query("SELECT asset_type, file_path FROM assets WHERE event_id = ?", (event["id"],))
    }
    research = query("SELECT * FROM research_sources WHERE event_id = ?", (event["id"],))
    story = build_story(event, script_row, research)
    package = _script_package(artifact_stem)
    scenes = (
        _scenes_from_package(package, assets, max_duration=max_duration)
        if package
        else build_scenes(story, assets.get("chart"), max_duration=max_duration)
    )
    template_context = build_template_context(
        story=story,
        event=event,
        scenes=scenes,
        assets=assets,
        script_row=script_row,
        artifact_stem=artifact_stem,
    )
    template_selection = select_video_template(template_context)
    selected_video_template = load_video_template(template_selection.selected_template_id)
    scenes = apply_template_to_scenes(scenes, selected_video_template, story, template_context)
    _prioritize_opening_market_context(scenes)
    bundle = review_bundle_dir(story.ticker, story.date, int(event["id"]))
    frames_dir = bundle / "frames"
    audio_dir = bundle / "audio"
    scene_assets_dir = bundle / "scene_assets"
    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    scene_assets_dir.mkdir(parents=True, exist_ok=True)
    RENDER_ROOT.mkdir(parents=True, exist_ok=True)
    VIDEO_ROOT.mkdir(parents=True, exist_ok=True)

    video_path = bundle / "video.mp4"
    final_video = VIDEO_ROOT / f"{artifact_stem}.mp4"
    audio_result = _attach_scene_audio(
        script_id,
        scenes,
        script_row,
        artifact_stem,
        audio_dir,
    )
    production_plan = build_production_plan(
        video_id=artifact_stem,
        output_video_path=final_video,
        scenes=scenes,
        assets=assets,
        max_duration=max_duration,
        tts_configured=audio_result["configured"],
        audio_generation_attempted=audio_result["attempted"],
        scene_audio_warnings=audio_result["warnings"],
        template_id=selected_video_template.template_id,
        template_name=selected_video_template.name,
        template_selection_reason=template_selection.reason,
        template_candidates=template_selection.candidate_template_ids,
        narration_audio_path=audio_result.get("full_audio_path"),
        narration_audio_duration=audio_result.get("full_audio_duration"),
    )
    apply_production_plan_to_scenes(scenes, production_plan)
    story_captions = captions_from_plan(production_plan) if captions else []
    caption_beats = [beat for scene in production_plan.scenes for beat in scene.caption_beats]
    scene_asset_manifest = render_scene_assets(scene_assets_dir, story, scenes)
    source_map = _source_map(artifact_stem, scenes)
    timing_plan = _timing_plan_from_production_plan(production_plan)
    render_plan = _render_plan(story, scenes, source_map, scene_asset_manifest)
    sync_report = build_sync_report(video_id=artifact_stem, plan=production_plan)

    _write_json(bundle / "story.json", story.model_dump())
    _write_json(bundle / "scenes.json", [scene.model_dump() for scene in scenes])
    _write_json(
        bundle / "template_selection.json",
        _template_selection_payload(template_selection, selected_video_template),
    )
    _write_json(bundle / "production_plan.json", production_plan.model_dump())
    _write_json(bundle / "timing_plan.json", timing_plan)
    _write_json(bundle / "render_plan.json", render_plan)
    _write_json(bundle / "scene_assets.json", scene_asset_manifest)
    _write_json(bundle / "source_map.json", source_map)
    _write_json(bundle / "captions.json", caption_beats_to_json(caption_beats))
    _write_json(bundle / "sync_report.json", sync_report.model_dump())
    (bundle / "captions.srt").write_text(captions_to_srt(story_captions), encoding="utf-8")
    render_thumbnail(bundle / "thumbnail.png", story, assets.get("chart"))

    content_video = RENDER_ROOT / f"{artifact_stem}_media_content.mp4"
    renderer_requested = _renderer_mode(renderer)
    renderer_used = renderer_requested
    explicit_remotion = renderer is not None and renderer_requested == "remotion"
    try:
        scene_audio_expected = any(scene.audio_path and Path(scene.audio_path).exists() for scene in scenes)
        if renderer_requested == "remotion" and scene_audio_expected:
            message = (
                "Remotion renderer requested for %s, but only per-scene audio is available; "
                "Python renderer is required for this render."
            )
            if explicit_remotion:
                raise RuntimeError(message % artifact_stem)
            LOGGER.warning(message, artifact_stem)
            renderer_used = "python"

        if renderer_used == "remotion":
            try:
                visual_video = _render_remotion_visual_track(
                    bundle,
                    artifact_stem,
                    story,
                    scenes,
                    production_plan,
                    assets,
                    source_map,
                    scene_asset_manifest,
                )
            except Exception as exc:
                LOGGER.warning(
                    "Remotion render failed for %s; falling back to Python renderer: %s",
                    artifact_stem,
                    exc,
                )
                _write_json(
                    bundle / "remotion_render.json",
                    {
                        "renderer": "remotion",
                        "passed": False,
                        "error": str(exc),
                    },
                )
                if explicit_remotion:
                    raise
                renderer_used = "python"
                visual_video, scene_frames, scene_audio_expected = _render_python_visual_track(
                    bundle,
                    artifact_stem,
                    story,
                    scenes,
                    frames_dir,
                    production_plan,
                    captions,
                )
                _contact_sheet(scene_frames, bundle / "contact_sheet.png", production_plan)
        else:
            visual_video, scene_frames, scene_audio_expected = _render_python_visual_track(
                bundle,
                artifact_stem,
                story,
                scenes,
                frames_dir,
                production_plan,
                captions,
            )
            _contact_sheet(scene_frames, bundle / "contact_sheet.png", production_plan)

        content_video = _mux_visual_track_audio(
            visual_video,
            audio_result,
            script_row,
            production_plan,
            scene_audio_expected,
            artifact_stem,
            bundle=bundle if renderer_used == "remotion" else None,
        )
        sync_report = build_sync_report(
            video_id=artifact_stem,
            plan=production_plan,
            content_video_duration=media_duration(content_video),
        )
        _write_json(bundle / "sync_report.json", sync_report.model_dump())
        _write_content_video(content_video, video_path)
        shutil.copyfile(video_path, final_video)
    except Exception as exc:
        LOGGER.warning("Skipping video for script %s after media render error: %s", script_id, exc)
        return None

    report = build_quality_report(
        video_path,
        story,
        scenes,
        story_captions,
        max_duration,
        content_video_path=content_video if "content_video" in locals() else None,
        production_plan_path=bundle / "production_plan.json",
        contact_sheet_path=bundle / "contact_sheet.png",
        captions_path=bundle / "captions.srt",
        template_selection_path=bundle / "template_selection.json",
        outro_path=None,
        sync_report=sync_report,
    )
    report.ready_for_posting = _ready_for_posting(final_video, report, sync_report)
    write_quality_report(bundle / "quality_report.json", report)
    _write_render_manifest(
        bundle,
        event,
        story,
        production_plan,
        sync_report,
        report,
        final_video,
        video_path,
        template_selection,
        scene_asset_manifest,
        renderer=renderer_used,
    )
    _record_template_usage_compat(template_selection, template_context, artifact_stem)
    return {
        "video_path": str(final_video),
        "bundle_path": str(bundle),
        "template_id": selected_video_template.template_id,
        "template_name": selected_video_template.name,
        "story_type": template_selection.story_type,
        "template_reason": template_selection.reason,
        "quality_report": report.model_dump(),
        "sync_report": sync_report.model_dump(),
        "ready_for_posting": report.ready_for_posting,
        "renderer": renderer_used,
        "warnings": len(report.warnings) + len(sync_report.warnings),
    }


def _script_package(stem: str) -> GeneratedScriptPackage | None:
    path = SCRIPT_ROOT / stem / "script.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        package = payload.get("package")
        if isinstance(package, dict):
            return GeneratedScriptPackage.model_validate(_package_with_card_bullets(package))
    except Exception as exc:
        LOGGER.warning("Ignoring generated script package at %s: %s", path, exc)
    return None


def _package_with_card_bullets(package: dict) -> dict:
    patched = dict(package)
    scenes = []
    for scene in patched.get("scenes") or []:
        if not isinstance(scene, dict):
            scenes.append(scene)
            continue
        copy = dict(scene)
        copy["highlights"] = _compatible_scene_bullets(copy)
        scenes.append(copy)
    patched["scenes"] = scenes
    return patched


def _compatible_scene_bullets(scene: dict) -> list[str]:
    text = scene.get("on_screen_text") if isinstance(scene.get("on_screen_text"), dict) else {}
    candidates = [
        *(scene.get("highlights") or []),
        text.get("subheadline"),
        scene.get("narration"),
        text.get("headline"),
    ]
    bullets: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for chunk in _compatible_bullet_chunks(str(candidate or "")):
            bullet = _clean_compatible_bullet(chunk)
            key = re.sub(r"[^a-z0-9]+", " ", bullet.lower()).strip()
            if not bullet or key in seen:
                continue
            bullets.append(bullet)
            seen.add(key)
            if len(bullets) == 3:
                return bullets
    for fallback in ("Facts decide what lasts", "Watch confirmation next", "Execution still matters"):
        key = re.sub(r"[^a-z0-9]+", " ", fallback.lower()).strip()
        if key not in seen:
            bullets.append(fallback)
            seen.add(key)
        if len(bullets) == 3:
            break
    return bullets[:3]


def _compatible_bullet_chunks(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|[;|•\n]+", text)
    return [chunk.strip(" .,:;\"'") for chunk in chunks if chunk.strip(" .,:;\"'")]


def _clean_compatible_bullet(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;\"'")
    text = re.sub(r"^(the\s+)?(takeaway|risk|caveat|catalyst|context)\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    words = text.split()
    while words and words[-1].strip(".,;:'\"").lower() in {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "for",
        "from",
        "of",
        "or",
        "the",
        "to",
        "with",
    }:
        words.pop()
    cleaned = " ".join(words[:10]).strip(" .,:;\"'")
    if len(cleaned.split()) < 2:
        return ""
    if cleaned.lower() in {"risk", "takeaway", "caveat", "catalyst", "chart check", "price action"}:
        return ""
    return cleaned


def _scenes_from_package(
    package: GeneratedScriptPackage,
    assets: dict[str, str],
    *,
    max_duration: int,
) -> list[Scene]:
    target = min(max_duration, package.video_metadata.estimated_duration_seconds)
    duration = max(4.0, target / max(1, len(package.scenes)))
    scenes = [
        Scene(
            scene_type=item.type,
            duration=duration,
            headline=item.on_screen_text.headline,
            subheadline=item.on_screen_text.subheadline,
            bullets=item.highlights,
            narration=item.narration,
            caption_text=item.on_screen_text.headline,
            chart_path=_chart_asset_for_scene(
                [requirement.asset_type for requirement in item.visual_requirements],
                assets,
            ),
            importance=item.importance,
            confidence_level=item.confidence_level,
            source_ids=item.source_ids,
            visual_requirements=[requirement.asset_type for requirement in item.visual_requirements],
        )
        for item in package.scenes
    ]
    _apply_progress(scenes)
    return scenes


def _chart_asset_for_scene(requirements: list[str], assets: dict[str, str]) -> str | None:
    if any(item in requirements for item in ("stock_chart", "price_move", "volume_chart")):
        return assets.get("chart")
    return None


def _apply_progress(scenes: list[Scene]) -> None:
    total = sum(scene.duration for scene in scenes) or 1
    elapsed = 0.0
    for scene in scenes:
        scene.progress_start = elapsed / total
        elapsed += scene.duration
        scene.progress_end = elapsed / total


def _renderer_mode(renderer: str | None) -> str:
    if renderer is None:
        return video_renderer_mode()
    return "remotion" if str(renderer).strip().lower() == "remotion" else "python"


def _prioritize_opening_market_context(scenes: list[Scene]) -> None:
    if len(scenes) < 3:
        return
    target_index = next(
        (
            index
            for index, scene in enumerate(scenes)
            if index > 1 and _is_market_context_scene(scene)
        ),
        None,
    )
    if target_index is None:
        return
    target = scenes.pop(target_index)
    scenes.insert(1, target)
    _apply_progress(scenes)


def _is_market_context_scene(scene: Scene) -> bool:
    return bool(
        scene.card_type == "chart_card"
        or scene.scene_type in {"chart", "timeline"}
        or (
            scene.card_type == "price_move_card"
            and scene.scene_type in {"price", "price_card", "price_action"}
        )
    )


def review_bundles() -> list[dict]:
    root = review_bundle_dir("", "", 0).parent
    if not root.exists():
        return []
    bundles = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        quality_path = path / "quality_report.json"
        story_path = path / "story.json"
        manifest_path = path / "manifest.json"
        production_path = path / "production_plan.json"
        sync_path = path / "sync_report.json"
        template_path = path / "template_selection.json"
        scene_assets_path = path / "scene_assets.json"
        animated_clips_path = path / "animated_clips.json"
        remotion_input_path = path / "remotion_input.json"
        remotion_render_path = path / "remotion_render.json"
        quality = _read_json(quality_path)
        story = _read_json(story_path)
        manifest = _read_json(manifest_path)
        production = _read_json(production_path)
        sync = _read_json(sync_path)
        template_selection = _read_json(template_path)
        scene_assets = _read_json(scene_assets_path)
        animated_clips = _read_json(animated_clips_path)
        remotion_input = _read_json(remotion_input_path)
        remotion_render = _read_json(remotion_render_path)
        bundles.append(
            {
                "bundle_path": str(path),
                "video_path": str(path / "video.mp4"),
                "thumbnail_path": str(path / "thumbnail.png"),
                "story": story,
                "quality": quality,
                "manifest": manifest,
                "production": production,
                "sync": sync,
                "template_selection": template_selection,
                "scene_assets": scene_assets,
                "animated_clips": animated_clips,
                "remotion_input": remotion_input,
                "remotion_render": remotion_render,
            }
        )
    return bundles


def _render_python_visual_track(
    bundle: Path,
    stem: str,
    story,
    scenes: list[Scene],
    frames_dir: Path,
    production_plan: ProductionPlan,
    captions: bool,
) -> tuple[Path, list[Path], bool]:
    scene_clips = []
    scene_frames = []
    animated_clips = []
    scene_audio_expected = any(scene.audio_path and Path(scene.audio_path).exists() for scene in scenes)
    for index, scene in enumerate(scenes):
        animated_clip = _render_animated_chart_clip(stem, index, story, scene)
        frame_scene = _safe_frame_scene(scene) if _should_animate_chart_scene(scene) else scene
        frame = render_scene_frame(
            frames_dir / f"scene_{index:02d}.png",
            story,
            frame_scene,
            index,
            len(scenes),
            captions=captions,
        )
        scene_frames.append(frame)
        if animated_clip:
            scene_clip = animated_clip
            animated_clips.append(
                {
                    "scene_index": index,
                    "scene_type": scene.scene_type,
                    "card_type": scene.card_type,
                    "animation_type": "price_chart_reveal",
                    "duration": round(scene.duration, 3),
                    "path": str(animated_clip),
                }
            )
        else:
            scene_clip = _render_scene_clip_compat(
                stem,
                index,
                frame,
                scene.duration,
                scene.motion or motion_preset(scene.scene_type),
            )
        if scene.audio_path and Path(scene.audio_path).exists():
            muxed_clip = RENDER_ROOT / f"{stem}_media_scene_{index}_audio.mp4"
            _mux_audio(scene_clip, Path(scene.audio_path), muxed_clip, duration=scene.duration)
            scene_clip = muxed_clip
        elif scene_audio_expected:
            muxed_clip = RENDER_ROOT / f"{stem}_media_scene_{index}_silence.mp4"
            _mux_silent_audio(scene_clip, scene.duration, muxed_clip)
            scene_clip = muxed_clip
        scene_clips.append(scene_clip)
    _write_json(bundle / "animated_clips.json", animated_clips)
    visual_video = RENDER_ROOT / f"{stem}_media_silent.mp4"
    _concat_clips(scene_clips, visual_video, stem, "media")
    return visual_video, scene_frames, scene_audio_expected


def _render_remotion_visual_track(
    bundle: Path,
    stem: str,
    story,
    scenes: list[Scene],
    production_plan: ProductionPlan,
    assets: dict[str, str],
    source_map: dict,
    scene_asset_manifest: dict,
) -> Path:
    public_dir = bundle / "remotion_public"
    input_path = bundle / "remotion_input.json"
    output_path = RENDER_ROOT / f"{stem}_media_remotion_silent.mp4"
    payload = build_remotion_render_input(
        story=story,
        scenes=scenes,
        plan=production_plan,
        assets=assets,
        source_map=source_map,
        scene_asset_manifest=scene_asset_manifest,
        public_dir=public_dir,
    )
    write_remotion_input(input_path, payload)
    result = render_remotion_video(
        input_path=input_path,
        output_path=output_path,
        public_dir=public_dir,
    )
    result["passed"] = output_path.exists()
    result["background_segments"] = [
        segment.model_dump() for segment in payload.background_segments
    ]
    result["music_track"] = payload.music_track.model_dump() if payload.music_track else None
    _write_json(bundle / "remotion_render.json", result)
    _write_json(bundle / "animated_clips.json", _remotion_animated_clips(scenes))
    return output_path


def _remotion_animated_clips(scenes: list[Scene]) -> list[dict]:
    clips = []
    chart_reveal_used = False
    for index, scene in enumerate(scenes):
        uses_chart_reveal = (scene.card_type == "hook_card" and bool(scene.chart_path)) or (
            _should_animate_chart_scene(scene) and not chart_reveal_used
        )
        if not uses_chart_reveal:
            continue
        clips.append(
            {
                "scene_index": index,
                "scene_type": scene.scene_type,
                "card_type": scene.card_type,
                "animation_type": "remotion_price_chart_reveal",
                "duration": round(scene.duration, 3),
                "path": None,
            }
        )
        chart_reveal_used = True
    return clips


def _mux_visual_track_audio(
    visual_video: Path,
    audio_result: dict,
    script_row: dict,
    production_plan: ProductionPlan,
    scene_audio_expected: bool,
    stem: str,
    *,
    bundle: Path | None = None,
) -> Path:
    content_video = RENDER_ROOT / f"{stem}_media_content.mp4"
    if scene_audio_expected:
        return visual_video
    if audio_result.get("full_audio_path") and Path(str(audio_result["full_audio_path"])).exists():
        _mux_narration_with_optional_music(
            visual_video=visual_video,
            narration_path=Path(str(audio_result["full_audio_path"])),
            output=content_video,
            duration=production_plan.total_duration,
            bundle=bundle,
        )
        return content_video
    if script_row.get("audio_path") and Path(script_row["audio_path"]).exists():
        _mux_narration_with_optional_music(
            visual_video=visual_video,
            narration_path=Path(script_row["audio_path"]),
            output=content_video,
            duration=production_plan.total_duration,
            bundle=bundle,
        )
        return content_video
    return visual_video


def _render_scene_clip(
    stem: str,
    index: int,
    frame: Path,
    duration: float,
    *,
    motion: str | None = None,
) -> Path:
    output = RENDER_ROOT / f"{stem}_media_scene_{index}.mp4"
    frame_count = max(1, int(duration * 30))
    fade_out_start = max(0.1, duration - 0.25)
    zoom, x_expr, y_expr = _motion_filter(motion or ("punch_in" if index % 2 == 0 else "takeaway_hold"))
    filtergraph = (
        "scale=1200:2134,"
        f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':"
        f"d={frame_count}:s=1080x1920:fps=30,"
        "fade=t=in:st=0:d=0.18,"
        f"fade=t=out:st={fade_out_start:.3f}:d=0.18,"
        "format=yuv420p"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(frame),
            "-t",
            f"{duration:.3f}",
            "-vf",
            filtergraph,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-r",
            "30",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return output


def _render_scene_clip_compat(
    stem: str,
    index: int,
    frame: Path,
    duration: float,
    motion: str,
) -> Path:
    try:
        return _render_scene_clip(stem, index, frame, duration, motion=motion)
    except TypeError:
        return _render_scene_clip(stem, index, frame, duration)


def _render_animated_chart_clip(
    stem: str,
    index: int,
    story,
    scene: Scene,
) -> Path | None:
    if not _should_animate_chart_scene(scene):
        return None
    output = RENDER_ROOT / f"{stem}_media_scene_{index}_chart.mp4"
    try:
        return render_price_chart_animation(story, scene, output, duration=scene.duration)
    except Exception as exc:
        LOGGER.warning("Falling back to static chart scene %s after animation error: %s", index, exc)
        return None


def _should_animate_chart_scene(scene: Scene) -> bool:
    return bool(
        scene.card_type == "chart_card"
        or scene.scene_type in {"chart", "timeline"}
    )


def _safe_frame_scene(scene: Scene) -> Scene:
    return scene.model_copy(update={"chart_path": None})


def _motion_filter(motion: str) -> tuple[str, str, str]:
    if motion == "chart_pan":
        return "min(zoom+0.0008,1.05)", "iw/2-(iw/zoom/2)+(on*0.18)", "ih/2-(ih/zoom/2)"
    if motion == "stat_reveal":
        return "min(zoom+0.0012,1.055)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)-(on*0.05)"
    if motion == "slide_up":
        return "min(zoom+0.0009,1.04)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)-(on*0.08)"
    if motion == "bullet_stagger":
        return "min(zoom+0.0007,1.035)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)+(on*0.04)"
    if motion == "split_reveal":
        return "min(zoom+0.0006,1.03)", "iw/2-(iw/zoom/2)+(on*0.05)", "ih/2-(ih/zoom/2)"
    if motion == "warning_pulse":
        return "max(1.035-on/2100,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "risk_contrast":
        return "max(1.045-on/1800,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "takeaway_hold":
        return "min(zoom+0.00025,1.015)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    return "min(zoom+0.0010,1.06)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"


def _concat_clips(clips: list[Path], output: Path, stem: str, label: str) -> None:
    concat_file = RENDER_ROOT / f"{stem}_{label}.txt"
    with concat_file.open("w", encoding="utf-8") as handle:
        for clip in clips:
            handle.write(f"file '{clip.resolve()}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _mux_narration_with_optional_music(
    *,
    visual_video: Path,
    narration_path: Path,
    output: Path,
    duration: float,
    bundle: Path | None,
) -> None:
    music_track = _remotion_music_track(bundle)
    music_path = _music_path_from_track(music_track, bundle)
    if music_track and music_path and music_path.exists():
        _mux_audio_with_background_music(
            visual_video,
            narration_path,
            music_path,
            output,
            duration=duration,
            music_volume=float(music_track.get("volume") or BACKGROUND_MUSIC_VOLUME),
        )
        return
    _mux_audio(visual_video, narration_path, output, duration=duration)


def _remotion_music_track(bundle: Path | None) -> dict | None:
    if not bundle:
        return None
    payload = _read_json(bundle / "remotion_input.json")
    music_track = payload.get("music_track") if isinstance(payload, dict) else None
    return music_track if isinstance(music_track, dict) else None


def _music_path_from_track(music_track: dict | None, bundle: Path | None) -> Path | None:
    if not music_track:
        return None
    source_path = music_track.get("source_path")
    if source_path:
        path = Path(str(source_path))
        if path.exists():
            return path
    public_path = str(music_track.get("public_path") or "").lstrip("/")
    if bundle and public_path:
        path = bundle / "remotion_public" / public_path
        if path.exists():
            return path
    return None


def _mux_audio(video_path: Path, audio_path: Path, output: Path, duration: float | None = None) -> None:
    audio_filter = "loudnorm=I=-16:LRA=11:TP=-1.5"
    if duration:
        audio_filter = f"{audio_filter},apad"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if duration:
        cmd.extend(["-t", f"{duration:.3f}"])
    else:
        cmd.append("-shortest")
    cmd.extend(
        [
            "-af",
            audio_filter,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ]
    )
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _mux_audio_with_background_music(
    video_path: Path,
    narration_path: Path,
    music_path: Path,
    output: Path,
    *,
    duration: float,
    music_volume: float = BACKGROUND_MUSIC_VOLUME,
) -> None:
    volume = max(0.0, min(1.0, music_volume))
    fade_out_start = max(0.0, duration - 0.9)
    filtergraph = (
        "[1:a]loudnorm=I=-16:LRA=11:TP=-1.5,apad,asplit=2[narrmix][narrside];"
        f"[2:a]volume={volume:.3f},afade=t=in:st=0:d=0.6,"
        f"afade=t=out:st={fade_out_start:.3f}:d=0.8[music];"
        "[music][narrside]sidechaincompress=threshold=0.08:ratio=6:attack=20:release=450[ducked];"
        "[narrmix][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(narration_path),
            "-stream_loop",
            "-1",
            "-i",
            str(music_path),
            "-filter_complex",
            filtergraph,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _mux_silent_audio(video_path: Path, duration: float, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _append_outro(content_video: Path, output: Path, stem: str) -> None:
    if not OUTRO_PATH.exists():
        _normalize_for_concat(content_video, output, add_silent_audio=not _has_audio(content_video))
        return
    normalized_content = RENDER_ROOT / f"{stem}_media_content_normalized.mp4"
    normalized_outro = RENDER_ROOT / f"{stem}_media_outro.mp4"
    _normalize_for_concat(content_video, normalized_content, add_silent_audio=not _has_audio(content_video))
    _normalize_for_concat(OUTRO_PATH, normalized_outro, add_silent_audio=not _has_audio(OUTRO_PATH))
    _concat_clips([normalized_content, normalized_outro], output, stem, "media_final")


def _write_content_video(content_video: Path, output: Path) -> None:
    if content_video.resolve() == output.resolve():
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(content_video, output)


def _normalize_for_concat(input_path: Path, output: Path, add_silent_audio: bool = False) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    if add_silent_audio:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
    cmd.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0" if add_silent_audio else "0:a:0",
            "-shortest",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output),
        ]
    )
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _has_audio(path: Path) -> bool:
    return media_has_audio(path)


def _attach_scene_audio(
    script_id: int,
    scenes: list[Scene],
    script_row: dict,
    stem: str,
    audio_dir: Path,
) -> dict:
    existing_full_audio = _existing_full_audio(stem, script_row)
    if existing_full_audio:
        return _attach_existing_full_audio(script_id, scenes, stem, audio_dir, existing_full_audio)
    existing_scene_audio = _attach_existing_scene_audio(script_id, scenes, stem, audio_dir)
    if existing_scene_audio["found"]:
        return existing_scene_audio
    configured = _scene_tts_configured()
    result = {"configured": configured, "attempted": False, "warnings": {}}
    if not configured:
        return result
    first_audio_path: str | None = None
    for index, scene in enumerate(scenes):
        text = scene_transcript([scene])
        scene_warnings = result["warnings"].setdefault(index, [])
        if not text:
            scene_warnings.append("Scene narration is empty; no scene audio generated.")
            continue
        suffix = ".wav" if os.getenv("TTS_PROVIDER", "").lower().strip() == "gemini" else ".mp3"
        path = audio_dir / f"{stem}_scene_{index:02d}{suffix}"
        try:
            result["attempted"] = True
            if not path.exists():
                _generate_scene_tts(text, path)
            measured = _audio_duration(str(path))
            if not measured:
                scene_warnings.append("Generated scene audio could not be measured.")
                continue
            scene.audio_path = str(path)
            scene.audio_duration = measured
            first_audio_path = first_audio_path or str(path)
        except Exception as exc:
            scene_warnings.append(f"Scene TTS failed: {exc}")
            LOGGER.warning("Scene TTS failed for script %s scene %s: %s", script_id, index, exc)
    if first_audio_path:
        execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (first_audio_path, script_id))
    result["warnings"] = {
        index: warnings
        for index, warnings in result["warnings"].items()
        if warnings
    }
    return result


def _existing_full_audio(stem: str, script_row: dict) -> Path | None:
    candidates = []
    for suffix in (".wav", ".mp3", ".m4a"):
        candidates.append(AUDIO_ROOT / f"{stem}{suffix}")
    attached = script_row.get("audio_path")
    if attached:
        attached_path = Path(attached)
        if "_scene_" not in attached_path.stem and "_existing_scene_" not in attached_path.stem:
            candidates.append(attached_path)
    for path in candidates:
        if path.exists() and _audio_duration(str(path)):
            return path
    return None


def _attach_existing_scene_audio(
    script_id: int,
    scenes: list[Scene],
    stem: str,
    audio_dir: Path,
) -> dict:
    result = {"configured": _scene_tts_configured(), "attempted": False, "warnings": {}, "found": False}
    first_audio_path: str | None = None
    existing_paths = {
        index: _existing_scene_audio_path(stem, audio_dir, index)
        for index, _scene in enumerate(scenes)
    }
    if not any(existing_paths.values()):
        return result
    result["found"] = True
    for index, scene in enumerate(scenes):
        path = existing_paths[index]
        if not path:
            result["warnings"].setdefault(index, []).append(
                "Existing scene audio set is incomplete; not regenerating automatically."
            )
            continue
        measured = _audio_duration(str(path))
        if not measured:
            result["warnings"].setdefault(index, []).append("Existing scene audio could not be measured.")
            continue
        scene.audio_path = str(path)
        scene.audio_duration = measured
        first_audio_path = first_audio_path or str(path)
    if first_audio_path:
        execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (first_audio_path, script_id))
    result["warnings"] = {
        index: warnings
        for index, warnings in result["warnings"].items()
        if warnings
    }
    return result


def _attach_existing_full_audio(
    script_id: int,
    scenes: list[Scene],
    _stem: str,
    _audio_dir: Path,
    audio_path: Path,
) -> dict:
    result = {"configured": _scene_tts_configured(), "attempted": False, "warnings": {}, "found": True}
    total_duration = _audio_duration(str(audio_path))
    if not total_duration:
        result["warnings"] = {0: ["Existing full audio could not be measured."]}
        return result
    for scene in scenes:
        scene.audio_path = None
        scene.audio_duration = None
    execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (str(audio_path), script_id))
    result["full_audio_path"] = str(audio_path)
    result["full_audio_duration"] = total_duration
    return result


def _audio_allocations_for_scenes(scenes: list[Scene], total_duration: float) -> list[float]:
    weights = [estimated_audio_duration(scene.narration) for scene in scenes]
    total_weight = sum(weights) or 1.0
    allocations = [max(0.1, total_duration * weight / total_weight) for weight in weights]
    drift = sum(allocations) - total_duration
    allocations[-1] = max(0.1, allocations[-1] - drift)
    return allocations


def _existing_scene_audio_path(stem: str, audio_dir: Path, index: int) -> Path | None:
    candidates = [
        audio_dir / f"{stem}_existing_scene_{index:02d}.wav",
        audio_dir / f"{stem}_scene_{index:02d}.wav",
        audio_dir / f"{stem}_scene_{index:02d}.mp3",
        AUDIO_ROOT / f"{stem}_scene_{index:02d}.wav",
        AUDIO_ROOT / f"{stem}_scene_{index:02d}.mp3",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _slice_audio(input_path: Path, output_path: Path, start: float, duration: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _scene_tts_configured() -> bool:
    provider = os.getenv("TTS_PROVIDER", "").lower().strip()
    if provider == "gemini":
        return gemini_configured()
    if provider and provider != "openai":
        return False
    return bool(os.getenv("OPENAI_API_KEY"))


def _generate_scene_tts(text: str, path: Path) -> Path:
    provider = os.getenv("TTS_PROVIDER", "").lower().strip()
    if provider == "gemini":
        return generate_tts_wav(text, path, voice_prompt=briefing_tts_prompt(text))
    if provider and provider != "openai":
        raise ValueError(f"Unsupported TTS_PROVIDER={provider!r}")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured")
    from openai import OpenAI

    path.parent.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    response = client.audio.speech.create(
        model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
        input=text,
    )
    response.write_to_file(path)
    return path


def _scene_audio_segments(
    script_id: int,
    scenes: list[Scene],
    fallback: str | None,
    stem: str,
) -> list[dict]:
    if os.getenv("TTS_PROVIDER", "").lower() != "gemini" or not gemini_configured():
        return []
    segments = []
    for index, scene in enumerate(scenes):
        text = scene_transcript([scene])
        if not text:
            continue
        path = AUDIO_ROOT / f"{stem}_scene_{index:02d}.wav"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            generate_tts_wav(text, path, voice_prompt=briefing_tts_prompt(text))
            measured = _audio_duration(str(path))
            scene.audio_path = str(path)
            scene.audio_duration = measured
            if measured:
                scene.duration = max(3.2, measured + _scene_padding(scene))
            segments.append(
                {
                    "scene_index": index,
                    "path": str(path),
                    "audio_duration": measured,
                    "planned_duration": scene.duration,
                }
            )
        except Exception as exc:
            LOGGER.warning("Using existing audio for script %s after scene %s TTS error: %s", script_id, index, exc)
            if fallback:
                return []
    if segments:
        execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (segments[0]["path"], script_id))
    return segments


def _legacy_audio_path(audio_segments: list[dict], fallback: str | None) -> str | None:
    if audio_segments:
        return None
    return fallback


def _scene_audio(script_id: int, scenes: list, fallback: str | None, stem: str) -> str | None:
    text = scene_transcript(scenes)
    if os.getenv("TTS_PROVIDER", "").lower() == "gemini" and gemini_configured():
        path = AUDIO_ROOT / f"{stem}_scene.wav"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            generate_tts_wav(text, path, voice_prompt=briefing_tts_prompt(text))
            execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (str(path), script_id))
            return str(path)
        except Exception as exc:
            LOGGER.warning("Using existing audio for script %s after scene TTS error: %s", script_id, exc)
    return fallback


def _scene_padding(scene: Scene) -> float:
    if scene.scene_type == "hook":
        return 0.28
    if scene.scene_type in {"risk", "conclusion", "takeaway"}:
        return 0.35
    return 0.22


def _audio_duration(audio_path: str | None) -> float | None:
    return media_duration(audio_path)


def _stretch_scenes_to_audio(scenes: list, audio_duration: float | None) -> None:
    if not audio_duration:
        return
    current = sum(scene.duration for scene in scenes)
    if current >= audio_duration + 0.25:
        return
    scale = (audio_duration + 0.5) / max(1, current)
    for scene in scenes:
        scene.duration *= scale
    total = sum(scene.duration for scene in scenes) or 1
    elapsed = 0.0
    for scene in scenes:
        scene.progress_start = elapsed / total
        elapsed += scene.duration
        scene.progress_end = elapsed / total


def _timing_plan_from_production_plan(plan: ProductionPlan) -> dict:
    return {
        "total_duration": plan.total_duration,
        "source": "production_plan",
        "audio_mode": plan.audio_mode,
        "narration_audio_path": plan.narration_audio_path,
        "narration_audio_duration": plan.narration_audio_duration,
        "scenes": [
            {
                "scene_index": scene.scene_index,
                "scene_type": scene.scene_type,
                "slot_id": scene.slot_id,
                "card_type": scene.card_type,
                "visual_style": scene.visual_style,
                "motion": scene.motion,
                "caption_style": scene.caption_style,
                "start": scene.start,
                "end": scene.end,
                "duration": scene.final_duration,
                "audio_path": scene.audio_path,
                "audio_duration": scene.measured_audio_duration,
                "estimated_audio_duration": scene.estimated_audio_duration,
                "timing_source": scene.timing_source,
                "padding_before": scene.padding_before,
                "padding_after": scene.padding_after,
                "warnings": scene.warnings,
                "beats": [beat.model_dump() for beat in scene.visual_beats],
            }
            for scene in plan.scenes
        ],
    }


def _timing_plan(scenes: list[Scene]) -> dict:
    items = []
    elapsed = 0.0
    for index, scene in enumerate(scenes):
        start = elapsed
        end = start + scene.duration
        items.append(
            {
                "scene_index": index,
                "scene_type": scene.scene_type,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(scene.duration, 3),
                "audio_path": scene.audio_path,
                "audio_duration": round(scene.audio_duration, 3) if scene.audio_duration else None,
                "padding": round(scene.duration - scene.audio_duration, 3) if scene.audio_duration else None,
                "source_ids": scene.source_ids,
                "confidence_level": scene.confidence_level,
                "beats": _scene_beats(scene, start),
            }
        )
        elapsed = end
    return {"total_duration": round(elapsed, 3), "scenes": items}


def _scene_beats(scene: Scene, start: float) -> list[dict]:
    labels = ["headline"]
    if scene.bullets:
        labels.extend(f"highlight:{item}" for item in scene.bullets[:3])
    if scene.subheadline:
        labels.append("subheadline")
    slot = scene.duration / max(1, len(labels))
    return [
        {
            "label": label,
            "start": round(start + slot * index, 3),
            "end": round(start + slot * (index + 1), 3),
        }
        for index, label in enumerate(labels)
    ]


def _render_plan(
    story,
    scenes: list[Scene],
    source_map: dict,
    scene_asset_manifest: dict | None = None,
) -> dict:
    assets_by_scene: dict[int, list[dict]] = {}
    for asset in (scene_asset_manifest or {}).get("assets", []):
        if isinstance(asset, dict):
            try:
                scene_index = int(asset.get("scene_index", 0))
            except (TypeError, ValueError):
                continue
            assets_by_scene.setdefault(scene_index, []).append(asset)
    return {
        "template": "news-studio",
        "video_template_id": scenes[0].template_id if scenes else None,
        "safe_area": {"x": 72, "y": 260, "width": 936, "height": 1260},
        "ticker": story.ticker,
        "scene_asset_templates": (scene_asset_manifest or {}).get("templates", []),
        "layouts": [
            {
                "scene_index": index,
                "scene_type": scene.scene_type,
                "slot_id": scene.slot_id,
                "card_type": scene.card_type,
                "visual_style": scene.visual_style,
                "motion": scene.motion or motion_preset(scene.scene_type),
                "caption_style": scene.caption_style,
                "layout": _layout_for_scene(scene),
                "headline": scene.headline,
                "subheadline": scene.subheadline,
                "highlights": scene.bullets,
                "visual_requirements": scene.visual_requirements,
                "scene_assets": assets_by_scene.get(index, []),
                "animation": (
                    {
                        "type": "price_chart_reveal",
                        "renderer": "matplotlib",
                        "duration": round(scene.duration, 3),
                    }
                    if _should_animate_chart_scene(scene)
                    else None
                ),
                "source_ids": scene.source_ids,
                "sources": [source_map.get(source_id, {}) for source_id in scene.source_ids],
            }
            for index, scene in enumerate(scenes)
        ],
    }


def _layout_for_scene(scene: Scene) -> str:
    if scene.card_type:
        return scene.card_type
    if scene.scene_type == "hook":
        return "impact_hook"
    if scene.scene_type == "price_action":
        return "price_move_chart"
    if scene.scene_type in {"earnings", "financials"}:
        return "metric_tiles"
    if scene.scene_type == "analyst":
        return "analyst_callout"
    if scene.scene_type == "risk":
        return "risk_counterpoint"
    if scene.scene_type in {"conclusion", "takeaway"}:
        return "takeaway_card"
    return "evidence_stack"


def _source_map(stem: str, scenes: list[Scene]) -> dict:
    manifest = _read_json(SCRIPT_MANIFEST_ROOT / stem / "manifest.json")
    rows = []
    for key in ("citable_sources", "context_sources"):
        values = manifest.get(key)
        if isinstance(values, list):
            rows.extend(item for item in values if isinstance(item, dict))
    wanted = {source_id for scene in scenes for source_id in scene.source_ids}
    mapped = {}
    for row in rows:
        source_id = str(row.get("source_id") or "")
        if source_id and (not wanted or source_id in wanted):
            mapped[source_id] = {
                "title": row.get("title"),
                "url": row.get("url"),
                "publisher": row.get("publisher") or row.get("source"),
                "usage_policy": row.get("usage_policy"),
            }
    return mapped


def _sync_report(scenes: list[Scene]) -> dict:
    warnings = []
    rows = []
    for index, scene in enumerate(scenes):
        drift = None
        if scene.audio_duration:
            drift = round(scene.duration - scene.audio_duration, 3)
            if drift < 0:
                warnings.append(f"scene {index} video is shorter than audio")
            elif drift > 1.0:
                warnings.append(f"scene {index} has more than one second of post-audio hold")
        rows.append(
            {
                "scene_index": index,
                "scene_type": scene.scene_type,
                "planned_duration": round(scene.duration, 3),
                "audio_duration": round(scene.audio_duration, 3) if scene.audio_duration else None,
                "drift_seconds": drift,
            }
        )
    return {
        "mode": "per_scene_audio" if any(scene.audio_path for scene in scenes) else "legacy_or_silent",
        "passed": not warnings,
        "warnings": warnings,
        "scenes": rows,
    }


def _ready_for_posting(final_video: Path, report, sync_report: SyncReport) -> bool:
    return bool(
        final_video.exists()
        and report.passed
        and sync_report.passed
        and not report.critical_warnings
        and report.has_audio
        and report.resolution == "1080x1920"
        and report.has_captions
        and report.has_disclaimer
    )


def _write_render_manifest(
    bundle: Path,
    event: dict,
    story,
    plan: ProductionPlan,
    sync_report: SyncReport,
    quality_report,
    final_video: Path,
    review_video: Path,
    template_selection: TemplateSelectionResult | None = None,
    scene_asset_manifest: dict | None = None,
    renderer: str = "python",
) -> None:
    path = bundle / "manifest.json"
    manifest = _read_json(path)
    scene_asset_count = len((scene_asset_manifest or {}).get("assets", []))
    animated_clips = _read_json(bundle / "animated_clips.json")
    animated_clip_count = len(animated_clips) if isinstance(animated_clips, list) else 0
    remotion_input = _read_json(bundle / "remotion_input.json")
    remotion_music_track = remotion_input.get("music_track") if isinstance(remotion_input, dict) else None
    remotion_background_segments = (
        remotion_input.get("background_segments") if isinstance(remotion_input, dict) else None
    )
    needs_tts = plan.audio_mode == "estimated" or (
        plan.audio_mode == "scene_audio"
        and any(not scene.measured_audio_duration for scene in plan.scenes)
    )
    manifest.update(
        {
            "event_id": int(event["id"]),
            "ticker": story.ticker,
            "date": story.date,
            "renderer": renderer,
            "template": "news-studio",
            "video_template_id": plan.template_id,
            "video_template_name": plan.template_name,
            "template_story_type": template_selection.story_type if template_selection else None,
            "template_selection_reason": plan.template_selection_reason,
            "template_candidates": plan.template_candidates,
            "audio_mode": plan.audio_mode,
            "narration_audio_path": plan.narration_audio_path,
            "narration_audio_duration": plan.narration_audio_duration,
            "automation_stage": "video_rendered",
            "ready_for_posting": quality_report.ready_for_posting,
            "needs_tts": needs_tts,
            "needs_render": False,
            "content_duration_estimate_sec": plan.total_duration,
            "final_video_path": str(final_video),
            "review_video_path": str(review_video),
            "production_plan_path": str(bundle / "production_plan.json"),
            "sync_report_path": str(bundle / "sync_report.json"),
            "quality_report_path": str(bundle / "quality_report.json"),
            "template_selection_path": str(bundle / "template_selection.json"),
            "scene_assets_path": str(bundle / "scene_assets.json"),
            "scene_asset_count": scene_asset_count,
            "animated_clips_path": str(bundle / "animated_clips.json"),
            "animated_clip_count": animated_clip_count,
            "remotion_input_path": str(bundle / "remotion_input.json")
            if (bundle / "remotion_input.json").exists()
            else None,
            "remotion_render_path": str(bundle / "remotion_render.json")
            if (bundle / "remotion_render.json").exists()
            else None,
            "background_music_path": remotion_music_track.get("source_path")
            if isinstance(remotion_music_track, dict)
            else None,
            "background_music_volume": remotion_music_track.get("volume")
            if isinstance(remotion_music_track, dict)
            else None,
            "background_segment_count": len(remotion_background_segments)
            if isinstance(remotion_background_segments, list)
            else 0,
            "sync_passed": sync_report.passed,
            "quality_passed": quality_report.passed,
            "warning_count": len(quality_report.warnings) + len(sync_report.warnings),
            "fixed_outro_expected": False,
            "fixed_outro_exists": OUTRO_PATH.exists(),
            "fixed_outro_appended": False,
        }
    )
    _write_json(path, manifest)


def _contact_sheet(frames: list[Path], output: Path, plan: ProductionPlan | None = None) -> None:
    if not frames:
        return
    try:
        from PIL import Image, ImageDraw

        thumb_w, thumb_h = 270, 480
        cols = min(4, len(frames))
        rows = (len(frames) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), "#0f172a")
        draw = ImageDraw.Draw(sheet)
        font = _contact_sheet_font(18)
        small_font = _contact_sheet_font(14)
        for index, frame in enumerate(frames):
            image = Image.open(frame).convert("RGB").resize((thumb_w, thumb_h))
            x = (index % cols) * thumb_w
            y = (index // cols) * thumb_h
            sheet.paste(image, (x, y))
            draw.rectangle((x, y, x + thumb_w - 1, y + thumb_h - 1), outline="#334155", width=2)
            timing = plan.scenes[index] if plan and index < len(plan.scenes) else None
            label = f"{index + 1}. {timing.scene_type if timing else 'scene'}"
            duration = f"{timing.final_duration:.1f}s" if timing else ""
            warning = "WARN" if timing and timing.warnings else ""
            draw.rectangle((x, y + thumb_h - 74, x + thumb_w, y + thumb_h), fill="#020617")
            draw.text((x + 10, y + thumb_h - 68), label[:24], font=font, fill="#f8fafc")
            draw.text((x + 10, y + thumb_h - 38), duration, font=small_font, fill="#38bdf8")
            if warning:
                draw.text((x + 168, y + thumb_h - 38), warning, font=small_font, fill="#fbbf24")
        output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(output)
    except Exception as exc:
        LOGGER.warning("Could not write contact sheet %s: %s", output, exc)


def _contact_sheet_font(size: int):
    from PIL import ImageFont

    for name in ("Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _template_selection_payload(
    selection: TemplateSelectionResult,
    template: VideoTemplate,
) -> dict:
    return {
        **selection.model_dump(),
        "template_name": template.name,
        "scene_slots": [slot.model_dump() for slot in template.scene_slots],
    }


def _record_template_usage_compat(
    selection: TemplateSelectionResult,
    context: dict,
    artifact_stem: str,
) -> None:
    try:
        record_template_usage(selection, context, artifact_stem=artifact_stem)
    except Exception as exc:
        LOGGER.warning("Could not record template usage for %s: %s", artifact_stem, exc)


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
