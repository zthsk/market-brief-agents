from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from media_engine.paths import script_output_dir
from media_engine.script_schema import (
    GeneratedScriptPackage,
    ScriptManifest,
    load_script_manifest,
    manifest_to_json,
)
from pydantic import ValidationError
from services.gemini import generate_json, generate_text, gemini_configured
from services.logging_utils import get_logger


LOGGER = get_logger(__name__)
SCRIPT_SYSTEM_INSTRUCTION = (
    "Return one valid JSON object only. No Markdown. No commentary. Never provide trading advice."
)
FINAL_COMPLIANCE_LINE = "This is Market Brief Agents. Educational only. Not financial advice."
ALLOWED_AUDIO_MODES = {
    "breaking_move",
    "earnings_breakdown",
    "risk_warning",
    "contrarian_signal",
    "educational_explainer",
}
FORBIDDEN_ADVICE_PATTERNS = [
    r"\bbuy\b",
    r"\bsell\b",
    r"\bhold\b",
    r"\bprice target\b",
    r"\bguaranteed\b",
    r"\bwill definitely\b",
    r"\bmust own\b",
    r"\bmoon\b",
    r"\bexplode\b",
]


def generate_script_from_manifest_path(manifest_path: str | Path) -> dict[str, Any]:
    manifest = load_script_manifest(manifest_path)
    return generate_script_from_manifest(manifest)


def generate_script_from_manifest(manifest: ScriptManifest) -> dict[str, Any]:
    if not manifest.ready_for_gemini_script:
        raise ValueError("Script manifest is not ready for script generation")

    prompt = _manifest_prompt(manifest)
    bundle = _script_bundle(manifest)
    bundle.mkdir(parents=True, exist_ok=True)
    _write_json(bundle / "manifest_snapshot.json", manifest_to_json(manifest))
    _write_json(bundle / "prompt.json", prompt)

    provider = _selected_script_provider()
    validation_errors: list[dict[str, Any]] = []
    repair_attempt: dict[str, Any] | None = None
    if provider == "local":
        raw_response = _fallback_manifest_payload(manifest)
    else:
        try:
            raw_response = _provider_raw_response(provider, prompt)
        except Exception as exc:
            LOGGER.warning(
                "Falling back to local manifest script for %s after %s error: %s",
                manifest.event.ticker,
                provider,
                exc,
            )
            raw_response = {"provider_error": str(exc)}
            validation_errors.append({"stage": "provider_call", "errors": [str(exc)]})
            provider = "local"
    _write_json(bundle / "raw_response.json", _json_safe(raw_response))
    parsed_response: dict[str, Any] | None = None
    if provider != "local":
        try:
            parsed_response = _extract_json_object(raw_response)
            _write_json(bundle / "raw_response_parsed.json", parsed_response)
        except ValueError:
            parsed_response = None

    package: GeneratedScriptPackage | None = None
    if provider != "local":
        package, validation_errors, repair_attempt = _package_from_raw_response(
            raw_response,
            manifest,
            validation_errors,
        )

    if package is None:
        fallback_payload = _fallback_manifest_payload(manifest)
        package = _validate_package(fallback_payload, manifest)
        provider = "local"

    db_fields = _db_fields(package)
    if validation_errors:
        _write_json(bundle / "validation_errors.json", validation_errors)
    if repair_attempt:
        _write_json(bundle / "repair_attempt.json", repair_attempt)
    _write_json(bundle / "validated_package.json", package.model_dump())
    _write_script_audit_bundle(manifest, bundle, package, db_fields, provider)
    return {
        "event_id": manifest.event.id,
        "provider": provider,
        "bundle_path": str(bundle),
        "script_path": str(bundle / "script.json"),
        "prompt_path": str(bundle / "prompt.json"),
        "raw_response_path": str(bundle / "raw_response.json"),
        "raw_response_parsed_path": str(bundle / "raw_response_parsed.json")
        if parsed_response is not None
        else None,
        "validated_package_path": str(bundle / "validated_package.json"),
        "package": package.model_dump(),
        "db_fields": db_fields,
        "payload": db_fields,
    }


def generate_script(ticker: str, analysis: dict, event: dict, research: list[dict] | None = None) -> dict:
    if os.getenv("AI_PROVIDER", "").lower() == "gemini" and gemini_configured():
        try:
            return _gemini_script(ticker, analysis, event, research or [])
        except Exception as exc:
            LOGGER.warning("Falling back to local script for %s after Gemini error: %s", ticker, exc)
    if os.getenv("OPENAI_API_KEY"):
        try:
            return _openai_script(ticker, analysis, event)
        except Exception as exc:
            LOGGER.warning("Falling back to local script for %s after OpenAI error: %s", ticker, exc)
    else:
        LOGGER.info("Using local script for %s because OPENAI_API_KEY is not configured.", ticker)
    return _fallback_script(ticker, analysis, event)


