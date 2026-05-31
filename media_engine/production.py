from __future__ import annotations

import re
import textwrap
from pathlib import Path

from media_engine.schemas.production import CaptionBeat, ProductionPlan, SceneTiming, SyncReport, VisualBeat
from media_engine.story_schema import Caption, Scene


MAX_ALLOWED_DRIFT_SECONDS = 0.35
MAX_CAPTION_CHARS = 52
MIN_CAPTION_DURATION = 0.85
MAX_CAPTION_DURATION = 3.25

SCENE_PADDING = {
    "hook": {"before": 0.0, "after": 0.35},
    "price_action": {"before": 0.0, "after": 0.25},
    "price_card": {"before": 0.0, "after": 0.25},
    "news": {"before": 0.0, "after": 0.25},
    "context": {"before": 0.0, "after": 0.25},
    "bullet_reveal": {"before": 0.0, "after": 0.25},
    "chart": {"before": 0.0, "after": 0.30},
    "risk": {"before": 0.0, "after": 0.35},
    "takeaway": {"before": 0.0, "after": 0.50},
    "conclusion": {"before": 0.0, "after": 0.50},
    "outro": {"before": 0.0, "after": 0.40},
    "default": {"before": 0.0, "after": 0.25},
}

MOTION_PRESETS = {
    "hook": "punch_in",
    "price_action": "stat_reveal",
    "price_card": "stat_reveal",
    "news": "bullet_stagger",
    "context": "bullet_stagger",
    "bullet_reveal": "bullet_stagger",
    "chart": "chart_pan",
    "risk": "risk_contrast",
    "takeaway": "takeaway_hold",
    "conclusion": "takeaway_hold",
    "outro": "takeaway_hold",
    "earnings": "stat_reveal",
    "financials": "stat_reveal",
    "analyst": "slide_up",
    "comparison": "split_reveal",
}

VISUAL_REQUIREMENT_MAP = {
    "price_chart": "chart",
    "stock_chart": "chart",
    "price_move": "chart",
    "volume_chart": "chart",
    "volume_spike": "chart",
    "earnings": "summary",
    "earnings_summary": "summary",
    "guidance": "summary",
    "financial_metric": "summary",
    "risk_warning": "summary",
    "warning_indicator": "summary",
    "company_logo": "company",
    "product_image": "company",
    "news_headline": "headline",
}


