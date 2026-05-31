from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ManifestEvent(BaseModel):
    id: int
    ticker: str
    company: str
    event_type: str | None = None
    date: str
    score: int | None = None
    reason: str
    analysis: dict[str, Any] = Field(default_factory=dict)


class ResearchReview(BaseModel):
    bundle_path: str
    approved: bool
    source_count: int
    discovery_signal_count: int = 0
    rejected_source_count: int = 0


class ResearchSource(BaseModel):
    source_id: str
    provider: str
    title: str | None = None
    url: str
    source: str | None = None
    published_at: str | None = None
    highlights: list[str] = Field(default_factory=list)
    source_quality: dict[str, Any] = Field(default_factory=dict)
    source_tier: int | None = None
    source_tier_label: str | None = None
    claim_use_policy: str | None = None
    is_official_company_release: bool = False
    requires_confirmation: bool = False


class DiscoverySignal(BaseModel):
    discovery_id: str
    provider: str
    title: str | None = None
    url: str | None = None
    source: str | None = None
    published_at: str | None = None
    highlights: list[str] = Field(default_factory=list)
    source_quality: dict[str, Any] = Field(default_factory=dict)
    source_tier: int | None = None
    source_tier_label: str | None = None
    claim_use_policy: str | None = None
    is_official_company_release: bool = False
    requires_confirmation: bool = True
    usage_policy: str = "Discovery only. Do not cite as source_ids or factual support."


class GeminiScriptRequest(BaseModel):
    output_format: dict[str, Any]
    narrative_beats: list[str]
    duration_target: str
    voice: str
    constraints: list[str]


class ScriptManifest(BaseModel):
    automation_stage: str
    ready_for_gemini_script: bool
    ready_for_tts: bool = False
    ready_for_render: bool = False
    ready_for_posting: bool = False
    event: ManifestEvent
    market_context: dict[str, Any] = Field(default_factory=dict)
    approved_research: dict[str, list[ResearchSource]] = Field(default_factory=dict)
    citable_sources: list[ResearchSource] = Field(default_factory=list)
    context_sources: list[ResearchSource] = Field(default_factory=list)
    discovery_signals: list[DiscoverySignal] = Field(default_factory=list)
    discovery_sources: list[DiscoverySignal] = Field(default_factory=list)
    rejected_sources: list[DiscoverySignal] = Field(default_factory=list)
    research_review: ResearchReview
    gemini_script_request: GeminiScriptRequest
    next_stage: str

    @model_validator(mode="after")
    def validate_script_readiness(self) -> "ScriptManifest":
        sources = self.sources
        if self.ready_for_gemini_script and not self.research_review.approved:
            raise ValueError("ready_for_gemini_script requires approved research")
        if self.ready_for_gemini_script and not sources:
            raise ValueError("ready_for_gemini_script requires approved research sources")
        expected = self.research_review.source_count
        if expected != len(sources):
            raise ValueError(
                f"research_review.source_count={expected} does not match {len(sources)} sources"
            )
        return self

    @property
    def sources(self) -> list[ResearchSource]:
        grouped = [source for rows in self.approved_research.values() for source in rows]
        preferred = [*self.citable_sources, *self.context_sources]
        return preferred or grouped


def load_script_manifest(path: str | Path) -> ScriptManifest:
    manifest_path = Path(path)
    return ScriptManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def manifest_to_json(manifest: ScriptManifest) -> dict[str, Any]:
    return json.loads(manifest.model_dump_json())


ProductionSceneType = Literal[
    "hook",
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
]
AssetType = Literal[
    "stock_chart",
    "price_move",
    "volume_chart",
    "company_logo",
    "company_photo",
    "ceo_photo",
    "earnings_summary",
    "financial_metric",
    "news_headline",
    "analyst_rating",
    "analyst_price_target",
    "industry_broll",
    "product_image",
    "competitor_logo",
    "timeline",
    "calendar_event",
    "warning_indicator",
    "market_statistic",
]
Importance = Literal["high", "medium", "low"]
ConfidenceLevel = Literal["high", "medium"]


class VideoMetadata(BaseModel):
    title: str = Field(max_length=80)
    ticker: str
    estimated_duration_seconds: int = Field(ge=60, le=75)


class VisualRequirement(BaseModel):
    asset_type: AssetType


class OnScreenText(BaseModel):
    headline: str
    subheadline: str

    @field_validator("headline")
    @classmethod
    def headline_max_words(cls, value: str) -> str:
        if _word_count(value) > 5:
            raise ValueError("headline must be 5 words or fewer")
        return value

    @field_validator("subheadline")
    @classmethod
    def subheadline_max_words(cls, value: str) -> str:
        if _word_count(value) > 8:
            raise ValueError("subheadline must be 8 words or fewer")
        return value


class AssetRequest(BaseModel):
    asset_type: AssetType
    query: str | None = None
    reason: str | None = None


class ProductionScene(BaseModel):
    id: int
    type: ProductionSceneType
    importance: Importance
    confidence_level: ConfidenceLevel
    narration: str
    on_screen_text: OnScreenText
    highlights: list[str] = Field(default_factory=list, min_length=2, max_length=3)
    source_ids: list[str] = Field(default_factory=list)
    visual_requirements: list[VisualRequirement] = Field(default_factory=list)

    @field_validator("highlights")
    @classmethod
    def highlights_are_short_bullets(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if len(cleaned) < 2 or len(cleaned) > 3:
            raise ValueError("highlights must contain 2-3 scene card bullets")
        for value in cleaned:
            count = _word_count(value)
            if count < 2 or count > 10:
                raise ValueError("each highlight must be 2-10 words")
        return cleaned


class GeneratedScriptPackage(BaseModel):
    video_metadata: VideoMetadata
    asset_requests: list[AssetRequest] = Field(default_factory=list)
    scenes: list[ProductionScene] = Field(min_length=4, max_length=8)

    @model_validator(mode="after")
    def validate_scene_plan(self) -> "GeneratedScriptPackage":
        if self.scenes[0].type != "hook":
            raise ValueError("first scene must be hook")
        if self.scenes[0].importance != "high":
            raise ValueError("hook scene importance must be high")
        expected_ids = list(range(1, len(self.scenes) + 1))
        actual_ids = [scene.id for scene in self.scenes]
        if actual_ids != expected_ids:
            raise ValueError("scene ids must be sequential starting at 1")
        return self


def _word_count(text: str) -> int:
    return len(str(text or "").split())