def _manifest_prompt(manifest: ScriptManifest) -> dict[str, Any]:
    return {
        "task": (
            "Create a highly engaging short-form vertical finance video production plan "
            "for TikTok, YouTube Shorts, and Instagram Reels."
        ),
        "manifest_version": "script-manifest-v2-scene-plan",
        "event": manifest_to_json(manifest)["event"],
        "market_context": manifest.market_context,
        "approved_research": manifest_to_json(manifest)["approved_research"],
        "citable_sources": manifest_to_json(manifest).get("citable_sources", []),
        "context_sources": manifest_to_json(manifest).get("context_sources", []),
        "discovery_sources": manifest_to_json(manifest).get("discovery_sources", []),
        "rejected_sources": manifest_to_json(manifest).get("rejected_sources", []),
        "discovery_signals": manifest_to_json(manifest).get("discovery_signals", []),
        "research_review": manifest_to_json(manifest)["research_review"],
        "script_request": manifest_to_json(manifest)["gemini_script_request"],
        "requirements": {
            "output": "Return one valid JSON object only.",
            "audience": "Retail investors and finance enthusiasts with basic market knowledge.",
            "duration": "Target 60-75 seconds total.",
            "scene_count": "Return 4-8 scenes.",
            "required_flow": [
                "hook",
                "what happened",
                "why it matters",
                "supporting evidence",
                "risk or counterpoint when relevant",
                "takeaway",
            ],
            "scene_types": [
                "hook",
                "price_action",
                "news",
                "earnings",
                "financials",
                "analyst",
                "company",
                "industry",
                "risk",
                "comparison",
                "timeline",
                "conclusion",
            ],
            "asset_types": [
                "stock_chart",
                "price_move",
                "volume_chart",
                "company_logo",
                "company_photo",
                "ceo_photo",
                "earnings_summary",
                "financial_metric",
                "news_headline",
                "analyst_rating",
                "analyst_price_target",
                "industry_broll",
                "product_image",
                "competitor_logo",
                "timeline",
                "calendar_event",
                "warning_indicator",
                "market_statistic",
            ],
            "hook_rules": [
                "First scene must be type hook and importance high.",
                "Grab attention within the first 3 seconds.",
                "Use a surprising number, dollar amount, percentage move, unexpected event, or evidence-backed bold statement.",
                "Avoid generic openings like Today we are talking about.",
            ],
            "narration_rules": [
                "Write natural voiceover copy.",
                "Use short, punchy sentences.",
                "Avoid bullet-point narration, formal report language, excessive numbers, and long sentences.",
                "Do not provide financial advice.",
                f"The stored/voiced script will end with: {FINAL_COMPLIANCE_LINE}",
            ],
            "on_screen_text_rules": [
                "Every scene must include headline and subheadline.",
                "headline maximum 5 words.",
                "subheadline maximum 8 words.",
                "Optimize for mobile viewing.",
            ],
            "scene_card_bullet_rules": [
                "Every scene must include highlights with exactly 2 or 3 short card bullets.",
                "Each highlight must be 2-10 words and useful as animated on-screen bullet text.",
                "Write highlights from the same facts and narration beat as that scene.",
                "Do not use generic labels such as risk, takeaway, catalyst, chart check, or market brief.",
                "Do not repeat the headline or subheadline as a highlight.",
            ],
            "confidence_rules": [
                "Set confidence_level high for facts derived directly from earnings, SEC filings, or verified news.",
                "Set confidence_level medium for interpretation such as investors may believe or markets appear concerned.",
            ],
            "source_policy": [
                "citable_sources are the source_id pool for sourced factual claims.",
                "context_sources may support broad macro, consumer sentiment, policy backdrop, business trend, or public reaction context only.",
                "Tier 1 sources should be preferred when exact financial, legal, regulatory, filing, earnings, insider-trade, or ownership claims are available from both Tier 1 and Tier 2.",
                "Tier 2 can support hard facts, but use cautious attribution for exact claims when no Tier 1 source is present.",
                "discovery_sources and rejected_sources are not source_id pools.",
                "Never put discovery_signals IDs in source_ids.",
                "Never put discovery_sources, discovery_signals, or rejected_sources IDs in source_ids.",
                "Never name low-confirmation platforms as evidence in narration.",
                "Do not say a claim is true because discovery_sources mention it.",
                "If only discovery_sources point to a catalyst, say no high-confidence catalyst is confirmed and use cautious language.",
                "Discovery sources may only shape cautious phrases like investors may be reacting to, appears, or may be watching.",
                "If a statement is inferred from market context instead of a source, say it is inferred.",
                "Do not cite rejected or unavailable sources.",
                "Every source_id in output must exist in citable_sources or context_sources.",
                "Use attribution-only language and avoid copying article wording, snippets, or headlines verbatim.",
            ],
            "caution_language": [
                "If catalyst is uncertain, use appears, likely, may be reacting to, or investors may be reading this as.",
            ],
            "output_shape": {
                "video_metadata": {
                    "title": "string, max 80 chars",
                    "ticker": manifest.event.ticker,
                    "estimated_duration_seconds": 68,
                },
                "asset_requests": [{"asset_type": "stock_chart", "query": "optional string", "reason": "optional string"}],
                "scenes": [
                    {
                        "id": 1,
                        "type": "hook",
                        "importance": "high",
                        "confidence_level": "medium",
                        "narration": "spoken narration",
                        "on_screen_text": {"headline": "max 5 words", "subheadline": "max 8 words"},
                        "highlights": [
                            "Move needs context",
                            "Catalyst still matters",
                            "Watch confirmation next",
                        ],
                        "source_ids": ["S1"],
                        "visual_requirements": [{"asset_type": "price_move"}],
                    }
                ],
            },
        },
    }


def _selected_script_provider() -> str:
    if os.getenv("AI_PROVIDER", "").lower() == "gemini" and gemini_configured():
        return "gemini"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "local"


def _provider_raw_response(provider: str, prompt: dict[str, Any]) -> Any:
    if provider == "gemini":
        return generate_text(
            {
                "payload": prompt,
                "format": "Return one valid JSON object only.",
            },
            system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
        )
    if provider == "openai":
        return _openai_manifest_script(prompt)
    raise ValueError(f"Unsupported script provider: {provider}")


