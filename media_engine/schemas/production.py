from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CaptionBeat(BaseModel):
    text: str
    start: float
    end: float
    scene_index: int


class VisualBeat(BaseModel):
    beat_type: str
    start: float
    end: float
    payload: dict[str, Any] = Field(default_factory=dict)


class SceneTiming(BaseModel):
    scene_index: int
    scene_type: str
    slot_id: str | None = None
    card_type: str | None = None
    visual_style: str | None = None
    motion: str | None = None
    caption_style: str | None = None
    narration: str
    start: float = 0.0
    end: float = 0.0
    audio_path: str | None = None
    measured_audio_duration: float | None = None
    estimated_audio_duration: float | None = None
    padding_before: float = 0.0
    padding_after: float = 0.25
    final_duration: float
    timing_source: str = "estimated"
    caption_beats: list[CaptionBeat] = Field(default_factory=list)
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductionPlan(BaseModel):
    video_id: str
    output_video_path: str
    template_id: str | None = None
    template_name: str | None = None
    template_selection_reason: str | None = None
    template_candidates: list[str] = Field(default_factory=list)
    audio_mode: str = "estimated"
    narration_audio_path: str | None = None
    narration_audio_duration: float | None = None
    width: int = 1080
    height: int = 1920
    fps: int = 30
    scenes: list[SceneTiming]
    total_duration: float
    warnings: list[str] = Field(default_factory=list)


class SyncReport(BaseModel):
    video_id: str
    total_audio_duration: float
    total_video_duration: float
    drift_seconds: float
    passed: bool
    warnings: list[str] = Field(default_factory=list)
