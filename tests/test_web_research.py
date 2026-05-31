import json
import os

from models.database import connect
from models.database import init_db, query
from pathlib import Path

from services.web_research import (
    classify_source,
    approve_research_bundle,
    collect_event_research,
    _clean_text,
    _press_release_allowed,
    _parse_google_news_rss,
    _result_allowed,
    research_bundle_detail,
    research_bundle_path,
    research_bundle_summaries,
    research_bundles,
    research_for_event,
    research_ready_for_event,
)


def test_collect_event_research_stores_exa_results(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    init_db()

    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move')"
        )

    monkeypatch.setattr(
        "services.web_research._exa_search_payload",
        lambda search_query, limit=8: {"results": [
            {
                "title": "ADBE Adobe AI story",
                "url": "https://www.reuters.com/markets/companies/adbe",
                "author": "Reuters",
                "publishedDate": "2026-05-29",
                "highlights": ["Adobe AI highlight"],
            }
        ]},
    )

    event = query("SELECT * FROM events WHERE id = 1")[0]
    assert collect_event_research(event) == 1
    assert research_for_event(1)[0]["highlights"] == ["Adobe AI highlight"]
    bundle = research_bundle_path(event)
    assert (bundle / "raw_response.json").exists()
    assert (bundle / "request.json").exists()
    assert (bundle / "review_results.json").exists()
    assert (bundle / "review.md").exists()
    assert research_ready_for_event(event) is False

    manifest = approve_research_bundle(bundle)

    assert manifest["ready_for_script_generation"] is True
    assert research_ready_for_event(event) is True


def test_collect_event_research_filters_excluded_and_irrelevant_results(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    init_db()

    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move')"
        )

    monkeypatch.setattr(
        "services.web_research._exa_search_payload",
        lambda search_query, limit=8: {"results": [
            {"title": "ADBE rumor", "url": "https://bitrss.com/adbe", "highlights": ["ADBE"]},
            {"title": "Other company", "url": "https://example.com/other", "highlights": ["MSFT"]},
            {
                "title": "ADBE real news",
                "url": "https://finance.yahoo.com/news/adbe",
                "source": "Yahoo Finance",
                "highlights": ["ADBE AI"],
            },
        ]},
    )

    event = query("SELECT * FROM events WHERE id = 1")[0]
    assert collect_event_research(event) == 1
    assert research_for_event(1)[0]["title"] == "ADBE real news"
    review = Path("outputs/research/ADBE_2026-05-29_1/review_results.json").read_text(
        encoding="utf-8"
    )
    assert "excluded domain" in review
    assert "ticker/company not found" in review


def test_source_classification_tiers():
    event = {"id": 1, "ticker": "ADBE", "company": "Adobe", "event_date": "2026-05-29"}

    assert classify_source(event, {"url": "https://www.sec.gov/Archives/adbe", "title": "ADBE 8-K"})[
        "tier"
    ] == 1
    assert (
        classify_source(
            event,
            {
                "url": "https://news.google.com/rss/articles/1",
                "source": "Business Wire",
                "title": "Adobe Reports Quarterly Results",
            },
        )["tier"]
        == 1
    )
    assert (
        classify_source(
            event,
            {
                "url": "https://www.news.adobe.com/news/adobe-delivers-record-q1-results",
                "source": "Adobe Newsroom",
                "title": "Adobe Delivers Record Q1 Results",
            },
        )["tier"]
        == 1
    )
    assert (
        classify_source(event, {"url": "https://www.reuters.com/markets/adbe", "title": "ADBE"})[
            "tier"
        ]
        == 2
    )
    assert (
        classify_source(event, {"url": "https://seekingalpha.com/article/adbe", "title": "ADBE"})[
            "tier"
        ]
        == 3
    )
    assert (
        classify_source(event, {"url": "https://minichart.com/news/adbe", "title": "ADBE"})[
            "tier"
        ]
        == 4
    )


