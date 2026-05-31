from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from models.database import query


try:
    from langchain_core.documents import Document as LangChainDocument
except Exception:  # pragma: no cover - optional integration
    LangChainDocument = None


@dataclass(frozen=True)
class LocalDocument:
    page_content: str
    metadata: dict[str, Any]


Document = LangChainDocument or LocalDocument


def documents_for_event(event_id: int) -> list[Any]:
    rows = query(
        """
        SELECT provider, title, url, source, published_at, highlights_json, metadata_json
        FROM research_sources
        WHERE event_id = ?
        ORDER BY id
        """,
        (event_id,),
    )
    docs = []
    for index, row in enumerate(rows, start=1):
        highlights = _json_list(row.get("highlights_json"))
        metadata = _json_dict(row.get("metadata_json"))
        source_quality = metadata.get("source_quality") or {}
        content = "\n".join(
            item
            for item in [
                str(row.get("title") or ""),
                str(row.get("source") or ""),
                *[str(highlight) for highlight in highlights],
            ]
            if item
        )
        docs.append(
            Document(
                page_content=content,
                metadata={
                    "event_id": event_id,
                    "source_id": f"S{index}",
                    "provider": row.get("provider"),
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "source": row.get("source"),
                    "published_at": row.get("published_at"),
                    "source_tier": metadata.get("source_tier") or source_quality.get("tier"),
                    "source_tier_label": metadata.get("source_tier_label")
                    or source_quality.get("label"),
                    "claim_use_policy": metadata.get("claim_use_policy")
                    or source_quality.get("claim_use_policy"),
                },
            )
        )
    return docs


def retrieve_context(query_text: str, *, event_id: int | None = None, k: int = 5) -> list[dict[str, Any]]:
    docs = []
    if event_id is not None:
        docs = documents_for_event(event_id)
    else:
        event_rows = query("SELECT id FROM events ORDER BY score DESC, created_at DESC LIMIT 10")
        for row in event_rows:
            docs.extend(documents_for_event(int(row["id"])))
    scored = sorted(
        ((score_document(query_text, _page_content(doc)), doc) for doc in docs),
        key=lambda item: item[0],
        reverse=True,
    )
    return [
        {
            "score": score,
            "content": _page_content(doc),
            "metadata": dict(getattr(doc, "metadata", {}) or {}),
        }
        for score, doc in scored[: max(1, k)]
        if score > 0
    ]


def score_document(query_text: str, content: str) -> int:
    query_terms = set(_tokens(query_text))
    if not query_terms:
        return 0
    content_terms = set(_tokens(content))
    return len(query_terms & content_terms)


def _tokens(value: str) -> Iterable[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2]


def _page_content(doc: Any) -> str:
    return str(getattr(doc, "page_content", ""))


def _json_list(value: str | None) -> list[Any]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _json_dict(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
