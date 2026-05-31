from pathlib import Path

from models.database import connect, init_db, project_status, upsert_companies, upsert_prices, upsert_video


def test_init_db_and_upserts(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))

    init_db()
    upsert_companies(
        [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "sector": "Information Technology",
                "industry": "Consumer Electronics",
                "market_cap": 1_000_000_000_000,
            }
        ]
    )
    upsert_prices(
        [
            {
                "ticker": "AAPL",
                "date": "2026-05-29",
                "open": 100,
                "high": 110,
                "low": 95,
                "close": 108,
                "volume": 10_000,
                "average_volume": 4_000,
                "current_price": 108,
                "change_percent": 8,
                "market_cap": 1_000_000_000_000,
            }
        ]
    )

    with connect(db) as conn:
        company_count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        price_count = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]

    assert company_count == 1
    assert price_count == 1


def test_project_status_reports_counts(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    monkeypatch.setenv("EXA_API_KEY", "exa-key")

    init_db()
    status = project_status()

    assert status["db_path"] == str(db)
    assert status["counts"]["companies"] == 0
    assert status["latest_price_date"] is None
    assert status["ai_provider"] == "gemini"
    assert status["tts_provider"] == "gemini"
    assert status["gemini_configured"] is True
    assert status["exa_configured"] is True
    assert status["web_search_provider"] == "exa"


def test_upsert_video_keeps_one_row_per_script(tmp_path: Path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    init_db()

    with connect(db) as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'AAPL', 'story_candidate', '2026-05-29', 80, 'reason')"
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'script')")

    upsert_video(1, "videos/first.mp4")
    upsert_video(1, "videos/second.mp4")

    rows = query_videos(db)
    assert len(rows) == 1
    assert rows[0]["video_path"] == "videos/second.mp4"


def query_videos(db: Path):
    with connect(db) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM videos")]