def test_article_level_source_policy_for_business_and_mainstream_sources():
    event = {"id": 1, "ticker": "ADBE", "company": "Adobe", "event_date": "2026-05-29"}

    wire = classify_source(
        event,
        {
            "url": "https://www.businesswire.com/news/home/adobe-results",
            "source": "Business Wire",
            "title": "Adobe Reports Quarterly Results",
            "highlights": ["Adobe reports results and announces guidance."],
        },
    )
    ambiguous_wire = classify_source(
        event,
        {
            "url": "https://www.businesswire.com/news/home/market-roundup",
            "source": "Business Wire",
            "title": "Market Roundup Mentions Adobe",
            "highlights": ["Adobe shares moved in a roundup."],
        },
    )
    fox_business = classify_source(
        event,
        {"url": "https://www.foxbusiness.com/markets/adbe-stock", "title": "ADBE stock moves"},
    )
    fox_news = classify_source(
        event,
        {"url": "https://www.foxnews.com/us/adbe", "title": "ADBE stock moves"},
    )
    cnn_business = classify_source(
        event,
        {"url": "https://www.cnn.com/business/adbe", "source": "CNN Business", "title": "Adobe trend"},
    )
    business_insider = classify_source(
        event,
        {"url": "https://www.businessinsider.com/adobe-ai", "title": "Adobe AI trend"},
    )

    assert wire["tier"] == 1
    assert wire["is_official_company_release"] is True
    assert wire["claim_use_policy"] == "hard_facts_and_official_claims"
    assert ambiguous_wire["tier"] == 3
    assert ambiguous_wire["requires_confirmation"] is True
    assert fox_business["tier"] == 2
    assert fox_news["tier"] == 4
    assert cnn_business["tier"] == 3
    assert business_insider["tier"] == 3
    assert cnn_business["claim_use_policy"] == "context_only_requires_confirmation"


def test_result_allowed_rejects_tier_4_sources():
    event = {"id": 1, "ticker": "ADBE", "company": "Adobe", "event_date": "2026-05-29"}

    allowed, reason = _result_allowed(
        event,
        {
            "title": "Adobe Reports Quarterly Results",
            "url": "https://minichart.com/news/adbe",
            "source": "Minichart",
            "highlights": ["Adobe reported revenue."],
        },
    )

    assert allowed is False
    assert "tier 4 discovery source" in reason


def test_press_release_allowed_accepts_tier_1_and_rejects_tier_4():
    event = {"id": 1, "ticker": "ADBE", "company": "Adobe", "event_date": "2026-05-29"}

    accepted, accepted_reason = _press_release_allowed(
        event,
        {
            "title": "Adobe Reports Quarterly Results",
            "url": "https://news.google.com/rss/articles/1",
            "source": "Business Wire",
            "publishedDate": "Fri, 29 May 2026 10:00:00 GMT",
            "highlights": ["Adobe reported quarterly results."],
        },
    )
    rejected, rejected_reason = _press_release_allowed(
        event,
        {
            "title": "Adobe Reports Quarterly Results",
            "url": "https://minichart.com/news/adbe",
            "source": "Minichart",
            "publishedDate": "Fri, 29 May 2026 10:00:00 GMT",
            "highlights": ["Adobe reported quarterly results."],
        },
    )

    assert accepted is True
    assert "tier 1" in accepted_reason
    assert rejected is False
    assert "tier 4 discovery source" in rejected_reason


def test_collect_event_research_skips_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    os.environ.pop("EXA_API_KEY", None)
    init_db()

    assert collect_event_research({"id": 1, "ticker": "ADBE", "reason": ""}) == 0


def test_collect_event_research_stores_google_news_and_press_release(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()

    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe')")
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move')"
        )

    def fake_google_payload(search_query, limit=8):
        return {
            "results": [
                {
                    "title": "Adobe announces AI product update",
                    "url": f"https://news.example.com/{search_query[:4]}",
                    "source": "Business Wire",
                    "publishedDate": "Fri, 29 May 2026 10:00:00 GMT",
                    "highlights": ["Adobe announced a product update."],
                }
            ],
            "_request": {"q": search_query, "limit": limit},
        }

    monkeypatch.setattr("services.web_research._google_news_payload", fake_google_payload)

    event = query("SELECT * FROM events WHERE id = 1")[0]
    assert collect_event_research(event, providers=["google_news", "press_releases"], force=True) == 2

    rows = research_for_event(1)
    assert {row["provider"] for row in rows} == {"google_news", "company_press_release"}
    bundle = research_bundle_path(event)
    manifest = (bundle / "manifest.json").read_text(encoding="utf-8")
    assert "google_news" in manifest
    assert "press_releases" in manifest
    assert (bundle / "google_news_review_results.json").exists()
    assert (bundle / "press_releases_review_results.json").exists()


