import json
from pathlib import Path

from media_engine.script_manifest import normalize_script_manifest_file, prepare_script_manifest
from media_engine.script_schema import load_script_manifest
from models.database import connect, init_db
from services.web_research import approve_research_bundle, research_bundle_path


def test_prepare_script_manifest_requires_approved_research(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    init_db()

    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe')")
        conn.execute(
            """
            INSERT INTO daily_prices (ticker, date, close, current_price, change_percent, volume)
            VALUES ('ADBE', '2026-05-29', 256.79, 256.79, 6.4, 1000000)
            """
        )
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'AI move', '{"impact":"sentiment"}')
            """
        )
        conn.execute(
            """
            INSERT INTO research_sources (
                event_id, ticker, provider, title, url, source, published_at, highlights_json, metadata_json
            )
            VALUES (
                1, 'ADBE', 'google_news', 'Adobe AI story', 'https://example.com/adbe',
                'Adobe Newsroom', '2026-05-29', '["Adobe source highlight [...] - with context. - still readable"]',
                '{"source_tier":1,"source_tier_label":"primary_official","claim_use_policy":"hard_facts_and_official_claims","is_official_company_release":false,"requires_confirmation":false,"classification_reason":"company investor relations or newsroom"}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_sources (
                event_id, ticker, provider, title, url, source, published_at, highlights_json, metadata_json
            )
            VALUES (
                1, 'ADBE', 'google_news', 'Adobe business context',
                'https://www.businessinsider.com/adobe-ai', 'Business Insider', '2026-05-29',
                '["Adobe business trend context"]',
                '{"source_tier":3,"source_tier_label":"market_commentary","claim_use_policy":"context_only_requires_confirmation","is_official_company_release":false,"requires_confirmation":true,"classification_reason":"business/mainstream context source"}'
            )
            """
        )

    event = {"id": 1, "ticker": "ADBE", "event_date": "2026-05-29"}
    bundle = research_bundle_path(event)
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(
        json.dumps({"ready_for_script_generation": False}), encoding="utf-8"
    )
    (bundle / "review_results.json").write_text(
        json.dumps(
            {
                "accepted": [],
                "rejected": [
                    {
                        "title": "Adobe low-confidence recap",
                        "url": "https://minichart.com/news/adbe",
                        "source": "Minichart",
                        "provider": "google_news",
                        "highlights": ["Adobe chatter appeared on a low-confidence site."],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    blocked = prepare_script_manifest(1)
    assert blocked["manifest"]["ready_for_gemini_script"] is False

    approve_research_bundle(bundle)
    result = prepare_script_manifest(1)
    manifest = result["manifest"]

    assert manifest["ready_for_gemini_script"] is True
    assert manifest["event"]["company"] == "Adobe"
    assert manifest["approved_research"]["google_news"][0]["source_id"] == "S1"
    assert manifest["approved_research"]["google_news"][0]["highlights"][0] == "Adobe source highlight with context. still readable"
    assert manifest["approved_research"]["google_news"][0]["source_quality"]["quality"] == "official"
    assert manifest["approved_research"]["google_news"][0]["source_quality"]["tier"] == 1
    assert len(manifest["approved_research"]["google_news"]) == 2
    assert manifest["citable_sources"][0]["source_id"] == "S1"
    assert manifest["citable_sources"][0]["claim_use_policy"] == "hard_facts_and_official_claims"
    assert manifest["context_sources"][0]["source_id"] == "S2"
    assert manifest["context_sources"][0]["requires_confirmation"] is True
    assert manifest["discovery_sources"][0]["source_quality"]["tier"] == 4
    assert manifest["rejected_sources"][0]["source_quality"]["tier"] == 4
    assert all(
        int(source["source_quality"]["tier"]) < 4
        for source in [*manifest["citable_sources"], *manifest["context_sources"]]
    )
    assert manifest["discovery_signals"][0]["discovery_id"] == "D1"
    assert manifest["discovery_signals"][0]["source_quality"]["tier"] == 4
    assert "Do not cite" in manifest["discovery_signals"][0]["usage_policy"]
    assert any(
        "Never cite discovery_signals" in item
        for item in manifest["gemini_script_request"]["constraints"]
    )
    assert Path(result["manifest_path"]).exists()
    loaded = load_script_manifest(result["manifest_path"])
    assert loaded.event.ticker == "ADBE"
    assert loaded.sources[0].source_id == "S1"
    assert loaded.discovery_signals[0].discovery_id == "D1"


def test_normalize_script_manifest_file_cleans_existing_nested_highlights(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "approved_research": {
                    "exa": [
                        {
                            "highlights": [
                                "Adobe revenue accelerated [...] - and margins improved. - Watch guidance [ … ] -"
                            ],
                            "source_tier": 2,
                            "claim_use_policy": "hard_facts_prefer_tier_1_for_exact_claims",
                        }
                    ]
                },
                "citable_sources": [
                    {
                        "title": "Adobe update [...] -",
                        "highlights": ["Shares moved after results. - Analyst reaction followed."],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = normalize_script_manifest_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result["changed"] is True
    assert result["string_replacements"] == 3
    assert manifest["approved_research"]["exa"][0]["highlights"][0] == (
        "Adobe revenue accelerated and margins improved. Watch guidance"
    )
    assert manifest["approved_research"]["exa"][0]["source_tier"] == 2
    assert manifest["approved_research"]["exa"][0]["claim_use_policy"] == (
        "hard_facts_prefer_tier_1_for_exact_claims"
    )
    assert manifest["citable_sources"][0]["title"] == "Adobe update"
    assert manifest["citable_sources"][0]["highlights"][0] == (
        "Shares moved after results. Analyst reaction followed."
    )
