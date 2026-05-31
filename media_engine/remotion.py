from __future__ import annotations

import json
import hashlib
import os
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_engine.chart_animation import _synthetic_price_rows
from media_engine.schemas.production import ProductionPlan
from media_engine.schemas.remotion import (
    RemotionAsset,
    RemotionBackgroundSegment,
    RemotionCaptionBeat,
    RemotionChartData,
    RemotionMusicTrack,
    RemotionPricePoint,
    RemotionRenderInput,
    RemotionSceneInput,
    RemotionTemplateMeta,
    RemotionTextBeat,
    RemotionVideoMeta,
    RemotionVisualBeat,
)
from media_engine.story_schema import Scene, Story
from models.database import query


REMOTION_ROOT = Path("remotion")
REMOTION_COMPOSITION_ID = "FinanceBrief"
INTRO_VIDEO_NAME = "1stFrame.mp4"
BACKGROUND_SEGMENT_MIN_SECONDS = 8.0
BACKGROUND_SEGMENT_MAX_SECONDS = 10.0
INTRO_SEGMENT_SECONDS = 2.8
BACKGROUND_TRANSITION_SECONDS = 0.45
BACKGROUND_MUSIC_VOLUME = 0.4


@dataclass(frozen=True)
class _RemotionMediaAssets:
    intro: RemotionAsset | None
    backgrounds: list[RemotionAsset]
    music: list[RemotionAsset]
    assets: list[RemotionAsset]


def video_renderer_mode() -> str:
    value = os.getenv("VIDEO_RENDERER", "python").strip().lower()
    return "remotion" if value == "remotion" else "python"


def build_remotion_render_input(
    *,
    story: Story,
    scenes: list[Scene],
    plan: ProductionPlan,
    assets: dict[str, str] | None = None,
    source_map: dict[str, dict[str, Any]] | None = None,
    scene_asset_manifest: dict | None = None,
    public_dir: Path | None = None,
) -> RemotionRenderInput:
    public_assets = _public_assets(
        assets or {},
        scene_asset_manifest or {},
        public_dir=public_dir,
    )
    local_media = _copy_remotion_media_assets(public_dir=public_dir)
    public_assets.extend(local_media.assets)
    background_paths = [asset.public_path for asset in local_media.backgrounds]
    public_paths_by_scene = _scene_asset_paths(scene_asset_manifest or {}, public_assets)
    chart = _chart_data(story)
    source_map = source_map or {}

    remotion_scenes = []
    for index, scene in enumerate(scenes):
        timing = plan.scenes[index]
        remotion_scenes.append(
            RemotionSceneInput(
                scene_index=index,
                scene_type=timing.scene_type,
                card_type=timing.card_type,
                slot_id=timing.slot_id,
                visual_style=timing.visual_style,
                motion=timing.motion,
                caption_style=timing.caption_style,
                start_seconds=round(timing.start, 3),
                end_seconds=round(timing.end, 3),
                duration_seconds=round(timing.final_duration, 3),
                narration=timing.narration,
                headline=scene.headline,
                subheadline=scene.subheadline,
                detail_text=_detail_text(scene, story),
                bullets=_informative_bullets(scene, story),
                caption_text=scene.caption_text,
                source_ids=scene.source_ids,
                sources=[source_map.get(source_id, {}) for source_id in scene.source_ids],
                caption_beats=[
                    RemotionCaptionBeat(
                        text=beat.text,
                        start_seconds=round(beat.start, 3),
                        end_seconds=round(beat.end, 3),
                        scene_index=beat.scene_index,
                    )
                    for beat in timing.caption_beats
                ],
                visual_beats=[
                    RemotionVisualBeat(
                        beat_type=beat.beat_type,
                        start_seconds=round(beat.start, 3),
                        end_seconds=round(beat.end, 3),
                        payload=beat.payload,
                    )
                    for beat in timing.visual_beats
                ],
                chart=chart if _uses_chart_data(scene) else None,
                asset_public_paths=public_paths_by_scene.get(index, []),
                background_public_path=_background_for_scene(background_paths, index),
            )
        )

    text_beats = _text_beats(story, scenes, plan)
    background_segments = _background_segments(
        intro=local_media.intro,
        backgrounds=local_media.backgrounds,
        plan=plan,
    )
    music_track = _music_track(local_media.music, plan)

    return RemotionRenderInput(
        video=RemotionVideoMeta(
            width=plan.width,
            height=plan.height,
            fps=plan.fps,
            total_duration_seconds=round(plan.total_duration, 3),
            ticker=story.ticker,
            company=story.company,
            date=story.date,
            disclaimer=story.disclaimer,
            price=story.price_card.price,
            change_pct=story.price_card.change_pct,
            direction=story.price_card.direction,
        ),
        template=RemotionTemplateMeta(
            template_id=plan.template_id,
            template_name=plan.template_name,
            scene_order=[scene.card_type or scene.scene_type for scene in scenes],
        ),
        scenes=remotion_scenes,
        assets=public_assets,
        intro_video=local_media.intro,
        background_segments=background_segments,
        text_beats=text_beats,
        music_track=music_track,
        public_dir=str(public_dir) if public_dir else None,
    )


