from __future__ import annotations

import json
import sqlite3
import time
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

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except Exception:  # pragma: no cover - optional integration
    SqliteSaver = None


AGENT_RUN_ROOT = OUTPUT_ROOT / "agent_runs"
DEFAULT_CHECKPOINT_PATH = Path("data/langgraph_checkpoints.sqlite")
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
    interrupt_before: list[str] | None = None,
    resume: bool = False,
    checkpoint_path: str | Path | None = None,
) -> dict[str, Any]:
    selected_thread_id = thread_id or f"local-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    if resume and not thread_id:
        raise ValueError("resume requires --thread-id")
    checkpoint = Path(checkpoint_path or DEFAULT_CHECKPOINT_PATH)
    interrupt_nodes = _normalize_interrupts(interrupt_before)
    resume_state = _resume_state(selected_thread_id) if resume else None
    if resume and resume_state is None:
        raise ValueError(f"No saved agent run found for thread_id={selected_thread_id}")
    state: MarketBriefAgentState = resume_state or {
        "run_id": str(uuid4()),
        "thread_id": selected_thread_id,
        "mode": "synthetic_demo" if demo else "local_pipeline",
        "status": "running",
        "paused_at": None,
        "checkpoint_path": str(checkpoint),
        "demo": demo,
        "skip_render": skip_render,
        "force_script": force_script,
        "interrupt_before": interrupt_nodes,
        "event_ids": event_ids or [],
        "research_bundle_paths": [],
        "manifest_paths": [],
        "retrieved_context": [],
        "script_ids": [],
        "script_bundle_paths": [],
        "video_paths": [],
        "quality_reports": [],
        "errors": [],
        "node_traces": [],
    }
    state.update(
        {
            "thread_id": selected_thread_id,
            "status": "running",
            "checkpoint_path": str(checkpoint),
            "skip_render": skip_render,
            "force_script": force_script,
            "interrupt_before": interrupt_nodes,
        }
    )
    if event_ids is not None:
        state["event_ids"] = event_ids
    if demo:
        state["demo"] = True
        state["mode"] = "synthetic_demo"
    if resume_state:
        state["_resume_from"] = resume_state.get("paused_at")

    graph = build_market_brief_graph(interrupt_before=interrupt_nodes, checkpoint_path=checkpoint)
    config = {"configurable": {"thread_id": selected_thread_id}}
    invoke_input: MarketBriefAgentState | None = (
        None if resume and not isinstance(graph, SequentialAgentGraph) else state
    )
    result = dict(graph.invoke(invoke_input, config=config))
    result = _apply_checkpoint_status(graph, config, result)
    _write_agent_run_artifact(result)
    return result


def inspect_agent_run(thread_id: str) -> dict[str, Any]:
    path = AGENT_RUN_ROOT / f"{_safe_thread_id(thread_id)}.json"
    if not path.exists():
        return {"thread_id": thread_id, "found": False, "path": str(path)}
    return {"thread_id": thread_id, "found": True, "path": str(path), "state": _read_json(path)}