def build_production_plan(
    *,
    video_id: str,
    output_video_path: str | Path,
    scenes: list[Scene],
    assets: dict[str, str] | None = None,
    max_duration: int = 75,
    tts_configured: bool = False,
    audio_generation_attempted: bool = False,
    scene_audio_warnings: dict[int, list[str]] | None = None,
    template_id: str | None = None,
    template_name: str | None = None,
    template_selection_reason: str | None = None,
    template_candidates: list[str] | None = None,
    narration_audio_path: str | Path | None = None,
    narration_audio_duration: float | None = None,
) -> ProductionPlan:
    assets = assets or {}
    timings: list[SceneTiming] = []
    warnings: list[str] = []
    elapsed = 0.0
    has_scene_audio = any(scene.audio_path and scene.audio_duration for scene in scenes)
    continuous_audio_path = str(narration_audio_path) if narration_audio_path and not has_scene_audio else None
    continuous_audio_duration = (
        float(narration_audio_duration)
        if continuous_audio_path and narration_audio_duration
        else None
    )
    continuous_allocations = (
        _continuous_audio_allocations(scenes, continuous_audio_duration)
        if continuous_audio_duration
        else []
    )
    for index, scene in enumerate(scenes):
        padding = scene_padding(scene.scene_type)
        scene_warnings: list[str] = list((scene_audio_warnings or {}).get(index, []))
        scene_warnings.extend(scene.template_warnings)
        measured = scene.audio_duration
        estimated = None
        timing_source = "estimated"
        if measured:
            audio_duration = measured
            timing_source = "scene_audio"
        elif continuous_allocations:
            estimated = continuous_allocations[index]
            audio_duration = estimated
            timing_source = "continuous_audio"
        else:
            estimated = _estimated_audio_duration(scene.narration)
            audio_duration = estimated
            if tts_configured and audio_generation_attempted:
                scene_warnings.append("TTS audio could not be measured; using estimated timing.")
            else:
                scene_warnings.append("TTS unavailable; using estimated timing.")
        if not scene.visual_requirements:
            scene_warnings.append("Scene has no visual requirements; using template fallback.")
        scene_asset_warnings = _apply_visual_asset_mapping(scene, assets)
        scene_warnings.extend(scene_asset_warnings)
        final_duration = max(2.2, audio_duration + padding["before"] + padding["after"])
        if final_duration > max_duration:
            scene_warnings.append("Scene duration exceeds max video duration target.")
        start = elapsed
        end = start + final_duration
        caption_beats = caption_beats_for_scene(
            scene,
            scene_index=index,
            scene_start=start + padding["before"],
            narration_duration=audio_duration,
            scene_end=end,
            warnings=scene_warnings,
        )
        visual_beats = visual_beats_for_scene(scene, index, start, end, assets)
        timing = SceneTiming(
            scene_index=index,
            scene_type=scene.scene_type,
            slot_id=scene.slot_id,
            card_type=scene.card_type,
            visual_style=scene.visual_style,
            motion=scene.motion or motion_preset(scene.scene_type),
            caption_style=scene.caption_style,
            narration=scene.narration,
            start=round(start, 3),
            end=round(end, 3),
            audio_path=scene.audio_path,
            measured_audio_duration=round(measured, 3) if measured else None,
            estimated_audio_duration=round(estimated, 3) if estimated else None,
            padding_before=padding["before"],
            padding_after=padding["after"],
            final_duration=round(final_duration, 3),
            timing_source=timing_source,
            caption_beats=caption_beats,
            visual_beats=visual_beats,
            warnings=scene_warnings,
        )
        timings.append(timing)
        warnings.extend(f"scene {index}: {warning}" for warning in scene_warnings)
        elapsed = end
    return ProductionPlan(
        video_id=video_id,
        output_video_path=str(output_video_path),
        template_id=template_id,
        template_name=template_name,
        template_selection_reason=template_selection_reason,
        template_candidates=template_candidates or [],
        audio_mode=(
            "continuous_audio"
            if continuous_allocations
            else "scene_audio"
            if has_scene_audio
            else "estimated"
        ),
        narration_audio_path=continuous_audio_path,
        narration_audio_duration=round(continuous_audio_duration, 3)
        if continuous_audio_duration
        else None,
        scenes=timings,
        total_duration=round(elapsed, 3),
        warnings=warnings,
    )


def apply_production_plan_to_scenes(scenes: list[Scene], plan: ProductionPlan) -> None:
    total = plan.total_duration or 1
    for scene, timing in zip(scenes, plan.scenes, strict=False):
        scene.duration = timing.final_duration
        scene.audio_path = timing.audio_path
        scene.audio_duration = timing.measured_audio_duration
        scene.progress_start = timing.start / total
        scene.progress_end = timing.end / total


