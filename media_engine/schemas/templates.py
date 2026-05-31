from __future__ import annotations

from pydantic import BaseModel, Field


class SceneSlot(BaseModel):
    slot_id: str
    card_type: str
    narration_role: str
    visual_style: str = "dark_grid"
    motion: str = "takeaway_hold"
    caption_style: str = "default_caption"
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    min_duration: float = 3.0
    max_duration: float = 8.0


class VideoTemplate(BaseModel):
    template_id: str
    name: str
    story_types: list[str] = Field(default_factory=list)
    target_min_duration: float = 30.0
    target_max_duration: float = 55.0
    scene_slots: list[SceneSlot]
    fallback_template_id: str = "three_things"


class TemplateSelectionResult(BaseModel):
    selected_template_id: str
    candidate_template_ids: list[str] = Field(default_factory=list)
    story_type: str | None = None
    reason: str
    warnings: list[str] = Field(default_factory=list)
