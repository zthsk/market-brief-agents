from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.gemini import generate_json, gemini_configured
from services.web_research import research_bundle_detail, research_bundle_summaries


DIGEST_FILE = "research_digest.json"
DEFAULT_DIGEST_BATCH_SIZE = 200
DIGEST_SYSTEM_INSTRUCTION = (
    "Return one valid JSON object only. No Markdown. Do not provide trading advice. "
    "Do not copy source headlines or snippets verbatim; rewrite into original concise language."
)


def digest_path(bundle_path: str | Path) -> Path:
    return Path(bundle_path) / DIGEST_FILE


def load_research_digest(bundle_path: str | Path) -> dict[str, Any] | None:
    path = digest_path(bundle_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def build_manifest_digest(
    manifest_path: str | Path,
    *,
    use_gemini: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    manifest = _read_manifest(manifest_path)
    bundle_path = _manifest_bundle_path(manifest, manifest_path)
    existing = load_research_digest(bundle_path)
    if (
        existing
        and existing.get("digest_source") == "cleaned_script_manifest"
        and not force
        and (not use_gemini or existing.get("provider") == "gemini")
    ):
        return existing
    digest = (
        _gemini_single_manifest_digest(manifest, manifest_path)
        if use_gemini and gemini_configured()
        else _local_manifest_digest(manifest, manifest_path)
    )
    _write_digest(bundle_path, digest)
    return digest


def build_manifest_digests_for_paths(
    manifest_paths: list[str | Path],
    *,
    use_gemini: bool = False,
    batch_size: int = DEFAULT_DIGEST_BATCH_SIZE,
    force: bool = False,
) -> dict[str, Any]:
    unique_paths = list(dict.fromkeys(str(path) for path in manifest_paths))
    selected = [
        path
        for path in unique_paths
        if force or _manifest_digest_should_run(path, use_gemini=use_gemini)
    ]
    result = {
        "eligible": len(unique_paths),
        "selected": len(selected),
        "digests": 0,
        "provider": "gemini" if use_gemini and gemini_configured() else "local",
        "paths": [],
    }
    if not selected:
        return result
    if use_gemini and gemini_configured():
        for index in range(0, len(selected), max(1, batch_size)):
            batch = selected[index : index + max(1, batch_size)]
            result["digests"] += _write_gemini_manifest_batch(batch, result["paths"])
        return result
    for path in selected:
        build_manifest_digest(path, use_gemini=False, force=force)
        result["digests"] += 1
        result["paths"].append(str(digest_path(_manifest_bundle_path(_read_manifest(path), path))))
    return result


def build_research_digest(
    bundle_path: str | Path,
    *,
    use_gemini: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    existing = load_research_digest(bundle_path)
    if existing and not force:
        return existing
    bundle = research_bundle_detail(bundle_path)
    if not bundle:
        raise ValueError(f"No research bundle found at {bundle_path}")
    digest = _gemini_single_digest(bundle) if use_gemini and gemini_configured() else _local_digest(bundle)
    _write_digest(bundle_path, digest)
    return digest


def build_research_digests(
    *,
    limit: int = 10,
    use_gemini: bool = False,
    batch_size: int = DEFAULT_DIGEST_BATCH_SIZE,
    force: bool = False,
) -> dict[str, Any]:
    summaries = research_bundle_summaries(limit=limit)
    selected = [
        summary
        for summary in summaries
        if force or not digest_path(summary["bundle_path"]).exists()
    ]
    result = {
        "eligible": len(summaries),
        "selected": len(selected),
        "digests": 0,
        "provider": "gemini" if use_gemini and gemini_configured() else "local",
        "paths": [],
    }
    if not selected:
        return result

    if use_gemini and gemini_configured():
        for index in range(0, len(selected), max(1, batch_size)):
            batch = selected[index : index + max(1, batch_size)]
            result["digests"] += _write_gemini_batch(batch, result["paths"])
        return result

    for summary in selected:
        digest = build_research_digest(summary["bundle_path"], use_gemini=False, force=force)
        result["digests"] += 1
        result["paths"].append(str(digest_path(summary["bundle_path"])))
        _write_digest(summary["bundle_path"], digest)
    return result


def build_research_digests_for_bundle_paths(
    bundle_paths: list[str | Path],
    *,
    use_gemini: bool = False,
    batch_size: int = DEFAULT_DIGEST_BATCH_SIZE,
    force: bool = False,
) -> dict[str, Any]:
    unique_paths = list(dict.fromkeys(str(path) for path in bundle_paths))
    selected = [
        path
        for path in unique_paths
        if force or _digest_should_run(path, use_gemini=use_gemini)
    ]
    result = {
        "eligible": len(unique_paths),
        "selected": len(selected),
        "digests": 0,
        "provider": "gemini" if use_gemini and gemini_configured() else "local",
        "paths": [],
    }
    if not selected:
        return result
    if use_gemini and gemini_configured():
        for index in range(0, len(selected), max(1, batch_size)):
            batch = [{"bundle_path": path} for path in selected[index : index + max(1, batch_size)]]
            result["digests"] += _write_gemini_batch(batch, result["paths"])
        return result
    for path in selected:
        digest = build_research_digest(path, use_gemini=False, force=force)
        result["digests"] += 1
        result["paths"].append(str(digest_path(path)))
        _write_digest(path, digest)
    return result


def _digest_should_run(bundle_path: str | Path, *, use_gemini: bool) -> bool:
    existing = load_research_digest(bundle_path)
    if not existing:
        return True
    return bool(use_gemini and existing.get("provider") != "gemini")


def _write_gemini_batch(summaries: list[dict[str, Any]], paths: list[str]) -> int:
    bundles = [
        research_bundle_detail(summary["bundle_path"])
        for summary in summaries
    ]
    bundles = [bundle for bundle in bundles if bundle]
    prompt = {
        "task": "Create concise editorial research digests for finance video planning and text posts.",
        "requirements": [
            "Return one digest per input bundle_path.",
            "Use only accepted sources.",
            "Prefer Tier 1 and Tier 2 facts; use Tier 3 only as context.",
            "Do not cite or rely on rejected/discovery-only sources.",
            "Rewrite all bullets in original wording.",
            "Keep text_post educational and attribution-based, not advice.",
        ],
        "output_shape": {
            "digests": [
                {
                    "bundle_path": "string matching input",
                    "key_bullets": ["4-7 concise bullets"],
                    "why_it_matters": "1-2 sentences",
                    "caveats": ["1-3 caveats or uncertainty notes"],
                    "watch_items": ["1-3 what-to-watch items"],
                    "text_post": "short social text post, 70-130 words",
                    "confidence": "high|medium|low",
                }
            ]
        },
        "bundles": [_digest_input(bundle) for bundle in bundles],
    }
    payload = generate_json(prompt, DIGEST_SYSTEM_INSTRUCTION)
    by_path = {
        str(item.get("bundle_path")): item
        for item in payload.get("digests", [])
        if item.get("bundle_path")
    }
    written = 0
    for bundle in bundles:
        bundle_path = bundle["bundle_path"]
        digest = _normalize_digest(bundle, by_path.get(bundle_path), provider="gemini")
        _write_digest(bundle_path, digest)
        paths.append(str(digest_path(bundle_path)))
        written += 1
    return written


def _write_gemini_manifest_batch(manifest_paths: list[str], paths: list[str]) -> int:
    manifests = [_read_manifest(path) for path in manifest_paths]
    prompt = {
        "task": "Create concise editorial research digests from cleaned script manifests for finance video planning and text posts.",
        "requirements": [
            "Return one digest per input manifest_path.",
            "Use only approved_research, citable_sources, and context_sources.",
            "Prefer Tier 1 and Tier 2 facts; use Tier 3 only as context.",
            "Do not cite or rely on discovery_sources or rejected_sources.",
            "Rewrite all bullets in original wording.",
            "Keep text_post educational and attribution-based, not advice.",
        ],
        "output_shape": {
            "digests": [
                {
                    "manifest_path": "string matching input",
                    "key_bullets": ["4-7 concise bullets"],
                    "why_it_matters": "1-2 sentences",
                    "caveats": ["1-3 caveats or uncertainty notes"],
                    "watch_items": ["1-3 what-to-watch items"],
                    "text_post": "short social text post, 70-130 words",
                    "confidence": "high|medium|low",
                }
            ]
        },
        "manifests": [
            _manifest_digest_input(manifest, path)
            for manifest, path in zip(manifests, manifest_paths, strict=False)
        ],
    }
    payload = generate_json(prompt, DIGEST_SYSTEM_INSTRUCTION)
    by_path = {
        str(item.get("manifest_path")): item
        for item in payload.get("digests", [])
        if item.get("manifest_path")
    }
    written = 0
    for manifest, manifest_path in zip(manifests, manifest_paths, strict=False):
        digest = _normalize_manifest_digest(
            manifest,
            manifest_path,
            by_path.get(str(manifest_path)),
            provider="gemini",
        )
        bundle_path = _manifest_bundle_path(manifest, manifest_path)
        _write_digest(bundle_path, digest)
        paths.append(str(digest_path(bundle_path)))
        written += 1
    return written


def _gemini_single_digest(bundle: dict[str, Any]) -> dict[str, Any]:
    prompt = {
        "task": "Create one concise editorial research digest for finance video planning and text posts.",
        "requirements": [
            "Use only accepted sources.",
            "Prefer Tier 1 and Tier 2 facts; use Tier 3 only as context.",
            "Rewrite all bullets in original wording.",
            "Keep text_post educational and attribution-based, not advice.",
        ],
        "output_shape": {
            "key_bullets": ["4-7 concise bullets"],
            "why_it_matters": "1-2 sentences",
            "caveats": ["1-3 caveats or uncertainty notes"],
            "watch_items": ["1-3 what-to-watch items"],
            "text_post": "short social text post, 70-130 words",
            "confidence": "high|medium|low",
        },
        "bundle": _digest_input(bundle),
    }
    payload = generate_json(prompt, DIGEST_SYSTEM_INSTRUCTION)
    return _normalize_digest(bundle, payload, provider="gemini")


def _gemini_single_manifest_digest(manifest: dict[str, Any], manifest_path: str | Path) -> dict[str, Any]:
    prompt = {
        "task": "Create one concise editorial research digest from a cleaned script manifest for finance video planning and text posts.",
        "requirements": [
            "Use only approved_research, citable_sources, and context_sources.",
            "Prefer Tier 1 and Tier 2 facts; use Tier 3 only as context.",
            "Rewrite all bullets in original wording.",
            "Keep text_post educational and attribution-based, not advice.",
        ],
        "output_shape": {
            "key_bullets": ["4-7 concise bullets"],
            "why_it_matters": "1-2 sentences",
            "caveats": ["1-3 caveats or uncertainty notes"],
            "watch_items": ["1-3 what-to-watch items"],
            "text_post": "short social text post, 70-130 words",
            "confidence": "high|medium|low",
        },
        "manifest": _manifest_digest_input(manifest, manifest_path),
    }
    payload = generate_json(prompt, DIGEST_SYSTEM_INSTRUCTION)
    return _normalize_manifest_digest(manifest, manifest_path, payload, provider="gemini")


def _local_manifest_digest(manifest: dict[str, Any], manifest_path: str | Path) -> dict[str, Any]:
    sources = _manifest_sources(manifest)
    citable = [item for item in sources if _tier(item) <= 2]
    selected = citable[:6] or sources[:6]
    bullets = _dedupe(
        highlight
        for item in selected
        for highlight in item.get("highlights", [])[:2]
        if highlight
    )[:7]
    if not bullets:
        bullets = _dedupe(item.get("title") for item in selected if item.get("title"))[:7]
    ticker = (manifest.get("event") or {}).get("ticker", "Unknown")
    payload = {
        "key_bullets": bullets,
        "why_it_matters": f"{ticker} has {len(citable)} citable source(s) in the cleaned script manifest.",
        "caveats": ["Confirm exact financial, legal, and regulatory claims against Tier 1 sources."],
        "watch_items": ["Whether price and volume follow-through continues.", "Next company filing, earnings update, or management guidance."],
        "text_post": _local_text_post(ticker, bullets, ["Educational only. Not financial advice."]),
        "confidence": "medium" if citable else "low",
    }
    return _normalize_manifest_digest(manifest, manifest_path, payload, provider="local")


def _local_digest(bundle: dict[str, Any]) -> dict[str, Any]:
    accepted = bundle.get("accepted") or []
    citable = [_source_brief(item) for item in accepted if _tier(item) <= 2]
    context = [_source_brief(item) for item in accepted if _tier(item) == 3]
    selected = citable[:6] or context[:6] or [_source_brief(item) for item in accepted[:6]]
    bullets = _dedupe(
        highlight
        for item in selected
        for highlight in item.get("highlights", [])[:2]
        if highlight
    )[:7]
    if not bullets:
        bullets = _dedupe(item["title"] for item in selected if item.get("title"))[:7]
    ticker = (bundle.get("manifest") or {}).get("ticker", "Unknown")
    why_it_matters = (
        f"{ticker} has research context from {len(citable)} citable source(s)"
        if citable
        else f"{ticker} has only context-level or lower-confidence accepted sources so far."
    )
    caveats = ["Confirm exact financial, legal, and regulatory claims against Tier 1 sources."]
    if not citable:
        caveats.append("No Tier 1 or Tier 2 accepted source is available in this bundle.")
    watch_items = ["Next company filing, earnings update, or management guidance.", "Whether volume and price action persist."]
    text_post = _local_text_post(ticker, bullets, caveats)
    return _normalize_digest(
        bundle,
        {
            "key_bullets": bullets,
            "why_it_matters": why_it_matters,
            "caveats": caveats,
            "watch_items": watch_items,
            "text_post": text_post,
            "confidence": "medium" if citable else "low",
        },
        provider="local",
    )


def _normalize_digest(
    bundle: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    provider: str,
) -> dict[str, Any]:
    payload = payload or {}
    manifest = bundle.get("manifest") or {}
    accepted = bundle.get("accepted") or []
    source_counts = {
        "tier_1": sum(1 for item in accepted if _tier(item) == 1),
        "tier_2": sum(1 for item in accepted if _tier(item) == 2),
        "tier_3": sum(1 for item in accepted if _tier(item) == 3),
        "tier_4": sum(1 for item in accepted if _tier(item) >= 4),
    }
    return {
        "digest_version": "research-digest-v1",
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle_path": bundle["bundle_path"],
        "event_id": manifest.get("event_id"),
        "ticker": manifest.get("ticker"),
        "date": manifest.get("date"),
        "source_counts": source_counts,
        "confidence": _string(payload.get("confidence") or ("medium" if source_counts["tier_1"] or source_counts["tier_2"] else "low")),
        "key_bullets": _strings(payload.get("key_bullets"))[:7],
        "why_it_matters": _string(payload.get("why_it_matters")),
        "caveats": _strings(payload.get("caveats"))[:4],
        "watch_items": _strings(payload.get("watch_items"))[:4],
        "text_post": _string(payload.get("text_post")),
        "source_notes": [_source_note(item) for item in accepted[:12]],
    }


def _normalize_manifest_digest(
    manifest: dict[str, Any],
    manifest_path: str | Path,
    payload: dict[str, Any] | None,
    *,
    provider: str,
) -> dict[str, Any]:
    payload = payload or {}
    sources = _manifest_sources(manifest)
    source_counts = {
        "tier_1": sum(1 for item in sources if _tier(item) == 1),
        "tier_2": sum(1 for item in sources if _tier(item) == 2),
        "tier_3": sum(1 for item in sources if _tier(item) == 3),
        "tier_4": sum(1 for item in sources if _tier(item) >= 4),
    }
    event = manifest.get("event") or {}
    return {
        "digest_version": "research-digest-v1",
        "digest_source": "cleaned_script_manifest",
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle_path": str(_manifest_bundle_path(manifest, manifest_path)),
        "manifest_path": str(manifest_path),
        "event_id": event.get("id"),
        "ticker": event.get("ticker"),
        "date": event.get("date"),
        "source_counts": source_counts,
        "confidence": _string(payload.get("confidence") or ("medium" if source_counts["tier_1"] or source_counts["tier_2"] else "low")),
        "key_bullets": _strings(payload.get("key_bullets"))[:7],
        "why_it_matters": _string(payload.get("why_it_matters")),
        "caveats": _strings(payload.get("caveats"))[:4],
        "watch_items": _strings(payload.get("watch_items"))[:4],
        "text_post": _string(payload.get("text_post")),
        "source_notes": [_source_note(item) for item in sources[:12]],
    }


def _digest_input(bundle: dict[str, Any]) -> dict[str, Any]:
    manifest = bundle.get("manifest") or {}
    accepted = bundle.get("accepted") or []
    return {
        "bundle_path": bundle["bundle_path"],
        "ticker": manifest.get("ticker"),
        "event_id": manifest.get("event_id"),
        "date": manifest.get("date"),
        "accepted_sources": [_source_brief(item) for item in accepted[:12]],
    }


def _manifest_digest_input(manifest: dict[str, Any], manifest_path: str | Path) -> dict[str, Any]:
    event = manifest.get("event") or {}
    return {
        "manifest_path": str(manifest_path),
        "bundle_path": str(_manifest_bundle_path(manifest, manifest_path)),
        "ticker": event.get("ticker"),
        "event_id": event.get("id"),
        "date": event.get("date"),
        "approved_sources": [_source_brief(item) for item in _manifest_sources(manifest)[:12]],
        "market_context": manifest.get("market_context") or {},
    }


def _manifest_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    grouped = manifest.get("approved_research") or {}
    sources = []
    for rows in grouped.values():
        sources.extend(rows or [])
    if not sources:
        sources = [*(manifest.get("citable_sources") or []), *(manifest.get("context_sources") or [])]
    return sources


def _manifest_digest_should_run(manifest_path: str | Path, *, use_gemini: bool) -> bool:
    manifest = _read_manifest(manifest_path)
    existing = load_research_digest(_manifest_bundle_path(manifest, manifest_path))
    if not existing:
        return True
    if existing.get("digest_source") != "cleaned_script_manifest":
        return True
    return bool(use_gemini and existing.get("provider") != "gemini")


def _manifest_bundle_path(manifest: dict[str, Any], manifest_path: str | Path) -> Path:
    review = manifest.get("research_review") or {}
    bundle_path = review.get("bundle_path")
    if bundle_path:
        return Path(bundle_path)
    return Path(manifest_path).parent


def _read_manifest(manifest_path: str | Path) -> dict[str, Any]:
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def _source_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title"),
        "source": item.get("source") or item.get("author") or item.get("provider"),
        "url": item.get("url"),
        "tier": _tier(item),
        "claim_use_policy": item.get("claim_use_policy"),
        "requires_confirmation": item.get("requires_confirmation"),
        "highlights": (item.get("highlights") or [])[:3],
    }


def _source_note(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title"),
        "source": item.get("source") or item.get("author") or item.get("provider"),
        "tier": _tier(item),
        "url": item.get("url"),
    }


def _tier(item: dict[str, Any]) -> int:
    quality = item.get("source_quality") or {}
    return int(item.get("source_tier") or quality.get("tier") or 4)


def _local_text_post(ticker: str, bullets: list[str], caveats: list[str]) -> str:
    lead = bullets[0] if bullets else "The research bundle has limited source detail."
    support = " ".join(bullets[1:3])
    caveat = caveats[0] if caveats else "Treat this as educational context, not advice."
    return f"{ticker} is worth a closer look today: {lead} {support} {caveat}"


def _dedupe(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = _string(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_string(item) for item in value if _string(item)]
    if value:
        return [_string(value)]
    return []


def _string(value: Any) -> str:
    return str(value or "").strip()


def _write_digest(bundle_path: str | Path, digest: dict[str, Any]) -> None:
    path = digest_path(bundle_path)
    path.write_text(json.dumps(digest, indent=2, sort_keys=True), encoding="utf-8")