def build_sync_report(
    *,
    video_id: str,
    plan: ProductionPlan,
    content_video_duration: float | None = None,
) -> SyncReport:
    if plan.audio_mode == "continuous_audio" and plan.narration_audio_duration:
        total_audio = plan.narration_audio_duration
    else:
        total_audio = sum(
            scene.measured_audio_duration or scene.estimated_audio_duration or 0.0
            for scene in plan.scenes
        )
    total_video = content_video_duration if content_video_duration is not None else plan.total_duration
    drift = round(abs(total_video - plan.total_duration), 3)
    warnings = []
    if drift > MAX_ALLOWED_DRIFT_SECONDS:
        warnings.append(
            f"Content video duration drift {drift:.2f}s exceeds {MAX_ALLOWED_DRIFT_SECONDS:.2f}s."
        )
    if total_audio - total_video > MAX_ALLOWED_DRIFT_SECONDS:
        warnings.append(
            "Content video is shorter than narration audio; the audio track may be truncated."
        )
    for scene in plan.scenes:
        if plan.audio_mode == "scene_audio" and (
            not scene.audio_path or not scene.measured_audio_duration
        ):
            warnings.append(f"scene {scene.scene_index} is using estimated timing.")
        elif plan.audio_mode == "estimated" and scene.timing_source == "estimated":
            warnings.append(f"scene {scene.scene_index} is using estimated timing.")
        for caption in scene.caption_beats:
            if caption.start < scene.start or caption.end > scene.end:
                warnings.append(f"scene {scene.scene_index} has caption outside scene boundary.")
                break
    return SyncReport(
        video_id=video_id,
        total_audio_duration=round(total_audio, 3),
        total_video_duration=round(total_video, 3),
        drift_seconds=drift,
        passed=not warnings,
        warnings=warnings,
    )


def caption_beats_for_scene(
    scene: Scene,
    *,
    scene_index: int,
    scene_start: float,
    narration_duration: float,
    scene_end: float,
    warnings: list[str] | None = None,
) -> list[CaptionBeat]:
    warnings = warnings if warnings is not None else []
    chunks = _caption_chunks(scene.narration or scene.caption_text or scene.headline)
    if not chunks:
        chunks = [scene.caption_text or scene.headline or "Caption unavailable"]
        warnings.append("Scene narration is empty; using fallback caption text.")
    usable_duration = max(0.1, min(narration_duration, scene_end - scene_start))
    chunks = _merge_caption_chunks(chunks, usable_duration)
    weights = [max(1, len(chunk.split())) for chunk in chunks]
    total_weight = sum(weights) or 1
    cursor = scene_start
    beats: list[CaptionBeat] = []
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            end = scene_start + usable_duration
        else:
            raw_duration = usable_duration * (weights[index] / total_weight)
            duration = min(MAX_CAPTION_DURATION, max(MIN_CAPTION_DURATION, raw_duration))
            remaining_chunks = len(chunks) - index - 1
            max_end = scene_start + usable_duration - remaining_chunks * MIN_CAPTION_DURATION
            end = min(cursor + duration, max_end)
        end = min(scene_end, max(cursor + 0.1, end))
        beats.append(
            CaptionBeat(
                text=chunk,
                start=round(cursor, 3),
                end=round(end, 3),
                scene_index=scene_index,
            )
        )
        cursor = end
    if beats and beats[-1].end < scene_start + usable_duration:
        beats[-1].end = round(min(scene_end, scene_start + usable_duration), 3)
    return beats


def visual_beats_for_scene(
    scene: Scene,
    scene_index: int,
    scene_start: float,
    scene_end: float,
    assets: dict[str, str] | None = None,
) -> list[VisualBeat]:
    labels = ["headline"]
    if scene.bullets:
        labels.extend(f"highlight:{item}" for item in scene.bullets[:3])
    if scene.subheadline:
        labels.append("subheadline")
    duration = max(0.1, scene_end - scene_start)
    slot = duration / max(1, len(labels))
    motion = scene.motion or motion_preset(scene.scene_type)
    return [
        VisualBeat(
            beat_type=label,
            start=round(scene_start + slot * index, 3),
            end=round(scene_start + slot * (index + 1), 3),
            payload={
                "scene_index": scene_index,
                "motion": motion,
                "slot_id": scene.slot_id,
                "card_type": scene.card_type,
                "visual_style": scene.visual_style,
                "caption_style": scene.caption_style,
                "visual_requirements": scene.visual_requirements,
                "asset_keys": _mapped_asset_keys(scene.visual_requirements),
                "assets": _resolved_assets(scene.visual_requirements, assets or {}),
            },
        )
        for index, label in enumerate(labels)
    ]


