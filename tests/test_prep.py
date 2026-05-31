from pathlib import Path

from media_engine.prep import prepare_event_story, prepare_top_events, update_prepared_story
from models.database import connect, init_db


def test_prepare_event_story_writes_automation_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))
    _seed_event()

    result = prepare_event_story(1)

    bundle = Path(result["bundle_path"])
    assert (bundle / "story.json").exists()
    assert (bundle / "scenes.json").exists()
    assert (bundle / "captions.srt").exists()
    assert (bundle / "thumbnail.png").exists()
    assert (bundle / "manifest.json").exists()
    assert result["manifest"]["automation_stage"] == "story_prepared"
    assert result["manifest"]["needs_tts"] is True
    assert result["manifest"]["ready_for_posting"] is False


def test_prepare_top_events_does_not_create_scripts_or_videos(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))
    _seed_event()

    result = prepare_top_events(limit=1)

    assert result == {"eligible": 1, "prepared": 1, "errors": 0}
    with connect() as conn:
        scripts = conn.execute("SELECT COUNT(*) AS count FROM scripts").fetchone()["count"]
        videos = conn.execute("SELECT COUNT(*) AS count FROM videos").fetchone()["count"]
    assert scripts == 0
    assert videos == 0


def test_update_prepared_story_refreshes_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))
    _seed_event()
    result = prepare_event_story(1)

    updated = update_prepared_story(
        result["bundle_path"],
        hook="ADBE has a cleaner story today",
        takeaway="Watch earnings before trusting the move",
    )

    story_text = (Path(updated["bundle_path"]) / "story.json").read_text(encoding="utf-8")
    manifest_text = (Path(updated["bundle_path"]) / "manifest.json").read_text(encoding="utf-8")
    assert "ADBE has a cleaner story today" in story_text
    assert "Watch earnings before trusting the move" in story_text
    assert '"status": "edited"' in manifest_text


def _seed_event() -> None:
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe Inc.')")
        conn.execute(
            """
            INSERT INTO daily_prices (ticker, date, close, current_price, change_percent)
            VALUES ('ADBE', '2026-05-29', 256.79, 256.79, 6.4)
            """
        )
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'Burry stake', '{}')
            """
        )
