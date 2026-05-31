from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from media_engine.paths import script_manifest_dir
from models.database import query
from services.web_research import classify_source, research_bundle_path, research_ready_for_event


def prepare_script_manifest(event_id: int) -> dict[str, Any]:
    event = _event(event_id)
    manifest = build_script_manifest(event)
    target = script_manifest_dir(event["ticker"], event.get("event_date") or "unknown", event_id)
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "manifest.json", manifest)
    _write_markdown(target / "README.md", manifest)
    return {
        "manifest_path": str(target / "manifest.json"),
        "bundle_path": str(target),
        "manifest": manifest,
    }


def prepare_script_manifests(limit: int = 5) -> dict[str, int]:
    rows = query(
        """
        SELECT e.*
        FROM events e
        ORDER BY e.score DESC, e.created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    counts = {"eligible": len(rows), "prepared": 0, "not_ready": 0, "errors": 0}
    for event in rows:
        try:
            result = prepare_script_manifest(int(event["id"]))
            if result["manifest"]["ready_for_gemini_script"]:
                counts["prepared"] += 1
            else:
                counts["not_ready"] += 1
        except Exception:
            counts["errors"] += 1
    return counts


def build_script_manifest(event: dict[str, Any]) -> dict[str, Any]:
    event_id = int(event["id"])
    company = _company(event["ticker"])
    price = _latest_price(event["ticker"])
    research = _research_sources(event)
    citable = [row for row in research if int(row["source_quality"].get("tier", 4)) <= 2]
    context = [row for row in research if int(row["source_quality"].get("tier", 4)) == 3]
    discovery = _discovery_signals(event)
    rejected = _rejected_sources(event)
    approved = research_ready_for_event(event)
    research_manifest = _read_json(research_bundle_path(event) / "manifest.json")
    return {
        "automation_stage": "script_manifest_ready" if approved else "script_manifest_blocked",
        "ready_for_gemini_script": approved and bool(research),
        "ready_for_tts": False,
        "ready_for_render": False,
        "ready_for_posting": False,
        "event": {
            "id": event_id,
            "ticker": event["ticker"],
            "company": company.get("name") or event["ticker"],
            "event_type": event.get("event_type"),
            "date": str(event.get("event_date") or event.get("created_at") or "unknown")[:10],
            "score": event.get("score"),
            "reason": event.get("reason"),
            "analysis": _json_or_empty(event.get("analysis_json")),
        },
        "market_context": {
            "latest_price": price,
            "chart_period": "90 trading days",
            "chart_instruction": "Use the chart for context only; do not infer unsupported causes from price action.",
        },
        "approved_research": _group_sources(research),
        "citable_sources": citable,
        "context_sources": context,
        "discovery_signals": discovery,
        "discovery_sources": discovery,
        "rejected_sources": rejected,
        "research_review": {
            "bundle_path": str(research_bundle_path(event)),
            "approved": approved,
            "approval_mode": research_manifest.get("approval_mode", "manual" if approved else "manual_required"),
            "source_count": len(research),
            "discovery_signal_count": len(discovery),
            "rejected_source_count": len(rejected),
        },
        "gemini_script_request": {
            "output_format": {
                "title": "string",
                "hook": "string",
                "sections": [
                    {
                        "type": "hook|price|catalyst|context|risk|watch|takeaway",
                        "on_screen_text": "8-12 major words max",
                        "highlights": [
                            "2-3 short card bullets, each 2-10 words, synced to narration beats"
                        ],
                        "narration": "spoken analyst narration",
                        "source_ids": ["source id strings used for this section"],
                    }
                ],
                "description": "string",
                "tags": ["string"],
            },
            "narrative_beats": [
                "open with ticker and price movement using moved, surged, jumped, fell, dropped, or slipped language when supported",
                "catalyst",
                "why investors care",
                "chart context",
                "risk or caveat",
                "what to watch next",
                "takeaway",
            ],
            "duration_target": "60-75 seconds total; no fixed outro is appended.",
            "voice": "Calm analyst, natural pauses, clear numbers, no hype.",
            "constraints": [
                "Use only supplied facts and approved research sources.",
                "Use citable_sources for factual support.",
                "Use context_sources only for broad macro, consumer sentiment, policy backdrop, business trend, or public reaction context.",
                "Prefer Tier 1 sources when exact financial, legal, regulatory, filing, earnings, insider-trade, or ownership claims are available from both Tier 1 and Tier 2.",
                "Use discovery_sources only as weak leads for cautious market-context language.",
                "Never cite discovery_signals, discovery_sources, or rejected_sources with source_ids or treat them as factual support.",
                "Use attribution-only language and avoid copying article wording, snippets, or headlines verbatim.",
                "No buy, sell, hold, price target, or investment advice.",
                "Do not include a follow/subscribe CTA or separate outro; end with a concise takeaway and educational disclaimer-ready line.",
                "Keep on-screen text short; narration can carry detail.",
                "Every scene must include 2-3 useful highlights for animated card bullets; do not use generic labels like risk, takeaway, or chart check.",
                "Mention uncertainty when the catalyst is inferred rather than directly sourced.",
            ],
        },
        "next_stage": "gemini_script_generation",
    }


def _event(event_id: int) -> dict[str, Any]:
    rows = query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not rows:
        raise ValueError(f"No event found for id {event_id}")
    return rows[0]


def _company(ticker: str) -> dict[str, Any]:
    rows = query("SELECT * FROM companies WHERE ticker = ?", (ticker,))
    return rows[0] if rows else {"ticker": ticker, "name": ticker}


def _latest_price(ticker: str) -> dict[str, Any]:
    rows = query(
        """
        SELECT date, close, current_price, change_percent, volume
        FROM daily_prices
        WHERE ticker = ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (ticker,),
    )
    return rows[0] if rows else {}