def _package_from_raw_response(
    raw_response: Any,
    manifest: ScriptManifest,
    validation_errors: list[dict[str, Any]],
) -> tuple[GeneratedScriptPackage | None, list[dict[str, Any]], dict[str, Any] | None]:
    repair_attempt = None
    try:
        payload = _extract_json_object(raw_response)
    except ValueError as exc:
        validation_errors.append({"stage": "json_extract", "errors": [str(exc)]})
        return None, validation_errors, repair_attempt

    payload = _apply_pre_validation_defaults(payload, manifest)
    package = _try_validate(payload, manifest, "initial_validation", validation_errors)
    if package and not _business_errors(package, manifest):
        return package, validation_errors, repair_attempt

    if package:
        validation_errors.append(
            {"stage": "business_validation", "errors": _business_errors(package, manifest)}
        )
    repair_attempt = {"before": payload}
    repaired_payload = _repair_payload(payload, manifest)
    repair_attempt["after"] = repaired_payload
    package = _try_validate(repaired_payload, manifest, "post_repair_validation", validation_errors)
    if package:
        errors = _business_errors(package, manifest)
        if not errors:
            return package, validation_errors, repair_attempt
        validation_errors.append({"stage": "post_repair_business_validation", "errors": errors})
    return None, validation_errors, repair_attempt


def _extract_json_object(raw_response: Any) -> dict[str, Any]:
    if isinstance(raw_response, dict):
        return dict(raw_response)
    if not isinstance(raw_response, str):
        raise ValueError(f"Unsupported raw response type: {type(raw_response).__name__}")
    text = raw_response.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in raw response") from None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON object: {exc}") from None


