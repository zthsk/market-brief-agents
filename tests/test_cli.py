from __future__ import annotations

import json
import sys

from backend.cli import main


def test_status_command_outputs_project_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["marketbrief", "status"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["db_path"] == str(tmp_path / "market_brief_agents.db")
    assert payload["counts"]["companies"] == 0
    assert payload["latest_price_date"] is None


def test_status_command_loads_dotenv(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    (tmp_path / ".env").write_text(
        'SEC_USER_AGENT="Market Brief Agents local MVP pingkshitiz@gmail.com"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["marketbrief", "status"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["sec_user_agent_configured"] is True


def test_render_videos_command_outputs_render_counts(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.render_existing_videos",
        lambda limit, approved_only=False, template="news-studio", max_duration=75, captions=True, renderer=None: {
            "eligible": limit or 0,
            "videos": 1,
            "errors": int(approved_only),
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["marketbrief", "render-videos", "--limit", "2", "--approved-only"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"eligible": 2, "errors": 1, "videos": 1}


def test_render_videos_command_surfaces_production_status(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.render_existing_videos",
        lambda limit, approved_only=False, template="news-studio", max_duration=75, captions=True, renderer=None: {
            "eligible": 1,
            "videos": 1,
            "errors": 0,
            "last_video": "videos/ADBE_2026-05-29_1.mp4",
            "last_bundle": "outputs/review/ADBE_2026-05-29_1",
            "last_ready_for_posting": True,
            "renders": [
                {
                    "video_path": "videos/ADBE_2026-05-29_1.mp4",
                    "bundle_path": "outputs/review/ADBE_2026-05-29_1",
                    "sync_passed": True,
                    "quality_passed": True,
                    "warnings": 0,
                    "ready_for_posting": True,
                    "template_id": "why_stock_moved",
                    "template_name": "Why The Stock Moved",
                    "story_type": "news",
                    "template_reason": "Selected why_stock_moved for news story signals.",
                }
            ],
        },
    )
    monkeypatch.setattr(sys, "argv", ["marketbrief", "render-videos", "--limit", "1"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["renders"][0]["sync_passed"] is True
    assert payload["renders"][0]["quality_passed"] is True
    assert payload["renders"][0]["template_id"] == "why_stock_moved"
    assert payload["last_ready_for_posting"] is True


def test_render_videos_command_forwards_renderer(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(
        "backend.cli.render_existing_videos",
        lambda limit, approved_only=False, template="news-studio", max_duration=75, captions=True, renderer=None: captured.update(
            {"renderer": renderer}
        )
        or {"eligible": 0, "videos": 0, "errors": 0},
    )
    monkeypatch.setattr(sys, "argv", ["marketbrief", "render-videos", "--renderer", "remotion"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"eligible": 0, "errors": 0, "videos": 0}
    assert captured["renderer"] == "remotion"


def test_weekday_autopilot_command_outputs_run_payload(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.run_weekday_autopilot",
        lambda **kwargs: {
            "skipped": False,
            "settings": kwargs,
            "selected_count": kwargs["video_limit"],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "marketbrief",
            "weekday-autopilot",
            "--video-limit",
            "2",
            "--renderer",
            "remotion",
            "--today",
            "2026-05-29",
            "--gemini-digests",
        ],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_count"] == 2
    assert payload["settings"]["renderer"] == "remotion"
    assert payload["settings"]["today"] == "2026-05-29"
    assert payload["settings"]["gemini_digests"] is True


def test_clean_generated_content_command_outputs_counts(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.clean_generated_content",
        lambda: {
            "video_rows": 1,
            "script_rows": 1,
            "script_audio_files": 2,
            "script_bundles": 1,
            "video_files": 2,
            "review_bundles": 1,
            "render_files": 3,
        },
    )
    monkeypatch.setattr(sys, "argv", ["marketbrief", "clean-generated-content"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["script_rows"] == 1
    assert payload["script_audio_files"] == 2


def test_collect_history_command_outputs_count(monkeypatch, capsys):
    monkeypatch.setattr("backend.cli.company_tickers", lambda limit: ["AAPL"][: limit or 1])
    monkeypatch.setattr("backend.cli.collect_history", lambda tickers, period: len(tickers))
    monkeypatch.setattr(
        sys,
        "argv",
        ["marketbrief", "collect-history", "--limit", "1", "--period", "6mo"],
    )

    main()

    assert capsys.readouterr().out == "Stored 1 historical price rows.\n"


def test_collect_research_command_for_top_events(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.collect_research_for_events",
        lambda limit, force=False, providers=None: {
            "eligible": limit,
            "researched": 1,
            "sources": 3,
            "errors": 0,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["marketbrief", "collect-research", "--limit", "2", "--providers", "all"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"eligible": 2, "errors": 0, "researched": 1, "sources": 3}


def test_prepare_script_manifest_command_for_event(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.prepare_script_manifest",
        lambda event_id: {"manifest_path": f"outputs/script_manifests/ADBE_2026-05-29_{event_id}/manifest.json"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["marketbrief", "prepare-script-manifest", "--event-id", "1"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest_path"].endswith("_1/manifest.json")


def test_generate_script_from_manifest_command_for_event(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.prepare_script_manifest",
        lambda event_id: {"manifest_path": f"outputs/script_manifests/ADBE_2026-05-29_{event_id}/manifest.json"},
    )
    monkeypatch.setattr(
        "backend.cli.generate_script_from_manifest_path",
        lambda manifest_path: {
            "event_id": 1,
            "provider": "local",
            "bundle_path": "outputs/scripts/ADBE_2026-05-29_1",
            "script_path": "outputs/scripts/ADBE_2026-05-29_1/script.json",
            "prompt_path": "outputs/scripts/ADBE_2026-05-29_1/prompt.json",
            "raw_response_path": "outputs/scripts/ADBE_2026-05-29_1/raw_response.json",
            "package": {"title": "ADBE"},
            "db_fields": {"title": "ADBE", "script": "script", "description": "desc", "tags": []},
        },
    )
    monkeypatch.setattr("backend.cli.insert_script", lambda event_id, payload: 12)
    monkeypatch.setattr(
        sys,
        "argv",
        ["marketbrief", "generate-script-from-manifest", "--event-id", "1"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["script_id"] == 12
    assert payload["script_path"].endswith("/script.json")


def test_prepare_video_story_command_for_event(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.prepare_event_story",
        lambda event_id, template="news-studio", max_duration=75: {
            "bundle_path": f"outputs/review/ADBE_2026-05-29_{event_id}"
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["marketbrief", "prepare-video-story", "--event-id", "1"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["bundle_path"] == "outputs/review/ADBE_2026-05-29_1"


def test_prepare_video_story_command_for_top_events(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.cli.prepare_top_events",
        lambda limit, template="news-studio", max_duration=75: {
            "eligible": limit,
            "prepared": limit,
            "errors": 0,
        },
    )
    monkeypatch.setattr(sys, "argv", ["marketbrief", "prepare-video-story", "--limit", "2"])

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"eligible": 2, "errors": 0, "prepared": 2}