def test_collect_event_research_runs_multiple_exa_queries_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    init_db()

    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe')")
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move')"
        )

    queries = []

    def fake_exa_payload(search_query, limit=8):
        queries.append(search_query)
        return {
            "results": [
                {
                    "title": "Adobe Reports Quarterly Results",
                    "url": "https://www.businesswire.com/news/home/adobe-results?utm_source=x",
                    "source": "Business Wire",
                    "publishedDate": "2026-05-29",
                    "highlights": [f"Adobe announced results from {search_query[:10]}"],
                }
            ],
            "_request": {"query": search_query},
        }

    monkeypatch.setattr("services.web_research._exa_search_payload", fake_exa_payload)

    event = query("SELECT * FROM events WHERE id = 1")[0]
    assert collect_event_research(event) == 1
    assert len(queries) == 4
    rows = research_for_event(1)
    assert len(rows) == 1
    assert rows[0]["source_tier"] == 1
    assert rows[0]["is_official_company_release"] is True


def test_exa_research_stops_after_core_queries_when_enough_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    init_db()

    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe')")
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move')"
        )

    calls = []

    def fake_exa_payload(search_query, limit=8):
        calls.append(search_query)
        return {
            "results": [
                {
                    "title": f"Adobe ADBE Reuters story {index} {search_query}",
                    "url": f"https://www.reuters.com/markets/adbe/{len(calls)}-{index}",
                    "source": "Reuters",
                    "highlights": ["Adobe ADBE market story."],
                }
                for index in range(8)
            ],
            "_request": {"query": search_query},
        }

    monkeypatch.setattr("services.web_research._exa_search_payload", fake_exa_payload)

    event = query("SELECT * FROM events WHERE id = 1")[0]
    assert collect_event_research(event) == 16
    request = json.loads((research_bundle_path(event) / "exa_request.json").read_text())
    assert len(calls) == 2
    assert request["expanded"] is False


def test_exa_research_expands_when_core_sources_are_thin(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "exa")
    init_db()

    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe')")
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move')"
        )

    calls = []

    def fake_exa_payload(search_query, limit=8):
        calls.append(search_query)
        return {
            "results": [
                {
                    "title": f"Adobe ADBE Reuters story {len(calls)}",
                    "url": f"https://www.reuters.com/markets/adbe/{len(calls)}",
                    "source": "Reuters",
                    "highlights": ["Adobe ADBE market story."],
                }
            ],
            "_request": {"query": search_query},
        }

    monkeypatch.setattr("services.web_research._exa_search_payload", fake_exa_payload)

    event = query("SELECT * FROM events WHERE id = 1")[0]
    assert collect_event_research(event) == 4
    request = json.loads((research_bundle_path(event) / "exa_request.json").read_text())
    assert len(calls) == 4
    assert request["expanded"] is True


def test_parse_google_news_rss():
    xml = """
    <rss><channel>
      <item>
        <title>ADBE stock moves</title>
        <link>https://example.com/adbe</link>
        <pubDate>Fri, 29 May 2026 10:00:00 GMT</pubDate>
        <source>Example</source>
        <description>&lt;a href="https://example.com"&gt;Adobe shares&lt;/a&gt; moved after news.&amp;nbsp;</description>
      </item>
    </channel></rss>
    """

    results = _parse_google_news_rss(xml)

    assert results[0]["title"] == "ADBE stock moves"
    assert results[0]["source"] == "Example"
    assert results[0]["highlights"] == ["Adobe shares moved after news."]


def test_parse_google_news_rss_strips_nested_escaped_html_and_markdown_links():
    xml = """
    <rss><channel>
      <item>
        <title>&amp;lt;b&amp;gt;ADBE stock moves&amp;lt;/b&amp;gt;</title>
        <link>https://example.com/adbe</link>
        <source>&lt;i&gt;Example&lt;/i&gt;</source>
        <description>&amp;lt;a href="https://example.com"&amp;gt;Adobe shares&amp;lt;/a&amp;gt; [read more](https://example.com) moved.</description>
      </item>
    </channel></rss>
    """

    results = _parse_google_news_rss(xml)

    assert results[0]["title"] == "ADBE stock moves"
    assert results[0]["source"] == "Example"
    assert results[0]["highlights"] == ["Adobe shares read more moved."]