def list_agent_runs(limit: int | None = None) -> list[dict[str, Any]]:
    if not AGENT_RUN_ROOT.exists():
        return []
    rows = []
    for path in sorted(AGENT_RUN_ROOT.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        state = _read_json(path)
        rows.append(
            {
                "thread_id": state.get("thread_id") or path.stem,
                "run_id": state.get("run_id"),
                "status": state.get("status"),
                "paused_at": state.get("paused_at"),
                "finished_at": state.get("finished_at"),
                "mode": state.get("mode"),
                "events": len(state.get("event_ids") or []),
                "scripts": len(state.get("script_ids") or []),
                "videos": len(state.get("video_paths") or []),
                "errors": len(state.get("errors") or []),
                "trace_steps": len(state.get("node_traces") or []),
                "path": str(path),
            }
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def build_market_brief_graph(
    *,
    interrupt_before: list[str] | None = None,
    checkpoint_path: str | Path | None = None,
) -> Any:
    nodes = _traced_node_functions()
    if StateGraph is None:
        return SequentialAgentGraph(nodes, interrupt_before=interrupt_before or [])
    graph = StateGraph(MarketBriefAgentState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)
    graph.add_edge(START, GRAPH_NODE_NAMES[0])
    for current, next_node in zip(GRAPH_NODE_NAMES, GRAPH_NODE_NAMES[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(GRAPH_NODE_NAMES[-1], END)
    checkpointer = _sqlite_checkpointer(Path(checkpoint_path or DEFAULT_CHECKPOINT_PATH))
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or None,
    )
    if getattr(checkpointer, "_market_brief_connection", None) is not None:
        compiled._market_brief_checkpoint_connection = checkpointer._market_brief_connection
    return compiled


class SequentialAgentGraph:
    def __init__(
        self,
        nodes: dict[str, Callable[[MarketBriefAgentState], dict[str, Any]]],
        *,
        interrupt_before: list[str],
    ) -> None:
        self.nodes = nodes
        self.interrupt_before = set(interrupt_before)

    def invoke(
        self,
        state: MarketBriefAgentState | None,
        config: dict[str, Any] | None = None,
    ) -> MarketBriefAgentState:
        del config
        current = dict(state or {})
        resume_from = current.pop("_resume_from", None)
        start_index = GRAPH_NODE_NAMES.index(resume_from) if resume_from in GRAPH_NODE_NAMES else 0
        for name in GRAPH_NODE_NAMES[start_index:]:
            if name in self.interrupt_before and name != resume_from:
                current["status"] = "paused"
                current["paused_at"] = name
                current["next_action"] = name
                return current
            update = self.nodes[name](current)
            current.update(update)
            if current.get("status") in {"paused", "failed"}:
                return current
        current["status"] = "completed"
        current["paused_at"] = None
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


def _traced_node_functions() -> dict[str, Callable[[MarketBriefAgentState], dict[str, Any]]]:
    return {name: _trace_node(name, fn) for name, fn in _node_functions().items()}


def _trace_node(
    name: str,
    fn: Callable[[MarketBriefAgentState], dict[str, Any]],
) -> Callable[[MarketBriefAgentState], dict[str, Any]]:
    def wrapped(state: MarketBriefAgentState) -> dict[str, Any]:
        started = datetime.now(UTC)
        started_perf = time.perf_counter()
        before = _state_digest(state)
        try:
            update = fn(state)
            status = update.get("status") or state.get("status") or "running"
            error = None
        except Exception as exc:  # pragma: no cover - safety net for unexpected node errors
            update = {
                "errors": [*state.get("errors", []), f"{name}_failed:{exc}"],
                "status": "failed",
                "next_action": "finalize_run",
            }
            status = "failed"
            error = str(exc)
        finished = datetime.now(UTC)
        trace = {
            "node": name,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_ms": round((time.perf_counter() - started_perf) * 1000, 3),
            "status": status,
            "input": before,
            "output": _update_digest(update),
            "error": error,
        }
        return {**update, "node_traces": [*state.get("node_traces", []), trace]}

    return wrapped


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
    status = "failed" if state.get("status") == "failed" else "completed"
    payload = {**state, "status": status, "paused_at": None, "finished_at": datetime.now(UTC).isoformat()}
    path = _write_agent_run_artifact(payload)
    return {
        "status": status,
        "paused_at": None,
        "summary": {**state.get("summary", {}), "run_artifact": str(path)},
        "next_action": "done",
    }


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


def _normalize_interrupts(values: list[str] | None) -> list[str]:
    aliases = {
        "script": "generate_scripts",
        "scripts": "generate_scripts",
        "generate": "generate_scripts",
        "generate_scripts": "generate_scripts",
        "render": "render_or_skip_videos",
        "video": "render_or_skip_videos",
        "videos": "render_or_skip_videos",
        "render_or_skip_videos": "render_or_skip_videos",
    }
    output = []
    for value in values or []:
        node = aliases.get(str(value).strip().lower())
        if node and node not in output:
            output.append(node)
    return output


def _resume_state(thread_id: str) -> MarketBriefAgentState | None:
    path = AGENT_RUN_ROOT / f"{_safe_thread_id(thread_id)}.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    return payload if payload else None


def _sqlite_checkpointer(path: Path) -> Any:
    if SqliteSaver is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    saver._market_brief_connection = conn
    return saver


def _apply_checkpoint_status(graph: Any, config: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    snapshot = None
    if hasattr(graph, "get_state"):
        try:
            snapshot = graph.get_state(config)
        except Exception:
            snapshot = None
    next_nodes = tuple(getattr(snapshot, "next", ()) or ()) if snapshot is not None else ()
    if next_nodes:
        paused_at = str(next_nodes[0])
        output = {
            **result,
            "status": "paused",
            "paused_at": paused_at,
            "next_action": paused_at,
        }
    else:
        status = result.get("status") or "completed"
        output = {**result, "status": status, "paused_at": None}
    summary = {
        **output.get("summary", {}),
        "status": output.get("status"),
        "paused_at": output.get("paused_at"),
        "checkpoint_path": output.get("checkpoint_path"),
        "trace_steps": len(output.get("node_traces") or []),
    }
    return {**output, "summary": summary}


def _write_agent_run_artifact(state: dict[str, Any]) -> Path:
    AGENT_RUN_ROOT.mkdir(parents=True, exist_ok=True)
    thread_id = str(state.get("thread_id") or "default")
    path = AGENT_RUN_ROOT / f"{_safe_thread_id(thread_id)}.json"
    payload = {
        **state,
        "finished_at": state.get("finished_at") or datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _state_digest(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": state.get("status"),
        "paused_at": state.get("paused_at"),
        "next_action": state.get("next_action"),
        "events": len(state.get("event_ids") or []),
        "manifests": len(state.get("manifest_paths") or []),
        "retrieved_context": len(state.get("retrieved_context") or []),
        "scripts": len(state.get("script_ids") or []),
        "videos": len(state.get("video_paths") or []),
        "errors": len(state.get("errors") or []),
    }


def _update_digest(update: dict[str, Any]) -> dict[str, Any]:
    digest = {
        key: value
        for key, value in update.items()
        if key
        in {
            "status",
            "paused_at",
            "next_action",
            "summary",
            "errors",
            "event_ids",
            "manifest_paths",
            "script_ids",
            "script_bundle_paths",
            "video_paths",
        }
    }
    if "node_traces" in update:
        digest["node_traces"] = len(update.get("node_traces") or [])
    if "retrieved_context" in update:
        digest["retrieved_context"] = len(update.get("retrieved_context") or [])
    if "quality_reports" in update:
        digest["quality_reports"] = len(update.get("quality_reports") or [])
    return _json_safe(digest)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        return str(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
