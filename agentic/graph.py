from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentic.rag import retrieve_context
from agentic.state import MarketBriefAgentState
from jobs.pipeline import _render_script_row
from media_engine.paths import OUTPUT_ROOT
from media_engine.script_manifest import prepare_script_manifest
from models.database import init_db, insert_script, query
from services.demo_data import load_synthetic_demo_data
from services.script_generator import generate_script_from_manifest_path
from services.web_research import collect_event_research, research_bundle_path, research_for_event


try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - optional integration
    END = START = None
    StateGraph = None


AGENT_RUN_ROOT = OUTPUT_ROOT / "agent_runs"
GRAPH_NODE_NAMES = [
    "initialize_run",
    "load_or_seed_demo_data",
    "collect_market_context",
    "detect_and_rank_events",
    "research_events",
    "prepare_script_manifests",
    "retrieve_evidence_context",
    "generate_scripts",
    "render_or_skip_videos",
    "quality_gate",
    "finalize_run",
]


def run_agent_pipeline(
    *,
    demo: bool = False,
    thread_id: str | None = None,
    skip_render: bool = True,
    force_script: bool = False,
    event_ids: list[int] | None = None,
) -> dict[str, Any]:
    state: MarketBriefAgentState = {
        "run_id": str(uuid4()),
        "thread_id": thread_id or f"local-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        "mode": "synthetic_demo" if demo else "local_pipeline",
        "demo": demo,
        "skip_render": skip_render,
        "force_script": force_script,
        "event_ids": event_ids or [],
        "errors": [],
    }
    graph = build_market_brief_graph()
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    return dict(result)


def inspect_agent_run(thread_id: str) -> dict[str, Any]:
    path = AGENT_RUN_ROOT / f"{_safe_thread_id(thread_id)}.json"
    if not path.exists():
        return {"thread_id": thread_id, "found": False, "path": str(path)}
    return {"thread_id": thread_id, "found": True, "path": str(path), "state": _read_json(path)}


