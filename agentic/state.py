from __future__ import annotations

from typing import Any, TypedDict


class MarketBriefAgentState(TypedDict, total=False):
    run_id: str
    thread_id: str
    mode: str
    status: str
    paused_at: str | None
    checkpoint_path: str
    demo: bool
    skip_render: bool
    force_script: bool
    interrupt_before: list[str]
    event_ids: list[int]
    research_bundle_paths: list[str]
    manifest_paths: list[str]
    retrieved_context: list[dict[str, Any]]
    script_ids: list[int]
    script_bundle_paths: list[str]
    video_paths: list[str]
    quality_reports: list[dict[str, Any]]
    errors: list[str]
    node_traces: list[dict[str, Any]]
    next_action: str
    summary: dict[str, Any]