def captions_from_plan(plan: ProductionPlan) -> list[Caption]:
    captions: list[Caption] = []
    for scene in plan.scenes:
        for beat in scene.caption_beats:
            captions.append(
                Caption(index=len(captions) + 1, start=beat.start, end=beat.end, text=beat.text)
            )
    return captions


def scene_padding(scene_type: str) -> dict[str, float]:
    return SCENE_PADDING.get(scene_type, SCENE_PADDING["default"])


def estimated_audio_duration(narration: str) -> float:
    return _estimated_audio_duration(narration)


def motion_preset(scene_type: str) -> str:
    return MOTION_PRESETS.get(scene_type, "takeaway_hold")


def _apply_visual_asset_mapping(scene: Scene, assets: dict[str, str]) -> list[str]:
    warnings = []
    if not scene.visual_requirements:
        return warnings
    mapped_assets = _resolved_assets(scene.visual_requirements, assets)
    for requirement in scene.visual_requirements:
        mapped_key = VISUAL_REQUIREMENT_MAP.get(requirement)
        if mapped_key and mapped_key not in mapped_assets:
            warnings.append(f"Optional visual asset missing for {requirement}; using fallback card.")
    if not scene.chart_path and any(
        VISUAL_REQUIREMENT_MAP.get(requirement) == "chart"
        for requirement in scene.visual_requirements
    ):
        chart_path = assets.get("chart")
        if chart_path and Path(chart_path).exists():
            scene.chart_path = chart_path
    return warnings


def _mapped_asset_keys(requirements: list[str]) -> list[str]:
    return sorted({VISUAL_REQUIREMENT_MAP.get(requirement, requirement) for requirement in requirements})


def _resolved_assets(requirements: list[str], assets: dict[str, str]) -> dict[str, str]:
    resolved = {}
    for key in _mapped_asset_keys(requirements):
        path = assets.get(key)
        if path and Path(path).exists():
            resolved[key] = path
    return resolved


def _caption_chunks(text: str) -> list[str]:
    parts = [
        part.strip(" ,;:")
        for part in re.split(r"(?<=[.!?])\s+|;\s+|\s+-\s+", str(text or ""))
        if part.strip(" ,;:")
    ]
    chunks: list[str] = []
    for part in parts:
        wrapped = textwrap.wrap(part, width=MAX_CAPTION_CHARS) or [part]
        chunks.extend(item.strip() for item in wrapped if item.strip())
    return chunks


def _merge_caption_chunks(chunks: list[str], usable_duration: float) -> list[str]:
    max_count = max(1, int(usable_duration / MIN_CAPTION_DURATION))
    merged = list(chunks)
    while len(merged) > max_count:
        best_index = 0
        best_score = 10_000
        for index in range(len(merged) - 1):
            score = len(merged[index]) + len(merged[index + 1])
            if score < best_score:
                best_score = score
                best_index = index
        merged[best_index : best_index + 2] = [
            f"{merged[best_index]} {merged[best_index + 1]}".strip()
        ]
    return merged


def _estimated_audio_duration(narration: str) -> float:
    words = len(str(narration or "").split())
    if words == 0:
        return 2.0
    return max(1.8, words / 155 * 60)


def _continuous_audio_allocations(scenes: list[Scene], total_duration: float | None) -> list[float]:
    if not scenes or not total_duration:
        return []
    weights = [_estimated_audio_duration(scene.narration) for scene in scenes]
    total_weight = sum(weights) or 1.0
    allocations = [max(0.1, total_duration * weight / total_weight) for weight in weights]
    drift = sum(allocations) - total_duration
    allocations[-1] = max(0.1, allocations[-1] - drift)
    return allocations
