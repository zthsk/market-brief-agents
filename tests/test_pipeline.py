import json
from pathlib import Path

from jobs import pipeline
from models.database import connect, init_db, query, upsert_video
from services.video_renderer import _frame_duration, _frame_durations


def test_render_existing_videos_renders_scripts_without_video(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    init_db()

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'reason', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO scripts (id, event_id, title, script, status)
            VALUES (1, 1, 'ADBE move', 'script', 'approved')
            """
        )
        for asset_type in ("chart", "company", "headline", "summary"):
            conn.execute(
                """
                INSERT INTO assets (event_id, asset_type, file_path)
                VALUES (1, ?, ?)
                """,
                (asset_type, f"storage/assets/1/{asset_type}.png"),
            )

    def fake_render_video(script_row, assets, **kwargs):
        upsert_video(int(script_row["id"]), "videos/ADBE_2026-05-29_1.mp4")
        return "videos/ADBE_2026-05-29_1.mp4"

    monkeypatch.setattr(pipeline, "render_video", fake_render_video)

    result = pipeline.render_existing_videos(approved_only=True)

    assert result == {"eligible": 1, "videos": 1, "errors": 0}
    assert query("SELECT COUNT(*) AS count FROM videos")[0]["count"] == 1


def test_render_existing_videos_passes_renderer_choice(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    init_db()

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'reason', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO scripts (id, event_id, title, script, status)
            VALUES (1, 1, 'ADBE move', 'script', 'approved')
            """
        )

    captured = {}

    def fake_render_video(script_row, assets, **kwargs):
        captured.update(kwargs)
        upsert_video(int(script_row["id"]), "videos/ADBE_2026-05-29_1.mp4")
        return "videos/ADBE_2026-05-29_1.mp4"

    monkeypatch.setattr(pipeline, "render_video", fake_render_video)

    result = pipeline.render_existing_videos(approved_only=True, renderer="remotion")

    assert result["videos"] == 1
    assert captured["renderer"] == "remotion"


def test_render_script_video_ignores_existing_video(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    init_db()

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'reason', '{}')
            """
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'script')")
    upsert_video(1, "videos/ADBE_2026-05-29_1.mp4")

    result = pipeline.render_script_video(1)

    assert result == {"eligible": 0, "videos": 0, "errors": 0}


def test_render_script_video_can_force_remotion_rerender(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    init_db()

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'STG', 'story_candidate', '2026-05-29', 80, 'reason', '{}')
            """
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'script')")
    upsert_video(1, "videos/STG_old.mp4")
    captured = {}

    def fake_render_video(script_row, assets, **kwargs):
        captured.update(kwargs)
        upsert_video(int(script_row["id"]), "videos/STG_new.mp4")
        return "videos/STG_new.mp4"

    monkeypatch.setattr(pipeline, "render_video", fake_render_video)

    result = pipeline.render_script_video(1, renderer="remotion", force=True)

    assert result == {"eligible": 1, "videos": 1, "errors": 0}
    assert captured["renderer"] == "remotion"
    assert query("SELECT video_path FROM videos WHERE script_id = 1")[0]["video_path"] == "videos/STG_new.mp4"


def test_generate_for_events_waits_when_research_manifest_is_missing(
    tmp_path: Path, monkeypatch
):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    monkeypatch.chdir(tmp_path)
    init_db()

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'reason')
            """
        )

    monkeypatch.setattr(pipeline, "collect_event_research", lambda event: 0)
    monkeypatch.setattr(
        pipeline,
        "analyze_story",
        lambda *args: {
            "reason": "ADBE moved.",
            "impact": "Investors noticed.",
            "risk": "Details may change.",
            "what_to_watch": "Watch filings.",
        },
    )
    monkeypatch.setattr(pipeline, "generate_audio", lambda script_id, script: False)

    result = pipeline.generate_for_events(limit=1, skip_video=True)

    assert result["research_pending_review"] == 1
    assert result["scripts"] == 0
    assert query("SELECT COUNT(*) AS count FROM scripts")[0]["count"] == 0


def test_generate_for_events_waits_when_research_bundle_needs_review(
    tmp_path: Path, monkeypatch
):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    monkeypatch.chdir(tmp_path)
    init_db()

    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'reason')
            """
        )

    def create_unapproved_bundle(event):
        bundle = pipeline.research_bundle_path(event)
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text(
            json.dumps({"ready_for_script_generation": False}),
            encoding="utf-8",
        )
        return 1

    monkeypatch.setattr(pipeline, "collect_event_research", create_unapproved_bundle)

    result = pipeline.generate_for_events(limit=1, skip_video=True)

    assert result["research_pending_review"] == 1
    assert result["scripts"] == 0
    assert query("SELECT COUNT(*) AS count FROM scripts")[0]["count"] == 0


def test_frame_duration_tracks_audio_length():
    assert _frame_duration(5, 45) == 9
    assert _frame_duration(5, 10) == 4
    assert _frame_duration(5, None) == 12


def test_frame_durations_prioritize_chart_scene():
    durations = _frame_durations(4, 50)

    assert len(durations) == 4
    assert durations[1] > durations[0]
    assert round(sum(durations), 1) == 51.5
