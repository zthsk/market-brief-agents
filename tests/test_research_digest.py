import json
from pathlib import Path

from services.research_digest import (
    build_manifest_digest,
    build_manifest_digests_for_paths,
    build_research_digest,
    build_research_digests,
    digest_path,
    load_research_digest,
)


def test_local_research_digest_creates_reusable_text_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = Path("outputs/research/ADBE_2026-05-29_1")
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        '{"ticker":"ADBE","date":"2026-05-29","event_id":1,"accepted_count":1,"rejected_count":0}',
        encoding="utf-8",
    )
    (bundle / "review_results.json").write_text(
        json.dumps(
            {
                "accepted": [
                    {
                        "title": "Adobe reports AI momentum",
                        "url": "https://www.reuters.com/markets/adbe",
                        "source": "Reuters",
                        "source_tier": 2,
                        "highlights": [
                            "Adobe shares moved after investors focused on AI demand.",
                            "Management commentary gave traders a catalyst to watch.",
                        ],
                    }
                ],
                "rejected": [],
            }
        ),
        encoding="utf-8",
    )

    digest = build_research_digest(bundle, use_gemini=False)

    assert digest["provider"] == "local"
    assert digest["ticker"] == "ADBE"
    assert digest["source_counts"]["tier_2"] == 1
    assert digest["key_bullets"]
    assert "ADBE" in digest["text_post"]
    assert load_research_digest(bundle) == digest


def test_batch_research_digests_uses_one_gemini_payload_for_batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for ticker, event_id in (("ADBE", 1), ("MSFT", 2)):
        bundle = Path(f"outputs/research/{ticker}_2026-05-29_{event_id}")
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "ticker": ticker,
                    "date": "2026-05-29",
                    "event_id": event_id,
                    "accepted_count": 1,
                    "rejected_count": 0,
                }
            ),
            encoding="utf-8",
        )
        (bundle / "review_results.json").write_text(
            json.dumps(
                {
                    "accepted": [
                        {
                            "title": f"{ticker} stock moves",
                            "url": f"https://www.reuters.com/markets/{ticker.lower()}",
                            "source": "Reuters",
                            "source_tier": 2,
                            "highlights": [f"{ticker} has a clear market catalyst."],
                        }
                    ],
                    "rejected": [],
                }
            ),
            encoding="utf-8",
        )

    calls = []

    def fake_generate_json(prompt, system_instruction):
        calls.append((prompt, system_instruction))
        return {
            "digests": [
                {
                    "bundle_path": bundle["bundle_path"],
                    "key_bullets": [f"{bundle['ticker']} rewritten bullet"],
                    "why_it_matters": "The move has a clear catalyst.",
                    "caveats": ["Exact claims still need primary-source confirmation."],
                    "watch_items": ["Follow-through in volume."],
                    "text_post": f"{bundle['ticker']} has a source-backed move to watch.",
                    "confidence": "medium",
                }
                for bundle in prompt["bundles"]
            ]
        }

    monkeypatch.setattr("services.research_digest.gemini_configured", lambda: True)
    monkeypatch.setattr("services.research_digest.generate_json", fake_generate_json)

    result = build_research_digests(limit=2, use_gemini=True, batch_size=2)

    assert result["provider"] == "gemini"
    assert result["digests"] == 2
    assert len(calls) == 1
    for path in result["paths"]:
        digest = json.loads(Path(path).read_text(encoding="utf-8"))
        assert digest["provider"] == "gemini"
        assert digest["key_bullets"]
        assert digest_path(Path(path).parent).exists()


def test_manifest_digest_uses_cleaned_script_manifest_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bundle = Path("outputs/research/ADBE_2026-05-29_1")
    manifest_path = Path("outputs/script_manifests/ADBE_2026-05-29_1/manifest.json")
    manifest_path.parent.mkdir(parents=True)
    bundle.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "event": {"id": 1, "ticker": "ADBE", "date": "2026-05-29"},
                "research_review": {"bundle_path": str(bundle)},
                "approved_research": {
                    "exa": [
                        {
                            "title": "Adobe source",
                            "source": "Reuters",
                            "url": "https://www.reuters.com/adbe",
                            "source_tier": 2,
                            "highlights": ["Adobe cleaned highlight"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    digest = build_manifest_digest(manifest_path, use_gemini=False)

    assert digest["digest_source"] == "cleaned_script_manifest"
    assert digest["manifest_path"] == str(manifest_path)
    assert digest["bundle_path"] == str(bundle)
    assert digest["source_counts"]["tier_2"] == 1
    assert digest_path(bundle).exists()


def test_batch_manifest_digests_uses_one_gemini_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = []
    for ticker, event_id in (("ADBE", 1), ("MSFT", 2)):
        bundle = Path(f"outputs/research/{ticker}_2026-05-29_{event_id}")
        manifest_path = Path(f"outputs/script_manifests/{ticker}_2026-05-29_{event_id}/manifest.json")
        bundle.mkdir(parents=True)
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "event": {"id": event_id, "ticker": ticker, "date": "2026-05-29"},
                    "research_review": {"bundle_path": str(bundle)},
                    "approved_research": {
                        "exa": [
                            {
                                "title": f"{ticker} source",
                                "source": "Reuters",
                                "url": f"https://www.reuters.com/{ticker.lower()}",
                                "source_tier": 2,
                                "highlights": [f"{ticker} cleaned highlight"],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        paths.append(manifest_path)

    calls = []

    def fake_generate_json(prompt, system_instruction):
        calls.append(prompt)
        return {
            "digests": [
                {
                    "manifest_path": manifest["manifest_path"],
                    "key_bullets": ["rewritten bullet"],
                    "why_it_matters": "It matters.",
                    "caveats": ["Caveat."],
                    "watch_items": ["Watch volume."],
                    "text_post": "Post text.",
                    "confidence": "medium",
                }
                for manifest in prompt["manifests"]
            ]
        }

    monkeypatch.setattr("services.research_digest.gemini_configured", lambda: True)
    monkeypatch.setattr("services.research_digest.generate_json", fake_generate_json)

    result = build_manifest_digests_for_paths(paths, use_gemini=True, batch_size=200)

    assert result["digests"] == 2
    assert result["provider"] == "gemini"
    assert len(calls) == 1
