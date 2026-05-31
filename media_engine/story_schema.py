from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Direction = Literal["up", "down", "flat"]
SceneType = Literal[
    "hook",
    "price_card",
    "chart",
    "bullet_reveal",
    "context",
    "takeaway",
    "price_action",
    "news",
    "earnings",
    "financials",
    "analyst",
    "company",
    "industry",
    "risk",
    "comparison",
    "timeline",
    "conclusion",
    "outro",
]


class PriceCard(BaseModel):
    price: str = "$0.00"
    change_pct: str = "+0.0%"
    direction: Direction = "flat"
    period: str = "today"


class StorySection(BaseModel):
    type: Literal["catalyst", "context", "watch", "risk"] = "context"
    title: str
    bullets: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("title")
    @classmethod
    def short_title(cls, value: str) -> str:
        return _trim_words(value, 5)

    @field_validator("bullets")
    @classmethod
    def short_bullets(cls, values: list[str]) -> list[str]:
        return [_trim_words(value, 9) for value in values if value][:3]


class Story(BaseModel):
    ticker: str
    company: str
    date: str
    hook: str
    price_card: PriceCard
    sections: list[StorySection] = Field(default_factory=list)
    chart_insight: str
    takeaway: str
    sources: list[str] = Field(default_factory=list)
    disclaimer: str = "Educational only. Not financial advice."

    @field_validator("hook", "chart_insight", "takeaway")
    @classmethod
    def short_major_text(cls, value: str) -> str:
        return _trim_words(value, 12)


class Scene(BaseModel):
    scene_type: SceneType
    duration: float
    headline: str
    subheadline: str = ""
    bullets: list[str] = Field(default_factory=list)
    narration: str
    caption_text: str
    chart_path: str | None = None
    importance: Literal["high", "medium", "low"] = "medium"
    confidence_level: Literal["high", "medium"] = "medium"
    source_ids: list[str] = Field(default_factory=list)
    visual_requirements: list[str] = Field(default_factory=list)
    audio_path: str | None = None
    audio_duration: float | None = None
    show_footer: bool = True
    progress_start: float = 0.0
    progress_end: float = 1.0
    template_id: str | None = None
    slot_id: str | None = None
    card_type: str | None = None
    visual_style: str = "dark_grid"
    motion: str | None = None
    caption_style: str = "default_caption"
    template_warnings: list[str] = Field(default_factory=list)

    @field_validator("headline", "subheadline", "caption_text")
    @classmethod
    def short_text(cls, value: str) -> str:
        return _trim_words(value, 12)

    @field_validator("bullets")
    @classmethod
    def short_scene_bullets(cls, values: list[str]) -> list[str]:
        return [_trim_words(value, 9) for value in values if value][:3]


class Caption(BaseModel):
    index: int
    start: float
    end: float
    text: str


class QualityReport(BaseModel):
    passed: bool
    duration_sec: float
    content_duration_sec: float = 0.0
    final_duration_sec: float = 0.0
    resolution: str
    has_hook: bool
    has_captions: bool
    has_disclaimer: bool
    has_chart: bool
    has_audio: bool
    max_scene_duration: float
    has_production_plan: bool = False
    has_contact_sheet: bool = False
    has_outro: bool = False
    has_template_selection: bool = False
    template_id: str | None = None
    sync_passed: bool = False
    ready_for_posting: bool = False
    critical_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _trim_words(value: str, max_words: int) -> str:
    words = str(value or "").replace("\n", " ").split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(".,;:") + "..."