def test_parse_google_news_rss_strips_malformed_google_description_markup():
    xml = """
    <rss><channel>
      <item>
        <title>CDW Reports First Quarter 2026 Earnings - Business Wire</title>
        <link>https://news.google.com/rss/articles/example?oc=5</link>
        <source>Business Wire</source>
        <description>a href="https://news.google.com/rss/articles/example?oc=5" target="_blank"CDW Reports First Quarter 2026 Earnings/a&amp;nbsp;&amp;nbsp;font color="#6f6f6f"Business Wire/font</description>
      </item>
    </channel></rss>
    """

    results = _parse_google_news_rss(xml)

    assert results[0]["highlights"] == ["CDW Reports First Quarter 2026 Earnings Business Wire"]


def test_clean_text_removes_ellipsis_bracket_artifacts():
    assert _clean_text("Adobe shares moved after earnings [...]") == "Adobe shares moved after earnings"
    assert _clean_text("Guidance improved [ … ] into the close") == "Guidance improved into the close"


def test_research_bundle_summaries_do_not_load_review_details(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = Path("outputs/research/ADBE_2026-05-29_1")
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        """
        {
          "ticker": "ADBE",
          "date": "2026-05-29",
          "event_id": 1,
          "accepted_count": 12,
          "rejected_count": 4,
          "ready_for_script_generation": true
        }
        """,
        encoding="utf-8",
    )
    (bundle / "review_results.json").write_text("not json", encoding="utf-8")

    summaries = research_bundle_summaries()

    assert summaries[0]["accepted_count"] == 12
    assert summaries[0]["rejected_count"] == 4
    assert summaries[0]["ready_for_script_generation"] is True
    assert "accepted" not in summaries[0]


def test_research_bundle_detail_loads_selected_review_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = Path("outputs/research/ADBE_2026-05-29_1")
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"ticker":"ADBE","date":"2026-05-29","event_id":1}',
        encoding="utf-8",
    )
    (bundle / "review_results.json").write_text(
        """
        {
          "accepted": [
            {
              "title": "Adobe expands AI [...]",
              "url": "https://www.reuters.com/markets/adbe",
              "source": "Reuters",
              "highlights": ["Adobe story [...]"]
            }
          ],
          "rejected": []
        }
        """,
        encoding="utf-8",
    )

    detail = research_bundle_detail(bundle)

    assert detail is not None
    assert detail["accepted_count"] == 1
    assert detail["accepted"][0]["title"] == "Adobe expands AI"
    assert detail["accepted"][0]["highlights"] == ["Adobe story"]


def test_research_bundles_sanitizes_existing_review_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = Path("outputs/research/ADBE_2026-05-29_1")
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"ticker":"ADBE","date":"2026-05-29"}',
        encoding="utf-8",
    )
    (bundle / "review_results.json").write_text(
        """
        {
          "accepted": [
            {
              "title": "&lt;b&gt;Adobe&lt;/b&gt; [link](https://example.com)",
              "url": "https://www.reuters.com/markets/adbe",
              "source": "Reuters",
              "provider": "google_news",
              "review_reason": "&lt;i&gt;matched&lt;/i&gt;",
              "highlights": ["a href=\\"https://example.com\\" target=\\"_blank\\"Adobe shares/a&nbsp;&nbsp;font color=\\"#6f6f6f\\"Example/font moved"]
            }
          ],
          "rejected": []
        }
        """,
        encoding="utf-8",
    )

    bundles = research_bundles()

    item = bundles[0]["accepted"][0]
    assert item["title"] == "Adobe link"
    assert item["review_reason"] == "matched"
    assert item["highlights"] == ["Adobe shares Example moved"]
    assert item["source_quality"]["tier"] == 2


def test_research_bundles_backfills_company_name_for_tier_classification(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('APTV', 'Aptiv')")
    bundle = Path("outputs/research/APTV_2026-05-29_2")
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"ticker":"APTV","date":"2026-05-29"}',
        encoding="utf-8",
    )
    (bundle / "review_results.json").write_text(
        """
        {
          "accepted": [],
          "rejected": [
            {
              "title": "Aptiv Reports First Quarter 2026 Financial Results",
              "url": "https://news.google.com/rss/articles/example?oc=5",
              "source": "Business Wire",
              "provider": "press_releases",
              "review_reason": "old rejection reason",
              "highlights": ["Aptiv reported quarterly results."]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    item = research_bundles()[0]["rejected"][0]

    assert item["source_quality"]["tier"] == 1
    assert item["current_review_status"] == "accepted"
    assert item["current_review_reason"] == "tier 1 wire company source"
