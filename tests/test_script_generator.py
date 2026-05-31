import json

import pytest
from pydantic import ValidationError

from media_engine.script_schema import GeneratedScriptPackage
from media_engine.script_schema import ScriptManifest
from services import script_generator as sg
from services.script_generator import generate_script, generate_script_from_manifest
from services.story_analyzer import analyze_story


def test_fallback_script_shape(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    payload = generate_script(
        "AAPL",
        {
            "reason": "AAPL rose 6.0% after a major headline.",
            "impact": "The move drew investor attention.",
            "risk": "The initial story may change.",
            "what_to_watch": "Watch earnings and company commentary.",
        },
        {"score": 80},
    )

    assert payload["title"]
    assert "HOOK:" in payload["script"]
    assert "WHAT HAPPENED:" in payload["script"]
    assert "WHY IT MATTERS:" in payload["script"]
    assert "WHAT TO WATCH:" in payload["script"]
    assert "CTA:" in payload["script"]
    assert "AAPL" in payload["tags"]


def test_openai_analysis_error_falls_back(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def raise_openai_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("services.story_analyzer._openai_analysis", raise_openai_error)

    payload = analyze_story("AAPL", {"change_percent": 4.2}, [], [])

    assert payload["reason"].startswith("AAPL rose 4.2%")
    assert payload["what_to_watch"]


def test_openai_script_error_falls_back(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def raise_openai_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("services.script_generator._openai_script", raise_openai_error)

    payload = generate_script(
        "AAPL",
        {
            "reason": "AAPL rose 4.2%.",
            "impact": "The move drew attention.",
            "risk": "Details can change.",
            "what_to_watch": "Watch follow-up commentary.",
        },
        {"score": 70},
    )

    assert payload["title"]
    assert "HOOK:" in payload["script"]


def test_gemini_analysis_is_used_when_configured(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.story_analyzer.gemini_configured", lambda: True)
    monkeypatch.setattr(
        "services.story_analyzer.generate_json",
        lambda prompt, system_instruction: {
            "reason": "AAPL moved on a specific catalyst.",
            "impact": "The catalyst matters.",
            "risk": "The story can change.",
            "what_to_watch": "Watch filings.",
        },
    )

    payload = analyze_story("AAPL", {"change_percent": 3}, [], [])

    assert payload["reason"] == "AAPL moved on a specific catalyst."


def test_gemini_script_is_used_when_configured(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.script_generator.gemini_configured", lambda: True)
    monkeypatch.setattr(
        "services.script_generator.generate_json",
        lambda prompt, system_instruction: {
            "title": "AAPL AI catalyst",
            "script": "HOOK: Apple moved. WHAT HAPPENED: A catalyst emerged. WHY IT MATTERS: Expectations changed. WHAT TO WATCH: Watch filings. CTA: Follow Market Brief Agents.",
            "description": "AAPL recap.",
            "tags": ["AAPL", "market news"],
        },
    )

    payload = generate_script("AAPL", {"reason": "AAPL moved."}, {"score": 70})

    assert payload["title"] == "AAPL AI catalyst"
    assert "HOOK:" in payload["script"]


def test_normalize_payload_converts_section_dict_to_script():
    from services.script_generator import _normalize_payload

    payload = _normalize_payload(
        {
            "title": "AAPL move",
            "script": {
                "HOOK": "First.",
                "WHAT HAPPENED": "Second.",
                "WHY IT MATTERS": "Third.",
                "WHAT TO WATCH": "Fourth.",
                "CTA": "Fifth.",
            },
        },
        "AAPL",
        {},
    )

    assert payload["script"].startswith("HOOK: First.")
    assert "CTA: Fifth." in payload["script"]


def test_generate_script_from_manifest_writes_audit_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    manifest = _manifest()

    result = generate_script_from_manifest(manifest)

    assert result["event_id"] == 1
    assert result["db_fields"]["title"]
    assert result["package"]["video_metadata"]["ticker"] == "ADBE"
    assert result["package"]["scenes"][0]["type"] == "hook"
    assert 4 <= len(result["package"]["scenes"]) <= 8
    assert result["provider"] == "local"
    assert (tmp_path / "outputs/scripts/ADBE_2026-05-29_1/prompt.json").exists()
    assert (tmp_path / "outputs/scripts/ADBE_2026-05-29_1/raw_response.json").exists()
    prompt_json = json.loads(
        (tmp_path / "outputs/scripts/ADBE_2026-05-29_1/prompt.json").read_text(
            encoding="utf-8"
        )
    )
    assert prompt_json["discovery_signals"][0]["discovery_id"] == "D1"
    assert "citable_sources" in prompt_json
    assert "context_sources" in prompt_json
    assert "rejected_sources" in prompt_json
    assert any(
        "Tier 1 sources should be preferred" in item
        for item in prompt_json["requirements"]["source_policy"]
    )
    assert any(
        "Use attribution-only language" in item
        for item in prompt_json["requirements"]["source_policy"]
    )
    assert any(
        "Every scene must include highlights" in item
        for item in prompt_json["requirements"]["scene_card_bullet_rules"]
    )
    assert "Never put discovery_signals IDs in source_ids." in prompt_json["requirements"][
        "source_policy"
    ]
    script_json = json.loads(
        (tmp_path / "outputs/scripts/ADBE_2026-05-29_1/script.json").read_text(
            encoding="utf-8"
        )
    )
    assert script_json["package"]["scenes"][0]["importance"] == "high"
    assert script_json["package"]["scenes"][0]["confidence_level"]
    assert all(2 <= len(scene["highlights"]) <= 3 for scene in script_json["package"]["scenes"])
    assert script_json["db_fields"]["title"] == result["db_fields"]["title"]


def test_model_response_defaults_and_repair_are_audited(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.script_generator.gemini_configured", lambda: True)
    manifest = _manifest()
    payload = sg._fallback_manifest_payload(manifest)
    payload["scenes"][0]["on_screen_text"]["headline"] = "this headline is much too long"
    payload["scenes"][0].pop("confidence_level")
    payload["scenes"][0]["id"] = 99
    monkeypatch.setattr(
        "services.script_generator.generate_text",
        lambda prompt, **kwargs: f"```json\n{json.dumps(payload)}\n```",
    )

    result = generate_script_from_manifest(manifest)

    bundle = tmp_path / "outputs/scripts/ADBE_2026-05-29_1"
    assert result["provider"] == "gemini"
    assert (bundle / "raw_response.json").exists()
    assert (bundle / "repair_attempt.json").exists()
    script_json = json.loads((bundle / "script.json").read_text(encoding="utf-8"))
    assert len(script_json["package"]["scenes"][0]["on_screen_text"]["headline"].split()) == 5
    assert script_json["package"]["scenes"][0]["confidence_level"] == "medium"
    assert script_json["package"]["scenes"][0]["id"] == 1


def test_repair_splits_caveat_takeaway_into_risk_and_conclusion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.script_generator.gemini_configured", lambda: True)
    manifest = _manifest()
    payload = sg._fallback_manifest_payload(manifest)
    payload["scenes"] = [scene for scene in payload["scenes"] if scene["type"] != "risk"]
    payload["scenes"][-1]["type"] = "takeaway"
    payload["scenes"][-1][
        "narration"
    ] = "While execution concerns remain, the overall setup has shifted."
    monkeypatch.setattr(
        "services.script_generator.generate_text",
        lambda prompt, **kwargs: json.dumps(payload),
    )

    result = generate_script_from_manifest(manifest)

    scene_types = [scene["type"] for scene in result["package"]["scenes"]]
    assert result["provider"] == "gemini"
    assert "risk" in scene_types
    assert "conclusion" in scene_types
    assert scene_types.index("risk") < scene_types.index("conclusion")
    assert " The." not in result["db_fields"]["script"]


def test_invalid_model_output_writes_errors_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.script_generator.gemini_configured", lambda: True)
    manifest = _manifest()
    payload = sg._fallback_manifest_payload(manifest)
    payload["scenes"] = payload["scenes"][:3]
    monkeypatch.setattr(
        "services.script_generator.generate_text",
        lambda prompt, **kwargs: json.dumps(payload),
    )

    result = generate_script_from_manifest(manifest)

    bundle = tmp_path / "outputs/scripts/ADBE_2026-05-29_1"
    assert result["provider"] == "local"
    assert (bundle / "validation_errors.json").exists()
    assert (bundle / "repair_attempt.json").exists()
    assert (bundle / "raw_response.json").exists()


def test_gemini_manifest_request_uses_native_system_instruction(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.script_generator.gemini_configured", lambda: True)
    calls = []

    def fake_generate_text(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return json.dumps(sg._fallback_manifest_payload(_manifest()))

    monkeypatch.setattr("services.script_generator.generate_text", fake_generate_text)

    result = generate_script_from_manifest(_manifest())

    prompt, kwargs = calls[0]
    assert result["provider"] == "gemini"
    assert "system_instruction" not in prompt
    assert prompt["payload"]["task"].startswith("Create a highly engaging short-form")
    assert prompt["payload"]["event"]["ticker"] == "ADBE"
    assert prompt["payload"]["citable_sources"][0]["source_id"] == "S1"
    assert prompt["payload"]["discovery_signals"][0]["discovery_id"] == "D1"
    assert "confidence_rules" in prompt["payload"]["requirements"]
    assert prompt["payload"]["requirements"]["output_shape"]["scenes"][0]["confidence_level"] == "medium"
    assert len(prompt["payload"]["requirements"]["output_shape"]["scenes"][0]["highlights"]) == 3
    assert kwargs["system_instruction"] == sg.SCRIPT_SYSTEM_INSTRUCTION
    assert kwargs["response_mime_type"] == "application/json"


def test_extract_json_object_accepts_fenced_and_embedded_json():
    assert sg._extract_json_object('```json\n{"title":"A"}\n```') == {"title": "A"}
    assert sg._extract_json_object('prefix {"title":"B"} suffix') == {"title": "B"}


def test_generated_script_package_schema_rejects_invalid_scene_plan():
    payload = sg._fallback_manifest_payload(_manifest())
    payload["video_metadata"]["title"] = "A" * 81
    with pytest.raises(ValidationError):
        GeneratedScriptPackage.model_validate(payload)

    payload = sg._fallback_manifest_payload(_manifest())
    payload["scenes"][0]["type"] = "news"
    with pytest.raises(ValidationError):
        GeneratedScriptPackage.model_validate(payload)

    payload = sg._fallback_manifest_payload(_manifest())
    payload["scenes"][0]["on_screen_text"]["headline"] = "one two three four five six"
    with pytest.raises(ValidationError):
        GeneratedScriptPackage.model_validate(payload)

    payload = sg._fallback_manifest_payload(_manifest())
    payload["scenes"][0]["highlights"] = ["generic"]
    with pytest.raises(ValidationError):
        GeneratedScriptPackage.model_validate(payload)


def test_repair_fills_scene_card_bullets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.script_generator.gemini_configured", lambda: True)
    manifest = _manifest()
    payload = sg._fallback_manifest_payload(manifest)
    for scene in payload["scenes"]:
        scene["highlights"] = ["risk"] if scene["type"] == "risk" else []
    monkeypatch.setattr(
        "services.script_generator.generate_text",
        lambda prompt, **kwargs: json.dumps(payload),
    )

    result = generate_script_from_manifest(manifest)

    assert result["provider"] == "gemini"
    for scene in result["package"]["scenes"]:
        assert 2 <= len(scene["highlights"]) <= 3
        assert all(2 <= len(item.split()) <= 10 for item in scene["highlights"])
        assert set(scene["highlights"]).isdisjoint({"risk", "takeaway", "chart check"})


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda payload: [
                scene.update({"type": "news"})
                for scene in payload["scenes"]
                if scene["type"] == "risk"
            ],
            "risk",
        ),
        (
            lambda payload: [
                scene.update({"type": "news"})
                for scene in payload["scenes"]
                if scene["type"] == "conclusion"
            ],
            "conclusion",
        ),
        (
            lambda payload: payload["scenes"][1].update(
                {"narration": payload["scenes"][1]["narration"] + " You should buy it."}
            ),
            "forbidden",
        ),
        (
            lambda payload: payload["scenes"][1].update(
                {"highlights": ["Buy pressure building", "Watch confirmation next"]}
            ),
            "forbidden",
        ),
    ],
)
def test_business_validation_rejects_invalid_packages(mutator, expected):
    manifest = _manifest()
    payload = sg._fallback_manifest_payload(manifest)
    mutator(payload)
    package = GeneratedScriptPackage.model_validate(payload)

    assert any(expected in error for error in sg._business_errors(package, manifest))


def _manifest() -> ScriptManifest:
    return ScriptManifest.model_validate(
        {
            "automation_stage": "script_manifest_ready",
            "ready_for_gemini_script": True,
            "event": {
                "id": 1,
                "ticker": "ADBE",
                "company": "Adobe",
                "event_type": "story_candidate",
                "date": "2026-05-29",
                "score": 80,
                "reason": "ADBE moved on AI news.",
                "analysis": {
                    "reason": "ADBE rose after investors reacted to AI-related commentary.",
                    "impact": "Investors noticed the combination of AI narrative and cash flow.",
                    "risk": "The market may be over-reading a still-developing catalyst.",
                    "what_to_watch": "Watch management commentary and follow-up filings.",
                },
            },
            "market_context": {"latest_price": {"close": 256.79}},
            "approved_research": {
                "google_news": [
                    {
                        "source_id": "S1",
                        "provider": "google_news",
                        "title": "Adobe AI story",
                        "url": "https://example.com/adbe",
                        "highlights": ["Adobe source highlight"],
                        "source_tier": 1,
                        "claim_use_policy": "hard_facts_and_official_claims",
                    }
                ]
            },
            "citable_sources": [
                {
                    "source_id": "S1",
                    "provider": "google_news",
                    "title": "Adobe AI story",
                    "url": "https://example.com/adbe",
                    "highlights": ["Adobe source highlight"],
                    "source_tier": 1,
                    "claim_use_policy": "hard_facts_and_official_claims",
                }
            ],
            "context_sources": [],
            "discovery_signals": [
                {
                    "discovery_id": "D1",
                    "provider": "google_news",
                    "title": "Adobe low-confirmation discussion",
                    "url": "https://minichart.com/news/adbe",
                    "source": "Minichart",
                    "highlights": ["Low-confidence sites are discussing Adobe."],
                    "source_quality": {
                        "quality": "discovery",
                        "tier": 4,
                        "tier_label": "discovery_low_confirmation",
                        "rank": 4,
                        "reason": "discovery or low-confirmation source",
                    },
                    "usage_policy": "Discovery only. Do not cite as source_ids or factual support.",
                }
            ],
            "discovery_sources": [
                {
                    "discovery_id": "D1",
                    "provider": "google_news",
                    "title": "Adobe low-confirmation discussion",
                    "url": "https://minichart.com/news/adbe",
                    "source": "Minichart",
                    "highlights": ["Low-confidence sites are discussing Adobe."],
                    "source_quality": {
                        "quality": "discovery",
                        "tier": 4,
                        "tier_label": "discovery_low_confirmation",
                        "rank": 4,
                        "reason": "discovery or low-confirmation source",
                    },
                    "source_tier": 4,
                    "claim_use_policy": "discovery_only_requires_confirmation",
                    "requires_confirmation": True,
                    "usage_policy": "Discovery only. Do not cite as source_ids or factual support.",
                }
            ],
            "rejected_sources": [],
            "research_review": {
                "bundle_path": "outputs/research/ADBE_2026-05-29_1",
                "approved": True,
                "source_count": 1,
            },
            "gemini_script_request": {
                "output_format": {"title": "string"},
                "narrative_beats": ["hook", "catalyst"],
                "duration_target": "60-75 seconds",
                "voice": "Calm analyst",
                "constraints": ["No trading advice."],
            },
            "next_stage": "gemini_script_generation",
        }
    )
