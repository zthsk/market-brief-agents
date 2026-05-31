from __future__ import annotations

from pathlib import Path

from agentic.graph import inspect_agent_run, run_agent_pipeline
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