def _apply_pre_validation_defaults(payload: dict[str, Any], manifest: ScriptManifest) -> dict[str, Any]:
    patched = dict(payload)
    metadata = patched.setdefault("video_metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("ticker", manifest.event.ticker)
        metadata.setdefault("title", _truncate_chars(f"{manifest.event.ticker} move explained", 80))
        metadata.setdefault("estimated_duration_seconds", 68)
    patched.setdefault("asset_requests", [])
    return patched


def _try_validate(
    payload: dict[str, Any],
    manifest: ScriptManifest,
    stage: str,
    validation_errors: list[dict[str, Any]],
) -> GeneratedScriptPackage | None:
    try:
        package = GeneratedScriptPackage.model_validate(payload)
    except ValidationError as exc:
        validation_errors.append({"stage": stage, "errors": exc.errors()})
        return None
    errors = _source_id_errors(package, manifest)
    if errors:
        validation_errors.append({"stage": f"{stage}_source_policy", "errors": errors})
        return None
    return package


def _validate_package(payload: dict[str, Any], manifest: ScriptManifest) -> GeneratedScriptPackage:
    patched = _apply_pre_validation_defaults(payload, manifest)
    package = GeneratedScriptPackage.model_validate(patched)
    errors = _source_id_errors(package, manifest) + _business_errors(package, manifest)
    if errors:
        raise ValueError(f"Invalid fallback script package: {errors}")
    return package


def _business_errors(package: GeneratedScriptPackage, manifest: ScriptManifest) -> list[str]:
    errors = []
    script = _script_from_package(package)
    word_count = _word_count(script)
    if word_count < 90 or word_count > 230:
        errors.append(f"script word count should be 90-230 for 60-75 seconds, got {word_count}")
    if len(package.scenes) < 4 or len(package.scenes) > 8:
        errors.append(f"scene count must be 4-8, got {len(package.scenes)}")
    section_types = {scene.type for scene in package.scenes}
    if "risk" not in section_types:
        errors.append("risk/caveat section is required")
    if "conclusion" not in section_types:
        errors.append("conclusion/takeaway section is required")
    visible_text = " ".join(
        [
            script,
            *[
                " ".join([scene.on_screen_text.headline, scene.on_screen_text.subheadline, *scene.highlights])
                for scene in package.scenes
            ],
        ]
    )
    advice_errors = _forbidden_advice_errors(visible_text)
    errors.extend(advice_errors)
    return errors


def _source_id_errors(package: GeneratedScriptPackage, manifest: ScriptManifest) -> list[str]:
    allowed = set()
    for source in manifest.sources:
        tier = source.source_tier or source.source_quality.get("tier")
        if tier is None or int(tier) < 4:
            allowed.add(source.source_id)
    blocked = {
        signal.discovery_id
        for signal in [*manifest.discovery_sources, *manifest.discovery_signals, *manifest.rejected_sources]
    }
    errors = []
    for index, scene in enumerate(package.scenes, start=1):
        unknown = sorted(set(scene.source_ids) - allowed)
        if unknown:
            errors.append(f"scene {index} has unknown source_ids: {', '.join(unknown)}")
        blocked_ids = sorted(set(scene.source_ids) & blocked)
        if blocked_ids:
            errors.append(f"scene {index} cites discovery/rejected source_ids: {', '.join(blocked_ids)}")
    return errors


def _forbidden_advice_errors(text: str) -> list[str]:
    normalized = text.lower().replace("sell-off", "selloff").replace("sell off", "selloff")
    errors = []
    for pattern in FORBIDDEN_ADVICE_PATTERNS:
        if re.search(pattern, normalized):
            errors.append(f"forbidden advice phrase matched: {pattern}")
    return errors


def _repair_payload(payload: dict[str, Any], manifest: ScriptManifest) -> dict[str, Any]:
    repaired = _apply_pre_validation_defaults(dict(payload), manifest)
    metadata = repaired.get("video_metadata")
    if isinstance(metadata, dict):
        metadata["title"] = _truncate_chars(
            str(metadata.get("title") or f"{manifest.event.ticker} move explained"),
            80,
        )
    scenes = repaired.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        scenes = _fallback_manifest_payload(manifest)["scenes"]
    for index, scene in enumerate(scenes[:8], start=1):
        if not isinstance(scene, dict):
            continue
        scene["id"] = index
        if scene.get("type") == "takeaway":
            scene["type"] = "conclusion"
        scene.setdefault("importance", "medium")
        scene.setdefault("confidence_level", "medium")
        scene["narration"] = _clean_narration_text(
            _sanitize_advice_language(str(scene.get("narration") or ""))
        )
        raw_highlights = scene.get("highlights", [])
        scene["source_ids"] = _valid_source_ids(scene.get("source_ids"), manifest)
        scene.setdefault("visual_requirements", [{"asset_type": _asset_type_for_scene(scene.get("type"))}])
        text = scene.setdefault("on_screen_text", {})
        if isinstance(text, dict):
            text["headline"] = _truncate_words(
                _sanitize_advice_language(str(text.get("headline") or scene.get("type") or "Update")),
                5,
            )
            text["subheadline"] = _truncate_words(
                _sanitize_advice_language(str(text.get("subheadline") or scene.get("narration") or "")),
                8,
            )
        scene["highlights"] = _scene_card_highlights(scene, manifest, raw_highlights)
    if scenes:
        scenes[0]["type"] = "hook"
        scenes[0]["importance"] = "high"
    scenes = _ensure_required_story_beats(scenes[:8], manifest)
    repaired["scenes"] = _trim_scene_plan_words(scenes[:8], target_words=220)
    return repaired


def _ensure_required_story_beats(
    scenes: list[dict[str, Any]],
    manifest: ScriptManifest,
) -> list[dict[str, Any]]:
    scenes = [scene for scene in scenes if isinstance(scene, dict)]
    scene_types = {str(scene.get("type") or "") for scene in scenes}
    if "risk" not in scene_types:
        scenes = _add_risk_scene(scenes, manifest)
    scene_types = {str(scene.get("type") or "") for scene in scenes}
    if "conclusion" not in scene_types:
        scenes.append(_fallback_conclusion_scene(len(scenes) + 1, manifest))
    return _renumber_scenes(scenes[:8])


def _add_risk_scene(scenes: list[dict[str, Any]], manifest: ScriptManifest) -> list[dict[str, Any]]:
    risk_index = _risk_like_scene_index(scenes)
    conclusion_index = next(
        (index for index, scene in enumerate(scenes) if scene.get("type") == "conclusion"),
        None,
    )
    if risk_index is not None:
        risk_scene = dict(scenes[risk_index])
        risk_scene["type"] = "risk"
        risk_scene["importance"] = "high"
        risk_scene.setdefault("confidence_level", "medium")
        risk_scene["visual_requirements"] = [{"asset_type": "warning_indicator"}]
        text = risk_scene.setdefault("on_screen_text", {})
        if isinstance(text, dict):
            text["headline"] = _truncate_words(str(text.get("headline") or "Risk Check"), 5)
            text["subheadline"] = _truncate_words(str(text.get("subheadline") or "Execution still matters"), 8)
        if risk_index == conclusion_index:
            scenes[risk_index] = risk_scene
            scenes.append(_fallback_conclusion_scene(len(scenes) + 1, manifest, source_scene=risk_scene))
        else:
            scenes.insert(min(len(scenes), risk_index + 1), risk_scene)
        return scenes

    insert_at = max(1, len(scenes) - 1)
    scenes.insert(insert_at, _fallback_risk_scene(insert_at + 1, manifest))
    return scenes


def _risk_like_scene_index(scenes: list[dict[str, Any]]) -> int | None:
    conclusion_match = _risk_like_scene_index_for_types(scenes, {"conclusion", "takeaway"})
    if conclusion_match is not None:
        return conclusion_match
    return _risk_like_scene_index_for_types(scenes, set())


def _risk_like_scene_index_for_types(
    scenes: list[dict[str, Any]],
    scene_types: set[str],
) -> int | None:
    for index, scene in enumerate(scenes):
        if scene_types and str(scene.get("type") or "") not in scene_types:
            continue
        haystack = " ".join(
            str(value or "")
            for value in [
                scene.get("type"),
                scene.get("narration"),
                (scene.get("on_screen_text") or {}).get("headline")
                if isinstance(scene.get("on_screen_text"), dict)
                else "",
                (scene.get("on_screen_text") or {}).get("subheadline")
                if isinstance(scene.get("on_screen_text"), dict)
                else "",
            ]
        ).lower()
        haystack = haystack.replace("risk management", "")
        if re.search(
            r"\b(risk|risks|caveat|concern|concerns|uncertain|uncertainty|delay|delays|execution|"
            r"regulatory|legal|valuation|downside|challenge|challenges|however|but|while)\b",
            haystack,
        ):
            return index
    return None


def _fallback_risk_scene(scene_id: int, manifest: ScriptManifest) -> dict[str, Any]:
    analysis = manifest.event.analysis or {}
    risk = _fallback_clause(
        analysis.get("risk"),
        "the catalyst still needs confirmation from follow-up data",
        12,
    )
    return _scene_plan(
        scene_id,
        "risk",
        "high",
        "medium",
        f"The risk is simple: {risk}. That makes follow-through the key test.",
        "Risk check",
        "Follow-through still matters",
        [
            risk,
            "Follow-through is the test",
            "Confirmation still matters",
        ],
        ["warning_indicator"],
    )


def _fallback_conclusion_scene(
    scene_id: int,
    manifest: ScriptManifest,
    source_scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = manifest.event.analysis or {}
    watch = _fallback_clause(
        analysis.get("what_to_watch"),
        "company commentary and the next filing cycle",
        10,
    )
    source_ids = []
    if source_scene:
        source_ids = [
            str(item)
            for item in source_scene.get("source_ids", [])
            if str(item)
        ][:3]
    scene = _scene_plan(
        scene_id,
        "conclusion",
        "high",
        "medium",
        f"The takeaway: the move is real, but execution decides whether the story lasts. Watch {watch}.",
        "Takeaway",
        "Execution decides what lasts",
        [
            watch,
            "Execution decides what lasts",
            "Separate move from facts",
        ],
        ["calendar_event"],
    )
    scene["source_ids"] = source_ids
    return scene


def _renumber_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, scene in enumerate(scenes, start=1):
        scene["id"] = index
    return scenes


def _openai_manifest_script(prompt: dict[str, Any]) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": SCRIPT_SYSTEM_INSTRUCTION},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content or "{}")


def _fallback_manifest_payload(manifest: ScriptManifest) -> dict[str, Any]:
    analysis = manifest.event.analysis or {}
    reason = _fallback_clause(
        manifest.event.reason or analysis.get("reason"),
        "investors are reassessing the latest company narrative",
        10,
    )
    impact = _fallback_clause(
        analysis.get("impact"),
        "expectations can move before the full story is settled",
        10,
    )
    risk = _fallback_clause(
        analysis.get("risk"),
        "the catalyst still needs confirmation from follow-up data",
        10,
    )
    watch = _fallback_clause(
        analysis.get("what_to_watch"),
        "company commentary and the next filing cycle",
        9,
    )
    watch = re.sub(r"^watch\s+", "", watch, flags=re.IGNORECASE)
    scenes = [
        _scene_plan(
            1,
            "hook",
            "high",
            "medium",
            f"{manifest.event.ticker} is moving, and the real question is why.",
            "Why it moved",
            "A sharp move needs context",
            [
                f"{manifest.event.ticker} move needs context",
                "Price reaction came first",
                "Facts decide what lasts",
            ],
            ["price_move", "stock_chart"],
        ),
        _scene_plan(
            2,
            "price_action",
            "high",
            "high",
            f"The latest market data puts {manifest.event.ticker} back on the watchlist after {reason}.",
            "Price action",
            "The move stands out",
            [
                reason,
                "Move stands out today",
                "Reaction needs confirmation",
            ],
            ["stock_chart", "price_move"],
        ),
        _scene_plan(
            3,
            "news",
            "high",
            "medium",
            f"The catalyst appears tied to {reason}, but the source trail still matters.",
            "Possible catalyst",
            "Evidence matters here",
            [
                reason,
                "Source trail matters",
                "Catalyst may still develop",
            ],
            ["news_headline"],
        ),
        _scene_plan(
            4,
            "company",
            "medium",
            "medium",
            f"Why it matters: {impact}. That can change how investors frame the next update.",
            "Why it matters",
            "Expectations can shift fast",
            [
                impact,
                "Expectations can shift fast",
                "Next update matters",
            ],
            ["company_logo", "market_statistic"],
        ),
        _scene_plan(
            5,
            "risk",
            "high",
            "medium",
            f"The caveat is important: {risk}. This is still a developing market story.",
            "The caveat",
            "Confirmation still matters",
            [
                risk,
                "Confirmation still matters",
                "Story may change",
            ],
            ["warning_indicator"],
        ),
        _scene_plan(
            6,
            "conclusion",
            "high",
            "medium",
            f"The takeaway: separate the price move from confirmed business facts. Watch {watch}.",
            "Takeaway",
            "Separate price from facts",
            [
                watch,
                "Separate price from facts",
                "Execution decides what lasts",
            ],
            ["calendar_event"],
        ),
    ]
    return {
        "video_metadata": {
            "title": _truncate_chars(f"{manifest.event.ticker} move explained: {manifest.event.reason}", 80),
            "ticker": manifest.event.ticker,
            "estimated_duration_seconds": 68,
        },
        "asset_requests": _asset_requests_from_scenes(scenes),
        "scenes": scenes,
    }


def _fallback_clause(value: Any, fallback: str, word_limit: int) -> str:
    if isinstance(value, list):
        text = " ".join(str(item) for item in value[:2])
    elif isinstance(value, dict):
        text = " ".join(str(item) for item in value.values())
    else:
        text = _string_field(value, fallback)
    text = re.sub(r"[*_`#>\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;\"'")
    text = _sanitize_advice_language(text)
    if not text or _word_count(text) > word_limit * 3:
        text = fallback
    return _truncate_words(text, word_limit).rstrip(" .,:;")


def _sanitize_advice_language(text: str) -> str:
    replacements = {
        r"\bbuy\b": "positive",
        r"\bsell\b": "negative",
        r"\bhold\b": "neutral",
        r"\bprice target\b": "valuation estimate",
        r"\bguaranteed\b": "expected",
        r"\bwill definitely\b": "may",
        r"\bmust own\b": "closely watched",
        r"\bmoon\b": "move sharply",
        r"\bexplode\b": "move sharply",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _clean_narration_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\.'\.", ".'", text)
    text = re.sub(r'\."\.', '."', text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text


def _valid_source_ids(value: Any, manifest: ScriptManifest) -> list[str]:
    if not isinstance(value, list):
        return []
    allowed = set()
    for source in manifest.sources:
        tier = source.source_tier or source.source_quality.get("tier")
        if tier is None or int(tier) < 4:
            allowed.add(source.source_id)
    blocked = {
        signal.discovery_id
        for signal in [*manifest.discovery_sources, *manifest.discovery_signals, *manifest.rejected_sources]
    }
    return [str(item) for item in value if str(item) in allowed and str(item) not in blocked]


def _trim_scene_plan_words(scenes: list[dict[str, Any]], target_words: int) -> list[dict[str, Any]]:
    current = sum(_word_count(str(scene.get("narration") or "")) for scene in scenes)
    if current <= target_words:
        return scenes
    scene_count = max(1, len(scenes))
    base_limit = max(18, target_words // scene_count)
    for scene in scenes:
        narration = str(scene.get("narration") or "")
        limit = base_limit
        if scene.get("type") == "hook":
            limit = min(limit, 24)
        elif scene.get("type") == "conclusion":
            limit = min(limit + 4, 34)
        scene["narration"] = _truncate_narration(narration, limit)
    return scenes


def _truncate_narration(text: str, limit: int) -> str:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text or "") if item.strip()]
    selected: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*selected, sentence])
        if _word_count(candidate) <= limit:
            selected.append(sentence)
        elif selected:
            break
        else:
            return _clean_narration_text(
                _drop_dangling_tail(_truncate_words(sentence, limit)).rstrip(" .,:;") + "."
            )
    if selected:
        return " ".join(selected)
    return _clean_narration_text(_truncate_words(text, limit).rstrip(" .,:;") + ".")


def _drop_dangling_tail(text: str) -> str:
    words = text.split()
    while words and words[-1].strip(".,;:'\"").lower() in {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "for",
        "from",
        "of",
        "or",
        "the",
        "to",
        "with",
    }:
        words.pop()
    return " ".join(words) if words else text


def _write_script_audit_bundle(
    manifest: ScriptManifest,
    bundle: Path,
    package: GeneratedScriptPackage,
    db_fields: dict[str, Any],
    provider: str,
) -> Path:
    _write_json(
        bundle / "script.json",
        {
            "provider": provider,
            "package": package.model_dump(),
            "db_fields": db_fields,
            **db_fields,
        },
    )
    (bundle / "README.md").write_text(
        "\n".join(
            [
                f"# {manifest.event.ticker} Script Generation",
                "",
                f"- Event ID: {manifest.event.id}",
                f"- Date: {manifest.event.date}",
                f"- Provider: {provider}",
                "- Source: reviewed script manifest",
            ]
        ),
        encoding="utf-8",
    )
    return bundle


def _script_bundle(manifest: ScriptManifest) -> Path:
    return script_output_dir(manifest.event.ticker, manifest.event.date, manifest.event.id)


def _db_fields(package: GeneratedScriptPackage) -> dict[str, Any]:
    title = package.video_metadata.title
    script = _script_from_package(package)
    return {
        "title": title,
        "script": script,
        "description": _description_from_package(package),
        "tags": _tags_from_package(package),
    }


def _default_audio_profile(manifest: ScriptManifest) -> dict[str, Any]:
    mode = "breaking_move"
    event_type = (manifest.event.event_type or "").lower()
    reason = manifest.event.reason.lower()
    if "earnings" in event_type or "earnings" in reason:
        mode = "earnings_breakdown"
    elif "risk" in reason or "warning" in reason:
        mode = "risk_warning"
    return {
        "voice_profile_id": "ari_vale_market_desk",
        "mode": mode,
        "pace_wpm": 150,
        "energy": "medium_high_controlled",
        "tts_direction": (
            "Ari Vale, Market Brief Agents Market Desk. Calm, sharp, modern market analyst. "
            "Controlled urgency, no hype, no robotic reading."
        ),
    }


def _default_quality_targets() -> dict[str, Any]:
    return {
        "target_duration_sec": 68,
        "min_duration_sec": 61,
        "max_duration_sec": 75,
        "target_words_min": 145,
        "target_words_max": 175,
        "max_on_screen_words": 12,
    }


def _hashtags(manifest: ScriptManifest) -> list[str]:
    return [f"#{manifest.event.ticker}", "#Stocks", "#MarketNews", "#Finance"]


def _script_from_package(package: GeneratedScriptPackage) -> str:
    lines = [scene.narration.strip() for scene in package.scenes if scene.narration.strip()]
    script = " ".join(lines).strip()
    if not script.endswith(FINAL_COMPLIANCE_LINE):
        script = f"{script} {FINAL_COMPLIANCE_LINE}".strip()
    return script


def _description_from_package(package: GeneratedScriptPackage) -> str:
    risk = next((scene.narration for scene in package.scenes if scene.type == "risk"), "")
    base = f"Educational recap of {package.video_metadata.ticker}: {package.video_metadata.title}."
    if risk:
        return _truncate_chars(f"{base} Includes key risk context.", 280)
    return _truncate_chars(base, 280)


def _tags_from_package(package: GeneratedScriptPackage) -> list[str]:
    tags = [package.video_metadata.ticker, "stocks", "market news", "finance"]
    for scene in package.scenes:
        if scene.type not in {"hook", "conclusion", "price_action"}:
            label = scene.type.replace("_", " ")
            if label not in tags:
                tags.append(label)
    return tags[:10]


def _scene_plan(
    scene_id: int,
    scene_type: str,
    importance: str,
    confidence_level: str,
    narration: str,
    headline: str,
    subheadline: str,
    highlights: list[str],
    asset_types: list[str],
) -> dict[str, Any]:
    card_bullets = _coerce_card_bullets(
        [
            *highlights,
            subheadline,
            narration,
            headline,
        ]
    )
    return {
        "id": scene_id,
        "type": scene_type,
        "importance": importance,
        "confidence_level": confidence_level,
        "narration": narration,
        "on_screen_text": {
            "headline": _truncate_words(headline, 5),
            "subheadline": _truncate_words(subheadline, 8),
        },
        "highlights": card_bullets,
        "visual_requirements": [{"asset_type": asset_type} for asset_type in asset_types],
    }


def _scene_card_highlights(
    scene: dict[str, Any],
    manifest: ScriptManifest,
    raw_highlights: Any,
) -> list[str]:
    analysis = manifest.event.analysis or {}
    source_highlights = [
        highlight
        for source in manifest.sources[:4]
        for highlight in source.highlights[:2]
    ]
    scene_type = str(scene.get("type") or "")
    headline = ""
    subheadline = ""
    text = scene.get("on_screen_text")
    if isinstance(text, dict):
        headline = str(text.get("headline") or "")
        subheadline = str(text.get("subheadline") or "")
    candidates: list[Any] = []
    if isinstance(raw_highlights, list):
        candidates.extend(raw_highlights)
    elif raw_highlights:
        candidates.append(raw_highlights)
    candidates.extend(
        [
            *_highlight_chunks(str(scene.get("narration") or "")),
            subheadline,
            *_scene_type_highlight_candidates(scene_type, manifest, analysis),
            *source_highlights,
            headline,
        ]
    )
    return _coerce_card_bullets(candidates, blocked={headline, subheadline})


def _scene_type_highlight_candidates(
    scene_type: str,
    manifest: ScriptManifest,
    analysis: dict[str, Any],
) -> list[str]:
    ticker = manifest.event.ticker
    reason = _fallback_clause(
        manifest.event.reason or analysis.get("reason"),
        "investors are reassessing the story",
        8,
    )
    impact = _fallback_clause(
        analysis.get("impact"),
        "expectations can shift quickly",
        8,
    )
    risk = _fallback_clause(
        analysis.get("risk"),
        "confirmation still matters",
        8,
    )
    watch = _fallback_clause(
        analysis.get("what_to_watch"),
        "watch the next company update",
        8,
    )
    defaults = {
        "hook": [f"{ticker} move needs context", reason, "Facts decide what lasts"],
        "price_action": [reason, "Move stands out today", "Reaction needs confirmation"],
        "news": [reason, "Source trail matters", "Catalyst may still develop"],
        "earnings": [impact, "Margins and guidance matter", "Next quarter sets tone"],
        "financials": [impact, "Cash flow gets attention", "Balance sheet matters"],
        "analyst": [impact, "Expectations moved quickly", "Consensus still matters"],
        "company": [impact, "Company execution matters", "Next update matters"],
        "industry": [impact, "Sector context matters", "Peers shape expectations"],
        "risk": [risk, "Follow-through is the test", "Confirmation still matters"],
        "comparison": [impact, "Peers frame the move", "Relative strength matters"],
        "timeline": [watch, "Timing changes expectations", "Next date matters"],
        "conclusion": [watch, "Separate move from facts", "Execution decides what lasts"],
    }
    return defaults.get(scene_type, [reason, impact, watch])


def _coerce_card_bullets(
    candidates: list[Any],
    *,
    blocked: set[str] | None = None,
) -> list[str]:
    blocked_keys = {_highlight_key(item) for item in (blocked or set()) if item}
    bullets: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for chunk in _highlight_chunks(str(candidate or "")):
            bullet = _clean_card_bullet(chunk)
            key = _highlight_key(bullet)
            if not bullet or key in seen or key in blocked_keys or _is_generic_highlight(bullet):
                continue
            bullets.append(bullet)
            seen.add(key)
            if len(bullets) == 3:
                return bullets
    fallback = ["Facts decide what lasts", "Watch confirmation next", "Execution still matters"]
    for item in fallback:
        key = _highlight_key(item)
        if key not in seen and key not in blocked_keys:
            bullets.append(item)
            seen.add(key)
        if len(bullets) == 3:
            break
    return bullets[:3] if len(bullets) >= 2 else [*bullets, "Watch confirmation next"][:2]


def _highlight_chunks(text: str) -> list[str]:
    text = re.sub(r"[*_`#>\[\]{}]", " ", text)
    chunks = re.split(r"(?<=[.!?])\s+|[;|•\n]+", text)
    return [chunk.strip(" .,:;\"'") for chunk in chunks if chunk.strip(" .,:;\"'")]


def _clean_card_bullet(text: str) -> str:
    text = _sanitize_advice_language(text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;\"'")
    text = re.sub(r"^(the\s+)?(takeaway|risk|caveat|catalyst|context)\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    text = _drop_dangling_tail(_truncate_words(text, 10)).strip(" .,:;\"'")
    if _word_count(text) < 2:
        return ""
    return text


def _highlight_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _is_generic_highlight(text: str) -> bool:
    return _highlight_key(text) in {
        "risk",
        "takeaway",
        "caveat",
        "catalyst",
        "context",
        "chart check",
        "price action",
        "market brief",
        "why it moved",
    }


def _asset_requests_from_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    requests = []
    for scene in scenes:
        for requirement in scene.get("visual_requirements", []):
            asset_type = requirement.get("asset_type")
            if asset_type and asset_type not in seen:
                seen.add(asset_type)
                requests.append({"asset_type": asset_type})
    return requests


def _asset_type_for_scene(scene_type: Any) -> str:
    return {
        "hook": "price_move",
        "price_action": "stock_chart",
        "news": "news_headline",
        "earnings": "earnings_summary",
        "financials": "financial_metric",
        "analyst": "analyst_rating",
        "risk": "warning_indicator",
        "timeline": "timeline",
        "conclusion": "market_statistic",
    }.get(str(scene_type or ""), "market_statistic")


def _narration_segments_from_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_type = section.get("type") or "context"
        segments.append(
            {
                "segment": section_type,
                "text": section.get("narration") or section.get("caption_text") or "",
                "tone": _tone_for_section(str(section_type)),
                "pause_after_sec": 0.35,
                "emphasis_terms": section.get("emphasis_terms") or [],
            }
        )
    return segments


def _sections_from_script(script: str, manifest: ScriptManifest) -> list[dict[str, Any]]:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", script) if item.strip()]
    section_types = [
        "hook",
        "price",
        "catalyst",
        "context",
        "context",
        "risk",
        "watch",
        "takeaway",
        "context",
        "risk",
        "takeaway",
        "cta",
    ]
    sections = []
    for index, section_type in enumerate(section_types):
        narration = sentences[index] if index < len(sentences) else _fallback_sentence(section_type, manifest)
        sections.append(
            {
                "type": section_type,
                "duration_sec": 5.5,
                "on_screen_text": _truncate_words(narration, 12),
                "narration": narration,
                "caption_text": _truncate_words(narration, 14),
                "source_ids": [],
                "emphasis_terms": [manifest.event.ticker] if manifest.event.ticker in narration else [],
                "visual_hint": _visual_hint(section_type),
            }
        )
    return sections


def _fallback_sentence(section_type: str, manifest: ScriptManifest) -> str:
    if section_type == "cta":
        return FINAL_COMPLIANCE_LINE
    if section_type == "watch":
        return "Watch the next company update and whether the move gets confirmed."
    if section_type == "risk":
        return "The caveat is that the catalyst still may not be fully confirmed."
    return f"{manifest.event.ticker} remains a developing market story."


def _rescale_section_durations(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not sections:
        return sections
    target = 66.0
    current = sum(float(section.get("duration_sec") or 0) for section in sections if isinstance(section, dict))
    scale = target / current if current else 1
    repaired = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        copy = dict(section)
        duration = float(copy.get("duration_sec") or target / len(sections))
        copy["duration_sec"] = round(min(6.0, max(3.0, duration * scale)), 2)
        repaired.append(copy)
    return repaired


def _visual_hint(section_type: str) -> str:
    return {
        "hook": "price_card",
        "price": "chart",
        "catalyst": "bullet_card",
        "context": "bullet_card",
        "risk": "warning_card",
        "watch": "bullet_card",
        "takeaway": "takeaway_card",
        "cta": "outro_card",
    }.get(section_type, "bullet_card")


def _tone_for_section(section_type: str) -> str:
    return {
        "hook": "curious_urgent",
        "price": "analytical",
        "catalyst": "analytical",
        "context": "clear",
        "risk": "cautious",
        "watch": "decisive",
        "takeaway": "decisive",
        "cta": "clear",
    }.get(section_type, "clear")


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def _truncate_words(text: str, limit: int) -> str:
    words = re.findall(r"\S+", text or "")
    return " ".join(words[:limit])


def _truncate_chars(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


def _gemini_script(ticker: str, analysis: dict, event: dict, research: list[dict]) -> dict:
    prompt = {
        "ticker": ticker,
        "analysis": analysis,
        "event": event,
        "web_research": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "highlights": item.get("highlights") or [],
            }
            for item in research[:8]
        ],
        "requirements": {
            "format": {"title": "string", "script": "string", "description": "string", "tags": ["string"]},
            "structure": ["HOOK", "WHAT HAPPENED", "WHY IT MATTERS", "WHAT TO WATCH", "CTA"],
            "length_words": "145-175",
            "tone": "clear, specific, energetic, not promotional",
            "constraints": [
                "Educational financial news only.",
                "No buy, sell, hold, price target, or investment advice.",
                "Do not invent catalysts beyond supplied context.",
                "Make the hook strong enough for a short-form video.",
                "Use source-backed specifics from web_research when available.",
                "Include one counterpoint or uncertainty.",
            ],
        },
    }
    payload = generate_json(prompt, "Return JSON only. Never provide trading advice.")
    return _normalize_payload(payload, ticker, analysis)


def _openai_script(ticker: str, analysis: dict, event: dict) -> dict:
    from openai import OpenAI

    client = OpenAI()
    prompt = {
        "ticker": ticker,
        "analysis": analysis,
        "event": event,
        "requirements": {
            "format": ["Title", "Script", "Description", "Tags"],
            "structure": ["HOOK", "WHAT HAPPENED", "WHY IT MATTERS", "WHAT TO WATCH", "CTA"],
            "length_words": "120-150",
            "constraints": "Educational financial news only. No investment advice.",
        },
    }
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": "Return JSON only. Never provide trading advice."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        response_format={"type": "json_object"},
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return _normalize_payload(payload, ticker, analysis)


def _fallback_script(ticker: str, analysis: dict, event: dict) -> dict:
    script = (
        f"HOOK: {ticker} just landed on today's market watchlist after a notable move. "
        f"WHAT HAPPENED: {analysis['reason']} The event score is {event.get('score', 0)}, "
        f"which means price action, volume, filings, or headlines may be lining up. "
        f"WHY IT MATTERS: {analysis['impact']} For a large public company, sharp moves can shape "
        f"the day's financial news cycle even before the full story is clear. "
        f"WHAT TO WATCH: {analysis['what_to_watch']} Also watch whether new details confirm the "
        f"initial catalyst or cool down the reaction. "
        f"CTA: Follow Market Brief Agents for quick, educational market recaps without trading calls."
    )
    return _normalize_payload(
        {
            "title": f"{ticker} market move explained",
            "script": script,
            "description": f"Educational recap of the latest {ticker} market story.",
            "tags": [ticker, "stocks", "market news", "earnings", "finance"],
        },
        ticker,
        analysis,
    )


def _normalize_payload(payload: dict, ticker: str, analysis: dict) -> dict:
    title = _string_field(payload.get("Title") or payload.get("title"), f"{ticker} market update")
    script = _string_field(payload.get("Script") or payload.get("script"), analysis.get("reason", ""))
    description = _string_field(payload.get("Description") or payload.get("description"), title)
    tags = payload.get("Tags") or payload.get("tags") or [ticker, "market news"]
    if isinstance(tags, str):
        tags = [tag.strip() for tag in re.split(r"[,#]", tags) if tag.strip()]
    if not isinstance(tags, list):
        tags = [ticker, "market news"]
    return {"title": title, "script": script, "description": description, "tags": tags}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _string_field(value, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        section_labels = ["HOOK", "WHAT HAPPENED", "WHY IT MATTERS", "WHAT TO WATCH", "CTA"]
        if any(label in value for label in section_labels):
            return " ".join(
                f"{label}: {value[label]}" for label in section_labels if value.get(label)
            )
        for key in ("text", "title", "value", "content"):
            if isinstance(value.get(key), str):
                return value[key]
    if value:
        return str(value)
    return fallback
