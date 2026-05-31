from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from jobs import daily_refresh
from models.database import (
    connect,
    execute,
    init_db,
    query,
    upsert_companies,
    upsert_prices,
    upsert_research_sources,
    upsert_universe_memberships,
)
from services.company_universe import collect_yahoo_screener_companies
from services.market_calendar import is_nyse_trading_day


def test_nyse_calendar_guard_skips_weekends():
    assert is_nyse_trading_day(date(2026, 5, 30)) is False


def test_nyse_calendar_guard_uses_calendar_for_holidays(monkeypatch):
    class FakeCalendar:
        def schedule(self, start_date, end_date):
            return pd.DataFrame()

    fake_module = SimpleNamespace(get_calendar=lambda name: FakeCalendar())
    monkeypatch.setitem(__import__("sys").modules, "pandas_market_calendars", fake_module)

    assert is_nyse_trading_day(date(2026, 5, 25)) is False


def test_nyse_calendar_guard_runs_on_trading_day(monkeypatch):
    class FakeCalendar:
        def schedule(self, start_date, end_date):
            return pd.DataFrame({"market_open": ["2026-05-29"]})

    fake_module = SimpleNamespace(get_calendar=lambda name: FakeCalendar())
    monkeypatch.setitem(__import__("sys").modules, "pandas_market_calendars", fake_module)

    assert is_nyse_trading_day(date(2026, 5, 29)) is True