def _research_sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT provider, title, url, source, published_at, highlights_json, metadata_json
        FROM research_sources
        WHERE event_id = ?
        ORDER BY provider, created_at DESC, id DESC
        LIMIT 20
        """,
        (int(event["id"]),),
    )
    script_grade_rows = []
    for index, row in enumerate(rows, start=1):
        row["source_id"] = f"S{index}"
        row["title"] = normalize_manifest_text(row.get("title"))
        row["source"] = normalize_manifest_text(row.get("source"))
        row["highlights"] = [
            normalize_manifest_text(item)
            for item in _json_or_empty(row.pop("highlights_json", None), fallback=[])
        ]
        metadata = _json_or_empty(row.pop("metadata_json", None), fallback={})
        row["source_quality"] = classify_source(event, row)
        _apply_source_metadata(row, metadata)
        if int(row["source_quality"].get("tier", 4)) < 4:
            script_grade_rows.append(row)
    script_grade_rows.sort(
        key=lambda item: (
            item["source_quality"]["tier"],
            item["source_quality"]["rank"],
            item["provider"],
            item["source_id"],
        )
    )
    for index, row in enumerate(script_grade_rows, start=1):
        row["source_id"] = f"S{index}"
    return script_grade_rows


def _discovery_signals(event: dict[str, Any]) -> list[dict[str, Any]]:
    review = _read_json(research_bundle_path(event) / "review_results.json")
    candidates = [*review.get("accepted", []), *review.get("rejected", [])]
    signals = []
    for item in candidates:
        row = _review_item_to_signal(event, item)
        if int(row["source_quality"].get("tier", 4)) == 4:
            signals.append(row)
    signals.sort(key=lambda item: (item["provider"], item["discovery_id"]))
    for index, row in enumerate(signals[:8], start=1):
        row["discovery_id"] = f"D{index}"
    return signals[:8]


def _rejected_sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    review = _read_json(research_bundle_path(event) / "review_results.json")
    rejected = []
    for item in review.get("rejected", []):
        row = _review_item_to_signal(event, item)
        row["usage_policy"] = "Rejected source. Do not cite as source_ids or factual support."
        rejected.append(row)
    rejected.sort(key=lambda item: (item["provider"], item["title"] or "", item["url"] or ""))
    for index, row in enumerate(rejected[:12], start=1):
        row["discovery_id"] = f"R{index}"
    return rejected[:12]


def _review_item_to_signal(event: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    row = {
        "discovery_id": "D0",
        "provider": item.get("provider") or "unknown",
        "title": normalize_manifest_text(item.get("title")),
        "url": item.get("url"),
        "source": normalize_manifest_text(item.get("author") or item.get("source")),
        "published_at": item.get("publishedDate") or item.get("published_at"),
        "highlights": [normalize_manifest_text(value) for value in item.get("highlights") or []],
    }
    row["source_quality"] = classify_source(event, row)
    _apply_source_metadata(row, item)
    row["usage_policy"] = "Discovery only. Do not cite as source_ids or factual support."
    return row


def _apply_source_metadata(row: dict[str, Any], metadata: dict[str, Any]) -> None:
    quality = row.get("source_quality") or {}
    row["source_tier"] = metadata.get("source_tier", quality.get("source_tier", quality.get("tier")))
    row["source_tier_label"] = metadata.get(
        "source_tier_label",
        quality.get("source_tier_label", quality.get("tier_label")),
    )
    row["claim_use_policy"] = metadata.get("claim_use_policy", quality.get("claim_use_policy"))
    row["is_official_company_release"] = bool(
        metadata.get(
            "is_official_company_release",
            quality.get("is_official_company_release", False),
        )
    )
    row["requires_confirmation"] = bool(
        metadata.get(
            "requires_confirmation",
            quality.get("requires_confirmation", int(row.get("source_tier") or 4) >= 3),
        )
    )
    row["classification_reason"] = metadata.get(
        "classification_reason",
        quality.get("classification_reason", quality.get("reason")),
    )


def _group_sources(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["provider"], []).append(row)
    return grouped


def normalize_manifest_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s*\[\s*(?:\.{3}|(?:\.\s*){3}|…)\s*\]\s*(?:[-–—]\s*)?", " ", text)
    text = re.sub(r"([.!?])\s*[-–—]\s*", r"\1 ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_script_manifest_payload(payload: Any) -> tuple[Any, int]:
    """Normalize already-built manifest payload text without changing structure."""
    return _normalize_manifest_value(payload)


def normalize_script_manifest_file(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    payload = _read_json(target)
    if not payload:
        return {"path": str(target), "changed": False, "string_replacements": 0, "error": "empty_or_invalid_json"}

    normalized, replacements = normalize_script_manifest_payload(payload)
    changed = replacements > 0
    if changed:
        _write_json(target, normalized)
        if isinstance(normalized, dict) and (target.parent / "README.md").exists():
            _write_markdown(target.parent / "README.md", normalized)
    return {
        "path": str(target),
        "changed": changed,
        "string_replacements": replacements,
        "error": None,
    }


def normalize_script_manifests(root: str | Path = "outputs/script_manifests") -> dict[str, Any]:
    root_path = Path(root)
    paths = [root_path] if root_path.is_file() else sorted(root_path.glob("**/manifest.json"))
    summary: dict[str, Any] = {
        "root": str(root_path),
        "scanned": len(paths),
        "changed": 0,
        "string_replacements": 0,
        "errors": 0,
        "changed_files": [],
    }
    for path in paths:
        result = normalize_script_manifest_file(path)
        if result.get("error"):
            summary["errors"] += 1
            continue
        if result["changed"]:
            summary["changed"] += 1
            summary["string_replacements"] += int(result["string_replacements"])
            summary["changed_files"].append(result["path"])
    return summary


def _normalize_manifest_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        normalized = normalize_manifest_text(value)
        return normalized, int(normalized != value)
    if isinstance(value, list):
        replacements = 0
        normalized_list = []
        for item in value:
            normalized, count = _normalize_manifest_value(item)
            normalized_list.append(normalized)
            replacements += count
        return normalized_list, replacements
    if isinstance(value, dict):
        replacements = 0
        normalized_dict = {}
        for key, item in value.items():
            normalized, count = _normalize_manifest_value(item)
            normalized_dict[key] = normalized
            replacements += count
        return normalized_dict, replacements
    return value, 0


def _json_or_empty(value: str | None, fallback: Any | None = None) -> Any:
    if fallback is None:
        fallback = {}
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_markdown(path: Path, manifest: dict[str, Any]) -> None:
    event = manifest["event"]
    lines = [
        f"# {event['ticker']} Script Manifest",
        "",
        f"- Ready for Gemini: {manifest['ready_for_gemini_script']}",
        f"- Research sources: {manifest['research_review']['source_count']}",
        f"- Next stage: `{manifest['next_stage']}`",
        "",
        "This file is the reviewed, deterministic payload Gemini should use for script writing.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
