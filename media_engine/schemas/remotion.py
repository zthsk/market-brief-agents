from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RemotionVideoMeta(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    total_duration_seconds: float
    ticker: str
    company: str
    date: str
    disclaimer: str
    price: str
    change_pct: str
    direction: str


class RemotionTemplateMeta(BaseModel):
    template_id: str | None = None
    template_name: str | None = None
    scene_order: list[str] = Field(default_factory=list)


class RemotionAsset(BaseModel):
    asset_type: str
    source_path: str
    public_path: str


class RemotionCaptionBeat(BaseModel):
    text: str
    start_seconds: float
    end_seconds: float
    scene_index: int


class RemotionVisualBeat(BaseModel):
    beat_type: str
    start_seconds: float
    end_seconds: float
    payload: dict[str, Any] = Field(default_factory=dict)


class RemotionTextBeat(BaseModel):
    text: str
    start_seconds: float
    end_seconds: float
    scene_index: int
    beat_type: str = "caption"


class RemotionBackgroundSegment(BaseModel):
    public_path: str
    source_path: str
    start_seconds: float
    duration_seconds: float
    transition: str = "crossfade"
    transition_duration_seconds: float = 0.45
    segment_type: str = "background"


class RemotionMusicTrack(BaseModel):
    source_path: str
    public_path: str
    volume: float = 0.4
    loop: bool = True
    start_seconds: float = 0.0
    duration_seconds: float


class RemotionPricePoint(BaseModel):
    date: str
    close: float
    volume: int = 0
    change_percent: float = 0.0


class RemotionChartData(BaseModel):
    title: str
    source: str
    synthetic: bool = False
    points: list[RemotionPricePoint] = Field(default_factory=list)


class RemotionSceneInput(BaseModel):
    scene_index: int
    scene_type: str
    card_type: str | None = None
    slot_id: str | None = None
    visual_style: str | None = None
    motion: str | None = None
    caption_style: str | None = None
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    narration: str
    headline: str
    subheadline: str = ""
    detail_text: str = ""
    bullets: list[str] = Field(default_factory=list)
    caption_text: str = ""
    source_ids: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    caption_beats: list[RemotionCaptionBeat] = Field(default_factory=list)
    visual_beats: list[RemotionVisualBeat] = Field(default_factory=list)
    chart: RemotionChartData | None = None
    asset_public_paths: list[str] = Field(default_factory=list)
    background_public_path: str | None = None


class RemotionRenderInput(BaseModel):
    video: RemotionVideoMeta
    template: RemotionTemplateMeta
    scenes: list[RemotionSceneInput]
    assets: list[RemotionAsset] = Field(default_factory=list)
    intro_video: RemotionAsset | None = None
    background_segments: list[RemotionBackgroundSegment] = Field(default_factory=list)
    text_beats: list[RemotionTextBeat] = Field(default_factory=list)
    music_track: RemotionMusicTrack | None = None
    public_dir: str | None = None