def build_market_brief_graph() -> Any:
    if StateGraph is None:
        return SequentialAgentGraph(_node_functions())
    graph = StateGraph(MarketBriefAgentState)
    for name, fn in _node_functions().items():
        graph.add_node(name, fn)
    graph.add_edge(START, GRAPH_NODE_NAMES[0])
    for current, next_node in zip(GRAPH_NODE_NAMES, GRAPH_NODE_NAMES[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(GRAPH_NODE_NAMES[-1], END)
    return graph.compile()


class SequentialAgentGraph:
    def __init__(self, nodes: dict[str, Callable[[MarketBriefAgentState], dict[str, Any]]]) -> None:
        self.nodes = nodes

    def invoke(
        self,
        state: MarketBriefAgentState,
        config: dict[str, Any] | None = None,
    ) -> MarketBriefAgentState:
        del config
        current = dict(state)
        for name in GRAPH_NODE_NAMES:
            update = self.nodes[name](current)
            current.update(update)
        return current


def _node_functions() -> dict[str, Callable[[MarketBriefAgentState], dict[str, Any]]]:
    return {
        "initialize_run": initialize_run,
        "load_or_seed_demo_data": load_or_seed_demo_data,
        "collect_market_context": collect_market_context,
        "detect_and_rank_events": detect_and_rank_events,
        "research_events": research_events,
        "prepare_script_manifests": prepare_script_manifests,
        "retrieve_evidence_context": retrieve_evidence_context,
        "generate_scripts": generate_scripts,
        "render_or_skip_videos": render_or_skip_videos,
        "quality_gate": quality_gate,
        "finalize_run": finalize_run,
    }


def initialize_run(state: MarketBriefAgentState) -> dict[str, Any]:
    init_db()
    return {"next_action": "load_or_seed_demo_data"}


def load_or_seed_demo_data(state: MarketBriefAgentState) -> dict[str, Any]:
    if not state.get("demo"):
        return {"next_action": "collect_market_context"}
    demo = load_synthetic_demo_data()
    return {
        "event_ids": [int(demo["event_id"])],
        "research_bundle_paths": [demo["research_bundle_path"]],
        "manifest_paths": [demo["manifest_path"]],
        "next_action": "collect_market_context",
    }


def collect_market_context(state: MarketBriefAgentState) -> dict[str, Any]:
    if state.get("event_ids"):
        return {"next_action": "detect_and_rank_events"}
    rows = query(
        """
        SELECT id
        FROM events
        ORDER BY score DESC, created_at DESC
        LIMIT 5
        """
    )
    return {
        "event_ids": [int(row["id"]) for row in rows],
        "next_action": "detect_and_rank_events",
    }


def detect_and_rank_events(state: MarketBriefAgentState) -> dict[str, Any]:
    event_ids = [int(event_id) for event_id in state.get("event_ids", [])]
    if not event_ids:
        return {
            "errors": [*state.get("errors", []), "no_events_selected"],
            "next_action": "finalize_run",
        }
    return {"event_ids": event_ids[:5], "next_action": "research_events"}


def research_events(state: MarketBriefAgentState) -> dict[str, Any]:
    bundle_paths = list(state.get("research_bundle_paths", []))
    errors = list(state.get("errors", []))
    if state.get("demo"):
        return {"research_bundle_paths": bundle_paths, "errors": errors, "next_action": "prepare_script_manifests"}
    for event_id in state.get("event_ids", []):
        event = _event(int(event_id))
        if not research_for_event(int(event_id)):
            try:
                collect_event_research(event)
            except Exception as exc:
                errors.append(f"research_failed:{event_id}:{exc}")
        bundle_paths.append(str(research_bundle_path(event)))
    return {"research_bundle_paths": bundle_paths, "errors": errors, "next_action": "prepare_script_manifests"}


def prepare_script_manifests(state: MarketBriefAgentState) -> dict[str, Any]:
    paths = list(dict.fromkeys(state.get("manifest_paths", [])))
    errors = list(state.get("errors", []))
    existing = set(paths)
    for event_id in state.get("event_ids", []):
        try:
            result = prepare_script_manifest(int(event_id))
            path = str(result["manifest_path"])
            if path not in existing:
                paths.append(path)
                existing.add(path)
            if not result["manifest"].get("ready_for_gemini_script"):
                errors.append(f"manifest_not_ready:{event_id}")
        except Exception as exc:
            errors.append(f"manifest_failed:{event_id}:{exc}")
    return {"manifest_paths": paths, "errors": errors, "next_action": "retrieve_evidence_context"}


def retrieve_evidence_context(state: MarketBriefAgentState) -> dict[str, Any]:
    contexts = []
    for event_id in state.get("event_ids", []):
        event = _event(int(event_id))
        query_text = f"{event['ticker']} {event.get('reason') or ''}"
        contexts.extend(retrieve_context(query_text, event_id=int(event_id), k=5))
    return {"retrieved_context": contexts, "next_action": "generate_scripts"}


def generate_scripts(state: MarketBriefAgentState) -> dict[str, Any]:
    script_ids = list(state.get("script_ids", []))
    bundle_paths = list(state.get("script_bundle_paths", []))
    errors = list(state.get("errors", []))
    for manifest_path in state.get("manifest_paths", []):
        try:
            result = generate_script_from_manifest_path(manifest_path)
            existing = _latest_script_for_event(int(result["event_id"]))
            if existing and not state.get("force_script"):
                script_ids.append(int(existing["id"]))
                bundle_paths.append(str(result["bundle_path"]))
                continue
            script_id = insert_script(int(result["event_id"]), result["db_fields"])
            script_ids.append(int(script_id))
            bundle_paths.append(str(result["bundle_path"]))
        except Exception as exc:
            errors.append(f"script_failed:{manifest_path}:{exc}")
    return {
        "script_ids": list(dict.fromkeys(script_ids)),
        "script_bundle_paths": list(dict.fromkeys(bundle_paths)),
        "errors": errors,
        "next_action": "render_or_skip_videos",
    }


def render_or_skip_videos(state: MarketBriefAgentState) -> dict[str, Any]:
    if state.get("skip_render", True):
        return {"video_paths": [], "next_action": "quality_gate"}
    video_paths = []
    reports = []
    errors = list(state.get("errors", []))
    for script_id in state.get("script_ids", []):
        rows = query("SELECT * FROM scripts WHERE id = ?", (int(script_id),))
        if not rows:
            errors.append(f"script_missing:{script_id}")
            continue
        result = _render_script_row(rows[0], renderer="remotion")
        if result.get("ok") and result.get("video_path"):
            video_paths.append(str(result["video_path"]))
            reports.append(result)
        else:
            errors.append(f"render_failed:{script_id}:{result.get('error') or 'unknown'}")
    return {
        "video_paths": video_paths,
        "quality_reports": reports,
        "errors": errors,
        "next_action": "quality_gate",
    }


def quality_gate(state: MarketBriefAgentState) -> dict[str, Any]:
    script_count = len(state.get("script_ids", []))
    render_required = not state.get("skip_render", True)
    ready = script_count > 0 and (not render_required or bool(state.get("video_paths")))
    return {
        "summary": {
            "ready": ready,
            "mode": state.get("mode"),
            "events": len(state.get("event_ids", [])),
            "retrieved_context": len(state.get("retrieved_context", [])),
            "scripts": script_count,
            "videos": len(state.get("video_paths", [])),
            "errors": len(state.get("errors", [])),
            "skip_render": state.get("skip_render", True),
        },
        "next_action": "finalize_run",
    }


def finalize_run(state: MarketBriefAgentState) -> dict[str, Any]:
    AGENT_RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = AGENT_RUN_ROOT / f"{_safe_thread_id(str(state['thread_id']))}.json"
    payload = {**state, "finished_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"summary": {**state.get("summary", {}), "run_artifact": str(path)}, "next_action": "done"}


def _event(event_id: int) -> dict[str, Any]:
    rows = query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not rows:
        raise ValueError(f"No event found for id {event_id}")
    return rows[0]


def _latest_script_for_event(event_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT *
        FROM scripts
        WHERE event_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (event_id,),
    )
    return rows[0] if rows else None


def _safe_thread_id(thread_id: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_." else "-" for char in thread_id)
    return safe.strip(".-") or "default"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
