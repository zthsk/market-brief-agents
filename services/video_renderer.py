from __future__ import annotations

from media_engine.renderer import render_video_bundle
from models.database import upsert_video


def render_video(
    script_row: dict,
    assets: dict[str, str] | None = None,
    *,
    template: str = "news-studio",
    max_duration: int = 75,
    captions: bool = True,
    renderer: str | None = None,
) -> str | None:
    result = render_video_bundle(
        script_row,
        assets,
        template=template,
        max_duration=max_duration,
        captions=captions,
        renderer=renderer,
    )
    if not result:
        return None
    status = "ready_to_upload" if result.get("ready_for_posting") else "queued_for_review"
    upsert_video(int(script_row["id"]), result["video_path"], status=status)
    return str(result["video_path"])


def _frame_duration(frame_count: int, audio_duration: float | None) -> float:
    if not audio_duration or frame_count <= 0:
        return 12
    return max(4, min(12, audio_duration / frame_count))


def _frame_durations(frame_count: int, audio_duration: float | None) -> list[float]:
    if frame_count <= 0:
        return []
    if not audio_duration:
        return [12] * frame_count
    weights = [0.18, 0.36, 0.25, 0.21]
    if frame_count != len(weights):
        return [_frame_duration(frame_count, audio_duration)] * frame_count
    usable_duration = max(audio_duration + 1.5, 22)
    durations = [max(4, usable_duration * weight) for weight in weights]
    total = sum(durations)
    if total > usable_duration:
        scale = usable_duration / total
        durations = [duration * scale for duration in durations]
    return durations
