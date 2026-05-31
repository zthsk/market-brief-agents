from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from jobs import autopilot
from media_engine.paths import review_bundle_dir
from models.database import connect, execute, init_db, query, upsert_video
from services.audio_generator import AudioGenerationResult


def test_weekday_autopilot_skips_non_trading_day(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.setattr(autopilot, "is_nyse_trading_day", lambda value: False)
    monkeypatch.setattr(
        autopilot,
        "daily_market_refresh",
        lambda **kwargs: pytest.fail("daily refresh should not run on closed market days"),
    )

    result = autopilot.run_weekday_autopilot(today=date(2026, 5, 30))

    assert result["skipped"] is True
    assert result["reason"] == "not_nyse_trading_day"
    assert result["selected_video_candidates"] == []
    assert Path(result["artifacts"]["run_json"]).exists()
    assert json.loads(Path("outputs/autopilot/latest.json").read_text(encoding="utf-8"))[
        "skipped"
    ] is True


def test_weekday_autopilot_generates_script_audio_and_remotion_video(
    tmp_path: Path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    _insert_event(1, "NOW")
    manifest = _write_manifest("NOW", 1)
    monkeypatch.setattr(autopilot, "is_nyse_trading_day", lambda value: True)
    monkeypatch.setattr(
        autopilot,
        "daily_market_refresh",
        lambda **kwargs: {
            "skipped": False,
            "video_candidates": [_candidate("NOW", 1)],
            "manifests": {"paths_by_event_id": {1: str(manifest)}},
        },
    )
    monkeypatch.setattr(
        autopilot,
        "generate_script_from_manifest_path",
        lambda path: _script_result(1, "NOW moved after management raised guidance."),
    )
    monkeypatch.setattr(autopilot, "generate_audio_result", _fake_audio_result)
    monkeypatch.setattr(autopilot, "_render_script_row", _fake_render_result)

    result = autopilot.run_weekday_autopilot(today="2026-05-29")

    assert result["skipped"] is False
    assert result["selected_count"] == 1
    assert result["candidate_results"][0]["script"]["status"] == "generated"
    assert result["candidate_results"][0]["tts"]["status"] == "generated"
    assert result["candidate_results"][0]["render"]["status"] == "rendered"
    assert result["candidate_results"][0]["render"]["renderer"] == "remotion"
    assert result["produced_videos"] == 1
    assert result["ready_to_upload"] == 1
    assert query("SELECT COUNT(*) AS count FROM scripts")[0]["count"] == 1
    assert query("SELECT status FROM videos")[0]["status"] == "ready_to_upload"


def test_weekday_autopilot_reuses_existing_ready_remotion_video(
    tmp_path: Path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    _insert_event(1, "STG")
    audio_path = Path("storage/audio/STG.wav")
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")
    video_path = Path("videos/STG.mp4")
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO scripts (id, event_id, title, script, audio_path, status)
            VALUES (1, 1, 'STG move', 'script', ?, 'approved')
            """,
            (str(audio_path),),
        )
    upsert_video(1, str(video_path), status="ready_to_upload")
    bundle = review_bundle_dir("STG", "2026-05-29", 1)
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps({"renderer": "remotion", "ready_for_posting": True}),
        encoding="utf-8",
    )
    (bundle / "quality_report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    (bundle / "sync_report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    monkeypatch.setattr(autopilot, "is_nyse_trading_day", lambda value: True)
    monkeypatch.setattr(
        autopilot,
        "daily_market_refresh",
        lambda **kwargs: {
            "skipped": False,
            "video_candidates": [_candidate("STG", 1)],
            "manifests": {"paths_by_event_id": {}},
        },
    )
    monkeypatch.setattr(
        autopilot,
        "generate_script_from_manifest_path",
        lambda path: pytest.fail("existing script should be reused"),
    )
    monkeypatch.setattr(
        autopilot,
        "generate_audio_result",
        lambda script_id, script: pytest.fail("existing audio should be reused"),
    )
    monkeypatch.setattr(
        autopilot,
        "_render_script_row",
        lambda script, **kwargs: pytest.fail("valid Remotion render should be reused"),
    )

    result = autopilot.run_weekday_autopilot(today=date(2026, 5, 29))

    candidate = result["candidate_results"][0]
    assert candidate["script"]["status"] == "reused"
    assert candidate["tts"]["status"] == "reused"
    assert candidate["render"]["status"] == "reused"
    assert candidate["render"]["valid"] is True
    assert result["ready_to_upload"] == 1


def test_weekday_autopilot_tts_failure_skips_render(tmp_path: Path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_event(1, "ADBE")
    manifest = _write_manifest("ADBE", 1)
    monkeypatch.setattr(autopilot, "is_nyse_trading_day", lambda value: True)
    monkeypatch.setattr(
        autopilot,
        "daily_market_refresh",
        lambda **kwargs: {
            "skipped": False,
            "video_candidates": [_candidate("ADBE", 1)],
            "manifests": {"paths_by_event_id": {1: str(manifest)}},
        },
    )
    monkeypatch.setattr(
        autopilot,
        "generate_script_from_manifest_path",
        lambda path: _script_result(1, "ADBE fell after cautious guidance."),
    )
    monkeypatch.setattr(
        autopilot,
        "generate_audio_result",
        lambda script_id, script: AudioGenerationResult(
            ok=False,
            path=None,
            provider="gemini",
            error="Gemini TTS failed",
        ),
    )
    monkeypatch.setattr(
        autopilot,
        "_render_script_row",
        lambda script, **kwargs: pytest.fail("render should wait for narration audio"),
    )

    result = autopilot.run_weekday_autopilot(today=date(2026, 5, 29))

    candidate = result["candidate_results"][0]
    assert candidate["tts"]["status"] == "failed"
    assert candidate["render"] == {}
    assert result["produced_videos"] == 0
    assert result["errors"] == 1


def test_weekday_autopilot_limits_top_three_and_defaults_gemini_digests_off(
    tmp_path: Path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(autopilot, "is_nyse_trading_day", lambda value: True)

    def fake_daily_refresh(**kwargs):
        calls.append(kwargs)
        return {
            "skipped": False,
            "video_candidates": [_candidate(f"T{i}", i) for i in range(1, 5)],
            "manifests": {"paths_by_event_id": {}},
        }

    monkeypatch.setattr(autopilot, "daily_market_refresh", fake_daily_refresh)
    monkeypatch.setattr(
        autopilot,
        "_process_candidate",
        lambda candidate, **kwargs: {
            "ticker": candidate["ticker"],
            "event_id": candidate["event_id"],
            "render": {"status": "skipped", "ready_for_posting": False},
            "errors": [],
        },
    )

    result = autopilot.run_weekday_autopilot(today=date(2026, 5, 29))

    assert result["selected_count"] == 3
    assert [item["ticker"] for item in result["selected_video_candidates"]] == ["T1", "T2", "T3"]
    assert calls[0]["video_limit"] == 3
    assert calls[0]["gemini_digest_video_ready"] is False
    assert calls[0]["gemini_digest_research_ready_batch"] is False

    calls.clear()
    autopilot.run_weekday_autopilot(today=date(2026, 5, 29), gemini_digests=True)

    assert calls[0]["gemini_digest_video_ready"] is True
    assert calls[0]["gemini_digest_research_ready_batch"] is True


def test_launchd_template_runs_weekdays_after_market_close():
    repo_root = Path(__file__).resolve().parents[1]
    plist = (repo_root / "scripts/com.marketbrief.weekday-autopilot.plist.template").read_text(
        encoding="utf-8"
    )
    installer = (repo_root / "scripts/install_weekday_autopilot_launchd.sh").read_text(
        encoding="utf-8"
    )

    assert "weekday-autopilot" in plist
    assert "<string>remotion</string>" in plist
    assert plist.count("<key>Weekday</key>") == 5
    assert plist.count("<integer>16</integer>") == 5
    assert plist.count("<integer>15</integer>") == 5
    assert "logs/autopilot/weekday-autopilot.out.log" in plist
    assert "launchctl bootstrap" in installer


def _setup_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()


def _insert_event(event_id: int, ticker: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (?, ?, 'daily_mover', '2026-05-29', 90, 'test catalyst', '{}')
            """,
            (event_id, ticker),
        )


def _write_manifest(ticker: str, event_id: int) -> Path:
    path = Path(f"outputs/script_manifests/{ticker}_2026-05-29_{event_id}/manifest.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ready_for_gemini_script": True}), encoding="utf-8")
    return path


def _candidate(ticker: str, event_id: int) -> dict:
    return {
        "ticker": ticker,
        "event_id": event_id,
        "decision": "video_ready",
        "video_score": 90,
    }


def _script_result(event_id: int, script: str) -> dict:
    return {
        "event_id": event_id,
        "db_fields": {
            "title": "Daily mover",
            "script": script,
            "description": "desc",
            "tags": ["stocks"],
        },
    }


def _fake_audio_result(script_id: int, script: str) -> AudioGenerationResult:
    path = Path(f"storage/audio/script-{script_id}.wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (str(path), script_id))
    return AudioGenerationResult(ok=True, path=str(path), provider="gemini")


def _fake_render_result(script_row: dict, **kwargs) -> dict:
    video_path = Path(f"videos/script-{script_row['id']}.mp4")
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    upsert_video(int(script_row["id"]), str(video_path), status="ready_to_upload")
    return {
        "ok": True,
        "video_path": str(video_path),
        "bundle_path": "outputs/review/test",
        "renderer": kwargs.get("renderer"),
        "ready_for_posting": True,
        "sync_passed": True,
        "quality_passed": True,
        "warnings": 0,
    }