def test_collect_yahoo_screener_companies_dedupes_and_stores_memberships(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()

    def fake_screen(query_name, size=100):
        return {
            "quotes": [
                {
                    "symbol": "XYZ",
                    "shortName": "XYZ Corp",
                    "sector": "Technology",
                    "industry": "Software",
                    "marketCap": 123,
                },
                {"symbol": "AAPL", "shortName": "Apple Inc.", "marketCap": 456},
            ]
        }

    fake_yf = SimpleNamespace(screen=fake_screen)
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

    counts = collect_yahoo_screener_companies(size=2)

    assert counts["companies"] == 2
    assert query("SELECT COUNT(*) AS count FROM companies")[0]["count"] == 2
    assert query("SELECT COUNT(*) AS count FROM universe_memberships")[0]["count"] == 10
    universes = {
        row["universe"]
        for row in query("SELECT universe FROM universe_memberships WHERE ticker = 'XYZ'")
    }
    assert "yahoo_day_gainers" in universes
    assert "yahoo_most_shorted" in universes


def test_movers_report_shape_and_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    upsert_companies(
        [
            {"ticker": "AAA", "name": "AAA Inc.", "sector": None, "industry": None, "market_cap": None},
            {"ticker": "BBB", "name": "BBB Inc.", "sector": None, "industry": None, "market_cap": None},
            {"ticker": "XYZ", "name": "XYZ Inc.", "sector": None, "industry": None, "market_cap": None},
        ]
    )
    upsert_universe_memberships(
        [
            {"ticker": "AAA", "universe": "sp500", "source": "test", "seen_at": None},
            {"ticker": "BBB", "universe": "sp500", "source": "test", "seen_at": None},
            {"ticker": "XYZ", "universe": "yahoo_day_gainers", "source": "test", "seen_at": None},
        ]
    )
    upsert_prices(
        [
            _price("AAA", 10, 5, 100, 100),
            _price("BBB", 20, -4, 300, 100),
            _price("XYZ", 30, 2, 500, 100),
        ]
    )

    report = daily_refresh.movers_report(limit=2)

    assert report["latest_date"] == "2026-05-29"
    assert report["coverage"]["sp500_price_tickers"] == 2
    assert report["coverage"]["extended_price_tickers"] == 1
    assert report["top_gainers"][0]["ticker"] == "AAA"
    assert report["top_losers"][0]["ticker"] == "BBB"
    assert report["top_unusual_volume"][0]["ticker"] == "XYZ"


def test_daily_market_refresh_collects_full_universe_and_creates_events(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()

    def fake_seed(use_sample_on_error=False):
        upsert_companies(
            [
                {"ticker": "AAA", "name": "AAA Inc.", "sector": None, "industry": None, "market_cap": None},
                {"ticker": "BBB", "name": "BBB Inc.", "sector": None, "industry": None, "market_cap": None},
            ]
        )
        upsert_universe_memberships(
            [
                {"ticker": "AAA", "universe": "sp500", "source": "test", "seen_at": None},
                {"ticker": "BBB", "universe": "sp500", "source": "test", "seen_at": None},
            ]
        )
        return 2

    def fake_yahoo(size=100):
        upsert_companies(
            [{"ticker": "XYZ", "name": "XYZ Inc.", "sector": None, "industry": None, "market_cap": None}]
        )
        upsert_universe_memberships(
            [{"ticker": "XYZ", "universe": "yahoo_day_gainers", "source": "test", "seen_at": None}]
        )
        return {"companies": 1, "memberships": 1}

    collected_tickers = []

    def fake_prices(tickers):
        collected_tickers.extend(tickers)
        return upsert_prices(
            [
                _price("AAA", 10, 6, 100, 100),
                _price("BBB", 20, -5, 300, 100),
                _price("XYZ", 30, 3, 600, 100),
            ]
        )

    monkeypatch.setattr(daily_refresh, "is_nyse_trading_day", lambda value: True)
    monkeypatch.setattr(daily_refresh, "seed_companies", fake_seed)
    monkeypatch.setattr(daily_refresh, "collect_yahoo_screener_companies", fake_yahoo)
    monkeypatch.setattr(daily_refresh, "collect_prices", fake_prices)
    monkeypatch.setattr(daily_refresh, "collect_news", lambda tickers: len(tickers))
    monkeypatch.setattr(daily_refresh, "collect_filings", lambda tickers: 0)
    monkeypatch.setattr(daily_refresh, "collect_event_research", lambda event, **kwargs: 1)

    result = daily_refresh.daily_market_refresh(
        extended_size=5,
        top_movers=3,
        research_limit=2,
        today=date(2026, 5, 29),
    )

    assert result["skipped"] is False
    assert set(collected_tickers) == {"AAA", "BBB", "XYZ"}
    assert result["report"]["coverage"]["latest_price_tickers"] == 3
    assert query("SELECT COUNT(*) AS count FROM events")[0]["count"] >= 2
    assert query("SELECT COUNT(*) AS count FROM scripts")[0]["count"] == 0
    assert query("SELECT COUNT(*) AS count FROM videos")[0]["count"] == 0


def test_daily_market_refresh_skips_non_trading_day(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.setattr(daily_refresh, "is_nyse_trading_day", lambda value: False)

    result = daily_refresh.daily_market_refresh(today=date(2026, 5, 30))

    assert result == {
        "date": "2026-05-30",
        "skipped": True,
        "reason": "not_nyse_trading_day",
    }


def test_auto_approval_only_marks_selected_research_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'AAA', 'daily_mover', '2026-05-29', 90, 'AAA moved')"
        )
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (2, 'BBB', 'daily_mover', '2026-05-29', 80, 'BBB moved')"
        )
    selected_bundle = Path("outputs/research/AAA_2026-05-29_1")
    other_bundle = Path("outputs/research/BBB_2026-05-29_2")
    selected_bundle.mkdir(parents=True)
    other_bundle.mkdir(parents=True)
    (selected_bundle / "manifest.json").write_text("{}", encoding="utf-8")
    (other_bundle / "manifest.json").write_text("{}", encoding="utf-8")

    result = daily_refresh.auto_approve_research_candidates([{"ticker": "AAA", "event_id": 1}])

    assert result["approved"] == 1
    selected_manifest = json.loads((selected_bundle / "manifest.json").read_text(encoding="utf-8"))
    other_manifest = json.loads((other_bundle / "manifest.json").read_text(encoding="utf-8"))
    assert selected_manifest["ready_for_script_generation"] is True
    assert selected_manifest["approval_mode"] == "auto_daily_research_candidate"
    assert "ready_for_script_generation" not in other_manifest


