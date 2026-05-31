from __future__ import annotations

from pathlib import Path

from agentic.graph import StateGraph, inspect_agent_run, run_agent_pipeline
from agentic.rag import retrieve_context
from models.database import query
from services.demo_data import load_synthetic_demo_data


def test_load_synthetic_demo_data_creates_approved_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))

    result = load_synthetic_demo_data()

    assert result["ticker"] == "ALTA"
    assert Path(result["research_bundle_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert query("SELECT COUNT(*) AS count FROM research_sources")[0]["count"] == 2


def test_rag_retrieves_synthetic_source_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))
    result = load_synthetic_demo_data()

    docs = retrieve_context("pilot conversion AI platform", event_id=int(result["event_id"]))

    assert docs
    assert docs[0]["metadata"]["event_id"] == result["event_id"]
    assert docs[0]["metadata"]["source_tier"] in {1, 2}


def test_agent_pipeline_demo_skip_render_writes_run_artifact(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))

    result = run_agent_pipeline(demo=True, thread_id="demo-test", skip_render=True)

    assert result["summary"]["ready"] is True
    assert result["summary"]["scripts"] == 1
    assert result["summary"]["videos"] == 0
    assert Path(result["summary"]["run_artifact"]).exists()
    inspected = inspect_agent_run("demo-test")
    assert inspected["found"] is True
    assert inspected["state"]["summary"]["ready"] is True
    assert inspected["state"]["node_traces"]


def test_agent_pipeline_interrupts_and_resumes_before_script(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))

    paused = run_agent_pipeline(
        demo=True,
        thread_id="pause-before-script",
        skip_render=True,
        interrupt_before=["generate_scripts"],
    )

    assert paused["status"] == "paused"
    assert paused["paused_at"] == "generate_scripts"
    assert paused["script_ids"] == []
    if StateGraph is not None:
        assert Path("data/langgraph_checkpoints.sqlite").exists()
    assert any(trace["node"] == "retrieve_evidence_context" for trace in paused["node_traces"])

    resumed = run_agent_pipeline(
        thread_id="pause-before-script",
        skip_render=True,
        resume=True,
    )

    assert resumed["status"] == "completed"
    assert resumed["summary"]["ready"] is True
    assert resumed["script_ids"]
    assert any(trace["node"] == "generate_scripts" for trace in resumed["node_traces"])
