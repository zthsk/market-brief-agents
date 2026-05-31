from pathlib import Path

from models.database import connect, init_db, query
from services.generated_cleanup import clean_generated_content


def test_clean_generated_content_preserves_brand_and_market_data(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "data/market_brief_agents.db"))
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe')")
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'reason')
            """
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'script')")
        conn.execute(
            """
            INSERT INTO assets (event_id, asset_type, file_path)
            VALUES (1, 'chart', 'storage/assets/1/chart.png')
            """
        )
        conn.execute(
            "INSERT INTO videos (id, script_id, video_path) VALUES (1, 1, 'videos/ADBE_2026-05-29_1.mp4')"
        )

    _write("storage/assets/1/chart.png")
    _write("storage/audio/ADBE_2026-05-29_1.wav")
    _write("storage/audio/ADBE_2026-05-29_1_scene.wav")
    _write("storage/audio/legacy_script_1.wav")
    _write("videos/ADBE_2026-05-29_1.mp4")
    _write("videos/.gitkeep")
    _write("outputs/scripts/ADBE_2026-05-29_1/script.json")
    _write("outputs/review/ADBE_2026-05-29_1/video.mp4")
    _write("storage/render/temp.mp4")
    _write("assets/brand/outro.mp4")

    counts = clean_generated_content(tmp_path)

    assert counts["script_rows"] == 1
    assert counts["video_rows"] == 1
    assert counts["asset_rows"] == 1
    assert counts["asset_files"] == 1
    assert counts["script_audio_files"] == 3
    assert counts["script_bundles"] == 1
    assert counts["video_files"] == 1
    assert query("SELECT COUNT(*) AS count FROM companies")[0]["count"] == 1
    assert query("SELECT COUNT(*) AS count FROM events")[0]["count"] == 1
    assert query("SELECT COUNT(*) AS count FROM assets")[0]["count"] == 0
    assert not (tmp_path / "storage/assets/1").exists()
    assert not (tmp_path / "storage/audio/ADBE_2026-05-29_1.wav").exists()
    assert not (tmp_path / "videos/ADBE_2026-05-29_1.mp4").exists()
    assert not (tmp_path / "outputs/scripts/ADBE_2026-05-29_1").exists()
    assert not (tmp_path / "outputs/review/ADBE_2026-05-29_1").exists()
    assert (tmp_path / "assets/brand/outro.mp4").exists()


def _write(path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