def test_candidate_digests_split_video_ready_from_batch_research_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'AAA', 'daily_mover', '2026-05-29', 90, 'AAA moved')"
        )
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (2, 'BBB', 'daily_mover', '2026-05-29', 80, 'BBB moved')"
        )
    manifest_a = Path("outputs/script_manifests/AAA_2026-05-29_1/manifest.json")
    manifest_b = Path("outputs/script_manifests/BBB_2026-05-29_2/manifest.json")
    manifest_a.parent.mkdir(parents=True)
    manifest_b.parent.mkdir(parents=True)
    manifest_a.write_text("{}", encoding="utf-8")
    manifest_b.write_text("{}", encoding="utf-8")

    calls = {"local": [], "video": [], "batch": []}

    def fake_batch(paths, *, use_gemini=False, batch_size=10, force=False):
        key = "batch" if use_gemini else "local"
        calls[key].append(list(paths))
        return {"digests": len(paths), "paths": [f"{path}/research_digest.json" for path in paths]}

    def fake_single(path, *, use_gemini=False, force=False):
        assert use_gemini is True
        calls["video"].append(str(path))
        return {"provider": "gemini", "bundle_path": f"outputs/research/{Path(path).parent.name}"}

    monkeypatch.setattr(daily_refresh, "build_manifest_digests_for_paths", fake_batch)
    monkeypatch.setattr(daily_refresh, "build_manifest_digest", fake_single)

    result = daily_refresh.build_candidate_digests(
        research_candidates=[{"ticker": "AAA", "event_id": 1}, {"ticker": "BBB", "event_id": 2}],
        video_candidates=[{"ticker": "AAA", "event_id": 1, "decision": "video_ready"}],
        manifest_paths_by_event_id={1: str(manifest_a), 2: str(manifest_b)},
        create_local_digests=True,
        gemini_digest_video_ready=True,
        gemini_digest_research_ready_batch=True,
        digest_batch_size=10,
    )

    assert result["research_ready_manifests"] == 2
    assert result["video_ready_manifests"] == 1
    assert calls["local"] == [[str(manifest_a), str(manifest_b)]]
    assert calls["video"] == [str(manifest_a)]
    assert calls["batch"] == [[str(manifest_b)]]


def test_balanced_selection_does_not_let_gainers_consume_limit():
    buckets = {
        "top_gainers": [_candidate(f"G{i}", 100 - i, "top_gainers", i) for i in range(1, 8)],
        "top_losers": [_candidate(f"L{i}", 90 - i, "top_losers", i) for i in range(1, 8)],
        "top_unusual_volume": [_candidate(f"U{i}", 80 - i, "top_unusual_volume", i) for i in range(1, 5)],
    }

    selected = daily_refresh.select_balanced_research_candidates(
        buckets,
        quotas={"top_gainers": 2, "top_losers": 2, "top_unusual_volume": 2},
        limit=6,
    )

    memberships = [item["primary_bucket"] for item in selected]
    assert memberships.count("top_gainers") == 2
    assert memberships.count("top_losers") == 2
    assert memberships.count("top_unusual_volume") == 2


def test_balanced_selection_dedupes_and_preserves_bucket_memberships():
    buckets = {
        "top_gainers": [_candidate("AAA", 90, "top_gainers", 1)],
        "most_actives": [_candidate("AAA", 88, "most_actives", 1)],
    }

    selected = daily_refresh.select_balanced_research_candidates(
        buckets,
        quotas={"top_gainers": 1, "most_actives": 1},
        limit=2,
    )

    assert len(selected) == 1
    assert selected[0]["ticker"] == "AAA"
    assert selected[0]["bucket_memberships"] == ["top_gainers", "most_actives"]


def test_balanced_selection_redistributes_unused_quota():
    buckets = {
        "top_gainers": [_candidate("AAA", 90, "top_gainers", 1)],
        "top_losers": [],
        "top_unusual_volume": [
            _candidate("BBB", 80, "top_unusual_volume", 1),
            _candidate("CCC", 79, "top_unusual_volume", 2),
        ],
    }

    selected = daily_refresh.select_balanced_research_candidates(
        buckets,
        quotas={"top_gainers": 1, "top_losers": 2, "top_unusual_volume": 1},
        limit=3,
    )

    assert [item["ticker"] for item in selected] == ["AAA", "BBB", "CCC"]


