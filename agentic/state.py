from __future__ import annotations

from typing import Any, TypedDict


class MarketBriefAgentState(TypedDict, total=False):
    run_id: str
    thread_id: str
    mode: str
    demo: bool
    skip_render: bool
    force_script: bool
    event_ids: list[int]
    research_bundle_paths: list[str]
    manifest_paths: list[str]
    retrieved_context: list[dict[str, Any]]
    script_ids: list[int]
    script_bundle_paths: list[str]
    video_paths: list[str]
    quality_reports: list[dict[str, Any]]
    errors: list[str]
    next_action: str
    summary: dict[str, Any]
