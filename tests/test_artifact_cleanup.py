from pathlib import Path

from models.database import connect, init_db, query
from services.artifact_cleanup import (
    delete_event_audio_artifacts,
    delete_event_production_artifacts,
    delete_event_video_artifacts,
    delete_script_artifacts,
    delete_script_video_artifacts,
    event_production_artifact_summary,
    event_video_artifact_summary,
)


def test_delete_event_audio_artifacts_clears_files_and_audio_links(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    _insert_event_with_script()
    audio = Path("storage/audio/NOW_2026-05-29_1.wav")
    scene_audio = Path("storage/audio/NOW_2026-05-29_1_scene_01.wav")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    scene_audio.write_bytes(b"scene")
    with connect() as conn:
        conn.execute("UPDATE scripts SET audio_path = ? WHERE id = 1", (str(audio),))

    result = delete_event_audio_artifacts(1)

    assert result["files_removed"] == 2
    assert result["script_audio_rows_cleared"] == 1
    assert not audio.exists()
    assert not scene_audio.exists()
    assert query("SELECT audio_path FROM scripts WHERE id = 1")[0]["audio_path"] is None


def test_delete_script_artifacts_removes_stale_event_script_package(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    _insert_event_with_script()
    script_bundle = Path("outputs/scripts/NOW_2026-05-29_1")
    script_bundle.mkdir(parents=True)
    (script_bundle / "script.json").write_text("{}", encoding="utf-8")
    audio = Path("storage/audio/NOW_2026-05-29_1.wav")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    video = Path("videos/NOW_2026-05-29_1.mp4")
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    with connect() as conn:
        conn.execute("UPDATE scripts SET audio_path = ? WHERE id = 1", (str(audio),))
        conn.execute("INSERT INTO videos (id, script_id, video_path) VALUES (1, 1, ?)", (str(video),))

    result = delete_script_artifacts(1)

    assert result["script_rows_deleted"] == 1
    assert result["video_rows_deleted"] == 1
    assert not script_bundle.exists()
    assert not audio.exists()
    assert not video.exists()
    assert query("SELECT * FROM scripts WHERE id = 1") == []
    assert query("SELECT * FROM videos WHERE script_id = 1") == []


def test_delete_event_video_artifacts_keeps_script_audio_assets_and_research(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    _insert_event_with_script()
    audio = Path("storage/audio/NOW_2026-05-29_1.wav")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    video = Path("videos/NOW_2026-05-29_1.mp4")
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    render_file = Path("storage/render/NOW_2026-05-29_1_scene_0.mp4")
    render_file.parent.mkdir(parents=True)
    render_file.write_bytes(b"render")
    review = Path("outputs/review/NOW_2026-05-29_1")
    review.mkdir(parents=True)
    (review / "video.mp4").write_bytes(b"review")
    script_bundle = Path("outputs/scripts/NOW_2026-05-29_1")
    script_bundle.mkdir(parents=True)
    (script_bundle / "script.json").write_text("{}", encoding="utf-8")
    research = Path("outputs/research/NOW_2026-05-29_1/manifest.json")
    research.parent.mkdir(parents=True)
    research.write_text("{}", encoding="utf-8")
    asset = Path("storage/assets/1/chart.png")
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"asset")
    with connect() as conn:
        conn.execute("UPDATE scripts SET audio_path = ? WHERE id = 1", (str(audio),))
        conn.execute("INSERT INTO videos (id, script_id, video_path) VALUES (1, 1, ?)", (str(video),))
        conn.execute("INSERT INTO assets (event_id, asset_type, file_path) VALUES (1, 'chart', ?)", (str(asset),))

    summary = event_video_artifact_summary(1)
    result = delete_event_video_artifacts(1)

    assert str(video) in summary["paths"]
    assert str(review) in summary["paths"]
    assert result["video_rows_deleted"] == 1
    assert not video.exists()
    assert not render_file.exists()
    assert not review.exists()
    assert audio.exists()
    assert script_bundle.exists()
    assert research.exists()
    assert asset.exists()
    assert query("SELECT * FROM scripts WHERE id = 1")
    assert query("SELECT audio_path FROM scripts WHERE id = 1")[0]["audio_path"] == str(audio)
    assert query("SELECT * FROM videos WHERE script_id = 1") == []


def test_delete_script_video_artifacts_only_removes_selected_script_video(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    _insert_event_with_script()
    video = Path("videos/NOW_2026-05-29_1.mp4")
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    with connect() as conn:
        conn.execute("INSERT INTO videos (id, script_id, video_path) VALUES (1, 1, ?)", (str(video),))

    result = delete_script_video_artifacts(1)

    assert result["video_rows_deleted"] == 1
    assert not video.exists()
    assert query("SELECT * FROM scripts WHERE id = 1")
    assert query("SELECT * FROM videos WHERE script_id = 1") == []


def test_delete_event_production_artifacts_keeps_research(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    _insert_event_with_script()
    research = Path("outputs/research/NOW_2026-05-29_1/manifest.json")
    research.parent.mkdir(parents=True)
    research.write_text("{}", encoding="utf-8")
    asset = Path("storage/assets/1/chart.png")
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"asset")
    review = Path("outputs/review/NOW_2026-05-29_1")
    review.mkdir(parents=True)
    (review / "video.mp4").write_bytes(b"review")
    with connect() as conn:
        conn.execute("INSERT INTO assets (event_id, asset_type, file_path) VALUES (1, 'chart', ?)", (str(asset),))

    summary = event_production_artifact_summary(1)
    result = delete_event_production_artifacts(1)

    assert str(asset) in summary["paths"]
    assert result["script_rows_deleted"] == 1
    assert result["asset_rows_deleted"] == 1
    assert not asset.exists()
    assert not review.exists()
    assert research.exists()


def _insert_event_with_script() -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'NOW', 'story_candidate', '2026-05-29', 80, 'reason')"
        )
        conn.execute(
            "INSERT INTO scripts (id, event_id, title, script) VALUES (1, 1, 'NOW script', 'script text')"
        )
