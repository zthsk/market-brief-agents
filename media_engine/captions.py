from __future__ import annotations

import re
from typing import Any

from media_engine.schemas.production import CaptionBeat
from media_engine.story_schema import Caption, Scene


def captions_for_scenes(scenes: list[Scene]) -> list[Caption]:
    captions: list[Caption] = []
    start = 0.0
    for scene in scenes:
        parts = _caption_parts(scene)
        slot = scene.duration / max(1, len(parts))
        for text in parts:
            end = start + slot
            captions.append(Caption(index=len(captions) + 1, start=start, end=end, text=text))
            start = end
    return captions


def captions_to_srt(captions: list[Caption]) -> str:
    blocks = []
    for caption in captions:
        blocks.append(
            f"{caption.index}\n{_stamp(caption.start)} --> {_stamp(caption.end)}\n{caption.text}\n"
        )
    return "\n".join(blocks)


def caption_beats_to_captions(beats: list[CaptionBeat]) -> list[Caption]:
    return [
        Caption(index=index, start=beat.start, end=beat.end, text=beat.text)
        for index, beat in enumerate(beats, start=1)
    ]


def caption_beats_to_json(beats: list[CaptionBeat]) -> list[dict[str, Any]]:
    return [beat.model_dump() for beat in beats]


def _caption_parts(scene: Scene) -> list[str]:
    if scene.narration:
        parts = [
            part.strip(" ,;:")
            for part in re.split(r"(?<=[.!?])\s+|;\s+|\s+-\s+", scene.narration)
            if part.strip(" ,;:")
        ]
        if parts:
            return parts[:4]
    if scene.bullets:
        return [scene.headline, *scene.bullets]
    return [scene.caption_text or scene.headline]


def _stamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