def test_video_ranking_caps_buckets_and_blocks_low_quality_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    candidates = []
    for ticker, bucket, tier in [
        ("AAA", "top_gainers", 1),
        ("BBB", "top_gainers", 2),
        ("CCC", "top_gainers", 2),
        ("DDD", "top_losers", 4),
    ]:
        event_id = _insert_event(ticker)
        _insert_research(event_id, ticker, tier)
        candidates.append(
            {
                **_candidate(ticker, 80, bucket, 1),
                "event_id": event_id,
                "volume_ratio": 3,
                "universes": "sp500",
            }
        )

    ranked = daily_refresh.rank_video_candidates(
        candidates,
        limit=4,
        min_video_score=70,
        bucket_caps={"top_gainers": 2, "top_losers": 2},
    )

    ready = [item for item in ranked if item["decision"] == "video_ready"]
    assert len([item for item in ready if item["primary_bucket"] == "top_gainers"]) == 2
    low_quality = next(item for item in ranked if item["ticker"] == "DDD")
    assert low_quality["decision"] != "video_ready"


def test_strong_event_without_catalyst_needs_review_or_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    event_id = _insert_event("AAA")
    candidate = {
        **_candidate("AAA", 95, "top_gainers", 1),
        "event_id": event_id,
        "volume_ratio": 4,
        "universes": "sp500",
    }

    ranked = daily_refresh.rank_video_candidates([candidate], limit=1, min_video_score=70, bucket_caps={})

    assert ranked[0]["decision"] in {"needs_manual_review", "skip_no_clear_catalyst"}


def test_tier12_catalyst_and_strong_move_can_be_video_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    event_id = _insert_event("AAA")
    _insert_research(event_id, "AAA", 1)
    candidate = {
        **_candidate("AAA", 90, "top_gainers", 1),
        "event_id": event_id,
        "volume_ratio": 4,
        "universes": "sp500,yahoo_most_actives",
    }

    ranked = daily_refresh.rank_video_candidates([candidate], limit=1, min_video_score=70, bucket_caps={})

    assert ranked[0]["decision"] == "video_ready"


def test_research_reuse_skips_existing_unless_forced(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    event_id = _insert_event("AAA")
    _insert_research(event_id, "AAA", 1)
    calls = []
    monkeypatch.setattr(
        daily_refresh,
        "collect_event_research",
        lambda event, **kwargs: calls.append(kwargs) or 1,
    )

    daily_refresh.collect_research_for_candidates([{"ticker": "AAA", "event_id": event_id}])
    daily_refresh.collect_research_for_candidates(
        [{"ticker": "AAA", "event_id": event_id}],
        force_research=True,
    )

    assert len(calls) == 1
    assert calls[0]["force"] is True


def test_research_refreshes_when_existing_has_no_tier12_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    event_id = _insert_event("AAA")
    _insert_research(event_id, "AAA", 4)
    calls = []
    monkeypatch.setattr(
        daily_refresh,
        "collect_event_research",
        lambda event, **kwargs: calls.append(kwargs) or 1,
    )

    daily_refresh.collect_research_for_candidates(
        [{"ticker": "AAA", "event_id": event_id}],
        force_if_no_tier1_or_tier2_sources=True,
    )

    assert len(calls) == 1
    assert calls[0]["force"] is True


def _price(ticker: str, close: float, change: float, volume: int, average_volume: int) -> dict:
    return {
        "ticker": ticker,
        "date": "2026-05-29",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "average_volume": average_volume,
        "current_price": close,
        "change_percent": change,
        "market_cap": None,
    }


def _candidate(ticker: str, score: int, bucket: str, rank: int) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "change_percent": score / 8,
        "volume_ratio": 1,
        "event_score": score,
        "rank_in_bucket": rank,
        "bucket": bucket,
        "primary_bucket": bucket,
        "bucket_memberships": [bucket],
        "universes": "",
    }


def _insert_event(ticker: str) -> int:
    execute(
        """
        INSERT INTO events (ticker, event_type, event_date, score, reason)
        VALUES (?, 'daily_mover', '2026-05-29', 80, 'test catalyst')
        """,
        (ticker,),
    )
    return query("SELECT id FROM events WHERE ticker = ?", (ticker,))[0]["id"]


def _insert_research(event_id: int, ticker: str, tier: int) -> None:
    upsert_research_sources(
        [
            {
                "event_id": event_id,
                "ticker": ticker,
                "provider": "exa",
                "title": f"{ticker} source",
                "url": f"https://example.com/{ticker}/{tier}",
                "source": "Example",
                "published_at": "2026-05-29",
                "highlights_json": "[]",
                "metadata_json": json.dumps({"source_tier": tier}),
            }
        ]
    )
