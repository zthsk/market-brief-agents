from models.database import init_db, query, upsert_news, upsert_prices
from services.event_detector import EVENT_THRESHOLD, detect_events, score_story


def test_score_story_reaches_threshold_for_large_move_and_news():
    score, reasons = score_story(
        {
            "change_percent": 10.5,
            "volume": 1_000_000,
            "average_volume": 900_000,
            "market_cap": 50_000_000_000,
        },
        news_count=2,
        has_new_8k=False,
    )

    assert score >= EVENT_THRESHOLD
    assert "10.5% price move" in reasons
    assert "2 recent headlines" in reasons


def test_score_story_volume_spike_and_8k_components():
    score, reasons = score_story(
        {
            "change_percent": 5.2,
            "volume": 3_000_000,
            "average_volume": 1_000_000,
            "market_cap": 5_000_000_000,
        },
        news_count=0,
        has_new_8k=True,
    )

    assert score == 65
    assert "volume above 2x average" in reasons
    assert "new 8-K filing" in reasons


def test_detect_events_is_idempotent_for_same_trading_date(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    upsert_prices(
        [
            {
                "ticker": "AAPL",
                "date": "2026-05-29",
                "open": 100,
                "high": 112,
                "low": 99,
                "close": 111,
                "volume": 3_000_000,
                "average_volume": 1_000_000,
                "current_price": 111,
                "change_percent": 11,
                "market_cap": 50_000_000_000,
            }
        ]
    )
    upsert_news(
        [
            {
                "ticker": "AAPL",
                "published_at": "2026-05-29T10:00:00Z",
                "headline": "Apple makes news",
                "url": "https://example.com/1",
                "source": "Example",
                "summary": "",
            },
            {
                "ticker": "AAPL",
                "published_at": "2026-05-29T10:05:00Z",
                "headline": "Apple makes more news",
                "url": "https://example.com/2",
                "source": "Example",
                "summary": "",
            },
        ]
    )

    assert detect_events() == 1
    assert detect_events() == 1
    assert query("SELECT COUNT(*) AS count FROM events")[0]["count"] == 1