def write_remotion_input(path: Path, payload: RemotionRenderInput) -> None:
    path.write_text(
        json.dumps(payload.model_dump(), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def render_remotion_video(
    *,
    input_path: Path,
    output_path: Path,
    public_dir: Path,
    remotion_root: Path = REMOTION_ROOT,
) -> dict[str, Any]:
    if not remotion_root.exists():
        raise FileNotFoundError(f"Remotion project is missing: {remotion_root}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "npm",
        "run",
        "render",
        "--",
        "--input",
        str(input_path.resolve()),
        "--output",
        str(output_path.resolve()),
        "--public-dir",
        str(public_dir.resolve()),
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=remotion_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = {
            "returncode": exc.returncode,
            "stdout": (exc.stdout or "")[-4000:],
            "stderr": (exc.stderr or "")[-4000:],
        }
        raise RuntimeError(f"Remotion render command failed: {json.dumps(details)}") from exc
    return {
        "renderer": "remotion",
        "composition_id": REMOTION_COMPOSITION_ID,
        "command": cmd,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "public_dir": str(public_dir),
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def _public_assets(
    assets: dict[str, str],
    scene_asset_manifest: dict,
    *,
    public_dir: Path | None,
) -> list[RemotionAsset]:
    if not public_dir:
        return []
    public_dir.mkdir(parents=True, exist_ok=True)
    rows = [(asset_type, path) for asset_type, path in assets.items()]
    rows.extend(
        (
            str(item.get("template") or item.get("asset_type") or "scene_asset"),
            str(item.get("path") or ""),
        )
        for item in scene_asset_manifest.get("assets", [])
        if isinstance(item, dict)
    )

    copied: list[RemotionAsset] = []
    seen: set[Path] = set()
    for asset_type, value in rows:
        source = Path(value)
        if not value or source in seen or not source.exists() or not source.is_file():
            continue
        seen.add(source)
        target_name = f"{_slug(asset_type)}-{_slug(source.stem)}{source.suffix.lower()}"
        target = public_dir / "assets" / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        copied.append(
            RemotionAsset(
                asset_type=asset_type,
                source_path=str(source),
                public_path=f"/assets/{target.name}",
            )
        )
    return copied


def _copy_remotion_media_assets(*, public_dir: Path | None) -> _RemotionMediaAssets:
    if not public_dir:
        return _RemotionMediaAssets(intro=None, backgrounds=[], music=[], assets=[])

    intro_path = _local_intro_video_path()
    intro_asset = (
        _copy_media_asset(intro_path, public_dir, asset_type="intro_video", folder="backgrounds")
        if intro_path
        else None
    )
    background_assets = [
        _copy_media_asset(source, public_dir, asset_type="background_video", folder="backgrounds")
        for source in _local_background_paths()
    ]
    music_assets = [
        _copy_media_asset(source, public_dir, asset_type="background_music", folder="music")
        for source in _local_music_paths()
    ]
    assets = [asset for asset in [intro_asset, *background_assets, *music_assets] if asset]
    return _RemotionMediaAssets(
        intro=intro_asset,
        backgrounds=background_assets,
        music=music_assets,
        assets=assets,
    )


def _copy_media_asset(
    source: Path,
    public_dir: Path,
    *,
    asset_type: str,
    folder: str,
) -> RemotionAsset:
    target = public_dir / folder / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return RemotionAsset(
        asset_type=asset_type,
        source_path=str(source),
        public_path=f"/{folder}/{target.name}",
    )


def _local_intro_video_path() -> Path | None:
    for source in _iter_local_media_paths({".mp4", ".mov", ".webm"}):
        if source.name.lower() == INTRO_VIDEO_NAME.lower():
            return source
    return None


def _local_background_paths() -> list[Path]:
    paths = [
        path
        for path in _iter_local_media_paths({".mp4", ".mov", ".webm"})
        if path.name.lower() != INTRO_VIDEO_NAME.lower()
    ]
    user_supplied = [path for path in paths if not _is_generated_background(path)]
    return user_supplied or paths


def _is_generated_background(path: Path) -> bool:
    return path.name.lower() in {
        "enterprise_motion.mp4",
        "market_flow.mp4",
        "software_flow.mp4",
    }


def _local_music_paths() -> list[Path]:
    return [
        path
        for path in _iter_local_media_paths({".mp3", ".m4a", ".wav"})
        if path.name.lower().startswith("backgroundmusic")
    ]


def _iter_local_media_paths(suffixes: set[str]) -> list[Path]:
    directories = [
        Path("assets/backgrounds"),
        Path("storage/backgrounds"),
        Path("storage/remotion_backgrounds"),
        REMOTION_ROOT / "backgrounds",
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: _natural_sort_key(item.name)):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return paths


def _natural_sort_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _background_for_scene(background_paths: list[str], scene_index: int) -> str | None:
    if not background_paths:
        return None
    return background_paths[scene_index % len(background_paths)]


def _background_segments(
    *,
    intro: RemotionAsset | None,
    backgrounds: list[RemotionAsset],
    plan: ProductionPlan,
) -> list[RemotionBackgroundSegment]:
    total_duration = max(0.0, float(plan.total_duration or 0.0))
    if total_duration <= 0:
        return []

    segments: list[RemotionBackgroundSegment] = []
    cursor = 0.0
    if intro:
        first_scene_end = plan.scenes[0].end if plan.scenes else total_duration
        intro_duration = min(INTRO_SEGMENT_SECONDS, total_duration, max(0.0, first_scene_end))
        if intro_duration > 0.05:
            segments.append(
                RemotionBackgroundSegment(
                    public_path=intro.public_path,
                    source_path=intro.source_path,
                    start_seconds=0.0,
                    duration_seconds=round(intro_duration, 3),
                    transition="intro",
                    transition_duration_seconds=BACKGROUND_TRANSITION_SECONDS,
                    segment_type="intro",
                )
            )
            cursor = intro_duration

    if not backgrounds:
        return segments

    rng = random.Random(_stable_seed(plan.video_id))
    shuffled = list(backgrounds)
    rng.shuffle(shuffled)
    pool_index = 0
    while cursor < total_duration - 0.05:
        if pool_index >= len(shuffled):
            rng.shuffle(shuffled)
            pool_index = 0
        asset = shuffled[pool_index]
        pool_index += 1
        remaining = total_duration - cursor
        target_duration = BACKGROUND_SEGMENT_MIN_SECONDS + rng.random() * (
            BACKGROUND_SEGMENT_MAX_SECONDS - BACKGROUND_SEGMENT_MIN_SECONDS
        )
        duration = min(target_duration, remaining)
        segments.append(
            RemotionBackgroundSegment(
                public_path=asset.public_path,
                source_path=asset.source_path,
                start_seconds=round(cursor, 3),
                duration_seconds=round(duration, 3),
                transition="crossfade",
                transition_duration_seconds=BACKGROUND_TRANSITION_SECONDS,
                segment_type="background",
            )
        )
        cursor += duration
    return segments


def _music_track(
    music_assets: list[RemotionAsset],
    plan: ProductionPlan,
) -> RemotionMusicTrack | None:
    if not music_assets or plan.total_duration <= 0:
        return None
    rng = random.Random(_stable_seed(f"{plan.video_id}:music"))
    asset = rng.choice(music_assets)
    return RemotionMusicTrack(
        source_path=asset.source_path,
        public_path=asset.public_path,
        volume=BACKGROUND_MUSIC_VOLUME,
        loop=True,
        start_seconds=0.0,
        duration_seconds=round(plan.total_duration, 3),
    )


def _text_beats(story: Story, scenes: list[Scene], plan: ProductionPlan) -> list[RemotionTextBeat]:
    beats: list[RemotionTextBeat] = []
    for index, scene in enumerate(scenes):
        if index >= len(plan.scenes):
            continue
        timing = plan.scenes[index]
        candidates = _beat_text_candidates(scene, story)
        if timing.caption_beats:
            for beat_index, beat in enumerate(timing.caption_beats):
                beats.append(
                    RemotionTextBeat(
                        text=_beat_text(scene, story, beat.text, candidates, beat_index),
                        start_seconds=round(beat.start, 3),
                        end_seconds=round(beat.end, 3),
                        scene_index=beat.scene_index,
                        beat_type=_beat_type(scene, beat_index),
                    )
                )
            continue
        beats.append(
            RemotionTextBeat(
                text=_beat_text(scene, story, scene.caption_text or scene.headline, candidates, 0),
                start_seconds=round(timing.start, 3),
                end_seconds=round(timing.end, 3),
                scene_index=index,
                beat_type=_beat_type(scene, 0),
            )
        )
    return _merge_short_text_beats(beats)


def _beat_text(
    scene: Scene,
    story: Story,
    caption_text: str,
    candidates: list[str],
    beat_index: int,
) -> str:
    cleaned = _clean_overlay_text(caption_text)
    candidate = candidates[min(beat_index, len(candidates) - 1)] if candidates else ""
    if _is_useful_overlay_text(cleaned):
        if not _is_fragment_overlay_text(cleaned):
            return cleaned
        repaired = _repair_fragment_overlay_text(cleaned)
        if _is_useful_overlay_text(repaired):
            return repaired
    if candidate:
        return candidate
    return _detail_text(scene, story)


def _beat_text_candidates(scene: Scene, story: Story) -> list[str]:
    candidates = [scene.headline, _detail_text(scene, story), *_informative_bullets(scene, story)]
    if scene.card_type == "chart_card":
        candidates = [
            f"{story.ticker} closed {story.price_card.change_pct} {story.price_card.period}",
            story.chart_insight,
            scene.subheadline,
            *_informative_bullets(scene, story),
        ]
    return _unique_text(candidates)


def _beat_type(scene: Scene, beat_index: int) -> str:
    if beat_index == 0:
        return "headline"
    if scene.card_type == "chart_card":
        return "chart_annotation"
    return "detail"


def _merge_short_text_beats(beats: list[RemotionTextBeat]) -> list[RemotionTextBeat]:
    merged: list[RemotionTextBeat] = []
    for beat in beats:
        duration = beat.end_seconds - beat.start_seconds
        if (
            merged
            and beat.text == merged[-1].text
            and beat.scene_index == merged[-1].scene_index
            and duration < 0.45
        ):
            merged[-1].end_seconds = beat.end_seconds
            continue
        merged.append(beat)
    return merged


def _stable_seed(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _scene_asset_paths(
    scene_asset_manifest: dict,
    copied_assets: list[RemotionAsset],
) -> dict[int, list[str]]:
    public_by_source = {Path(asset.source_path): asset.public_path for asset in copied_assets}
    output: dict[int, list[str]] = {}
    for item in scene_asset_manifest.get("assets", []):
        if not isinstance(item, dict):
            continue
        try:
            scene_index = int(item.get("scene_index", 0))
        except (TypeError, ValueError):
            continue
        path = Path(str(item.get("path") or ""))
        public_path = public_by_source.get(path)
        if public_path:
            output.setdefault(scene_index, []).append(public_path)
    return output


def _chart_data(story: Story) -> RemotionChartData:
    rows = _price_rows(story.ticker)
    synthetic = len(rows) < 2
    if synthetic:
        rows = _synthetic_price_rows(story, rows)
    points = [
        RemotionPricePoint(
            date=str(row.get("date") or ""),
            close=_float(row.get("close"), 0.0),
            volume=int(_float(row.get("volume"), 0.0)),
            change_percent=_float(row.get("change_percent"), 0.0),
        )
        for row in rows
        if _float(row.get("close"), 0.0) > 0
    ]
    return RemotionChartData(
        title="Price context" if synthetic else "90-day price trend",
        source="latest market snapshot" if synthetic else f"market data through {story.date}",
        synthetic=synthetic,
        points=points,
    )


def _detail_text(scene: Scene, story: Story) -> str:
    sentences = _sentences(scene.narration)
    for sentence in sentences:
        cleaned = _clean_overlay_text(sentence)
        if _is_useful_overlay_text(cleaned):
            return cleaned
    for value in (scene.subheadline, story.takeaway, story.chart_insight):
        cleaned = _clean_overlay_text(value)
        if _is_useful_overlay_text(cleaned):
            return cleaned
    return _clean_overlay_text(scene.caption_text or scene.headline)


def _informative_bullets(scene: Scene, story: Story) -> list[str]:
    existing = [_clean_overlay_text(item) for item in scene.bullets if _is_useful_overlay_text(item)]
    if existing and not _only_generic_bullets(existing):
        return existing[:3]
    if scene.card_type == "hook_card":
        return _unique_text(
            [
                _clean_overlay_text(scene.narration),
                *_story_section_bullets(story, "catalyst"),
                story.takeaway,
            ]
        )[:3]
    if scene.card_type == "chart_card":
        return _unique_text(
            [
                f"{story.ticker} closed {story.price_card.change_pct} {story.price_card.period}",
                scene.subheadline,
                story.chart_insight,
            ]
        )[:3]
    if scene.card_type == "bull_bear_card":
        candidates = [
            *_sentences(scene.narration),
            *_story_section_bullets(story, "catalyst"),
            *_story_section_bullets(story, "context"),
            *_story_section_bullets(story, "risk"),
        ]
        return _unique_text(candidates)[:3]
    if scene.card_type == "takeaway_card":
        return _unique_text([*_sentences(scene.narration), story.takeaway])[:3]
    candidates = [
        *_sentences(scene.narration),
        scene.subheadline,
        *_story_section_bullets(story, "catalyst"),
        *_story_section_bullets(story, "watch"),
    ]
    return _unique_text(candidates)[:3]


def _story_section_bullets(story: Story, section_type: str) -> list[str]:
    return [
        bullet
        for section in story.sections
        if section.type == section_type
        for bullet in section.bullets
    ]


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return [_clean_overlay_text(chunk) for chunk in chunks if _clean_overlay_text(chunk)]


def _clean_overlay_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = cleaned.removeprefix("The takeaway:").strip()
    cleaned = cleaned.rstrip(".")
    words = cleaned.split()
    if len(words) > 18:
        cleaned = " ".join(words[:18]).rstrip(",:;") + "..."
    return cleaned


def _is_useful_overlay_text(text: str) -> bool:
    cleaned = str(text or "").strip()
    if len(cleaned.split()) < 2:
        return False
    if _is_filler_overlay_text(cleaned):
        return False
    return cleaned.lower() not in {
        "takeaway",
        "price action",
        "chart check",
        "risk check",
        "market brief",
    }


def _is_filler_overlay_text(text: str) -> bool:
    lower = str(text or "").lower().strip()
    return lower.startswith(
        (
            "several factors fueled",
            "the takeaway",
            "educational only",
        )
    )


def _is_fragment_overlay_text(text: str) -> bool:
    raw = str(text or "").strip()
    lower = raw.lower().strip(" ,;:")
    words = lower.split()
    if not words:
        return True
    if raw[0].islower() and len(words) > 3:
        return True
    if words[-1] in {
        "a",
        "an",
        "and",
        "as",
        "by",
        "for",
        "from",
        "how",
        "including",
        "into",
        "marking",
        "of",
        "or",
        "the",
        "to",
        "with",
    }:
        return True
    if " including " in f" {lower} ":
        tail = lower.split(" including ", 1)[1].split()
        return len(tail) <= 3
    return False


def _repair_fragment_overlay_text(text: str) -> str:
    cleaned = str(text or "").strip(" ,;:")
    if not cleaned:
        return ""
    cleaned = re.sub(
        r",?\s+(including|marking|with|and|or|to|from|for|by|as|of|the)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" ,;:")
    if " including " in f" {cleaned.lower()} ":
        head, tail = re.split(r"\s+including\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(tail.split()) <= 3:
            cleaned = head.strip(" ,;:")
    if cleaned and cleaned[0].islower():
        cleaned = f"{cleaned[0].upper()}{cleaned[1:]}"
    return cleaned


def _only_generic_bullets(items: list[str]) -> bool:
    return all(not _is_useful_overlay_text(item) for item in items)


def _unique_text(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _clean_overlay_text(item)
        key = cleaned.lower()
        if not _is_useful_overlay_text(cleaned) or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _price_rows(ticker: str) -> list[dict[str, Any]]:
    try:
        rows = query(
            """
            SELECT date, close, volume, change_percent
            FROM daily_prices
            WHERE ticker = ? AND close IS NOT NULL
            ORDER BY date DESC
            LIMIT 90
            """,
            (ticker,),
        )
    except Exception:
        return []
    return list(reversed(rows))


def _is_chart_scene(scene: Scene) -> bool:
    return scene.card_type == "chart_card" or scene.scene_type in {"chart", "timeline"}


def _uses_chart_data(scene: Scene) -> bool:
    return bool(
        _is_chart_scene(scene)
        or scene.chart_path
        or scene.card_type in {"hook_card", "price_move_card"}
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", value.lower()).strip("-")
    return slug or "asset"


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
