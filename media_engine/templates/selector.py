from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from media_engine.schemas.templates import SceneSlot, TemplateSelectionResult, VideoTemplate
from media_engine.story_schema import Scene, Story, StorySection
from models.database import connect, init_db


TEMPLATE_DIR = Path(__file__).parent / "video"
DEFAULT_TEMPLATE_ID = "three_things"

CARD_TYPE_TO_SCENE_TYPES = {
    "hook_card": ["hook"],
    "price_move_card": ["price_action", "price_card"],
    "news_headline_card": ["news", "context", "bullet_reveal"],
    "chart_card": ["chart", "timeline"],
    "three_bullet_card": ["bullet_reveal", "context", "news", "company", "industry"],
    "earnings_card": ["earnings", "financials"],
    "analyst_card": ["analyst"],
    "volume_spike_card": ["price_action", "price_card", "chart"],
    "risk_card": ["risk", "context"],
    "bull_bear_card": ["comparison", "risk", "context"],
    "takeaway_card": ["takeaway", "conclusion"],
    "outro_disclaimer_card": ["outro"],
}

CARD_TYPE_TO_SCENE_TYPE = {
    "hook_card": "hook",
    "price_move_card": "price_action",
    "news_headline_card": "news",
    "chart_card": "chart",
    "three_bullet_card": "bullet_reveal",
    "earnings_card": "earnings",
    "analyst_card": "analyst",
    "volume_spike_card": "price_action",
    "risk_card": "risk",
    "bull_bear_card": "comparison",
    "takeaway_card": "conclusion",
    "outro_disclaimer_card": "outro",
}

ROLE_TO_SCENE_TYPES = {
    "hook": ["hook"],
    "price_move": ["price_action", "price_card"],
    "catalyst": ["news", "context", "bullet_reveal"],
    "evidence": ["bullet_reveal", "context", "news"],
    "chart": ["chart", "timeline"],
    "earnings": ["earnings", "financials"],
    "analyst": ["analyst"],
    "volume": ["price_action", "price_card", "chart"],
    "risk": ["risk", "context"],
    "bull_bear": ["comparison", "risk", "context"],
    "takeaway": ["takeaway", "conclusion"],
    "disclaimer": ["outro"],
}

CARD_VISUAL_REQUIREMENTS = {
    "hook_card": ["company_logo"],
    "price_move_card": ["price_move"],
    "news_headline_card": ["news_headline"],
    "chart_card": ["price_chart"],
    "three_bullet_card": ["news_headline"],
    "earnings_card": ["earnings", "guidance"],
    "analyst_card": ["news_headline"],
    "volume_spike_card": ["volume_spike"],
    "risk_card": ["risk_warning"],
    "bull_bear_card": ["risk_warning", "price_chart"],
    "takeaway_card": ["news_headline"],
    "outro_disclaimer_card": [],
}


def load_video_templates(directory: Path | None = None) -> list[VideoTemplate]:
    template_dir = directory or TEMPLATE_DIR
    templates = []
    for path in sorted(template_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        templates.append(VideoTemplate.model_validate(payload))
    return sorted(templates, key=lambda item: item.template_id)


def load_video_template(template_id: str, directory: Path | None = None) -> VideoTemplate:
    templates = {template.template_id: template for template in load_video_templates(directory)}
    try:
        return templates[template_id]
    except KeyError as exc:
        raise ValueError(f"Unknown video template: {template_id}") from exc


def build_template_context(
    *,
    story: Story,
    event: Mapping[str, Any] | None = None,
    scenes: list[Scene] | None = None,
    assets: Mapping[str, str] | None = None,
    script_row: Mapping[str, Any] | None = None,
    artifact_stem: str | None = None,
) -> dict[str, Any]:
    event = event or {}
    script_row = script_row or {}
    scenes = scenes or []
    assets = assets or {}
    analysis = _json_dict(event.get("analysis_json"))
    text = _search_text(story, event, scenes, script_row, analysis)
    analysis_text = json.dumps(analysis, sort_keys=True).lower()
    event_type = str(event.get("event_type") or "").lower()
    price_change_pct = _parse_percent(story.price_card.change_pct)
    if price_change_pct is None:
        price_change_pct = _first_float(analysis, ("price_change_pct", "change_percent"))
    volume_ratio = _first_float(
        analysis,
        ("volume_ratio", "relative_volume", "volume_multiple", "volume_vs_average"),
    )
    has_earnings = (
        "earnings" in event_type
        or any(scene.scene_type in {"earnings", "financials"} for scene in scenes)
        or _has_any(analysis_text, ("earnings", "eps", "revenue", "guidance"))
    )
    has_analyst = _has_any(
        text,
        ("analyst", "upgrade", "downgrade", "price target", "initiated", "rating"),
    )
    has_risk = (
        _has_any(text, ("risk", "warning", "lawsuit", "probe", "miss", "cut guidance"))
        or any(section.type == "risk" for section in story.sections)
        or any(scene.scene_type == "risk" for scene in scenes)
    )
    has_news = (
        bool(event.get("reason"))
        or _has_any(text, ("news", "reported", "announced", "filing", "press release"))
        or any(scene.scene_type in {"news", "context", "bullet_reveal"} for scene in scenes)
    )
    mixed_sentiment = has_risk and (
        price_change_pct is not None
        or has_earnings
        or _has_any(text, ("but", "however", "despite", "while"))
    )
    story_type = _story_type(
        has_earnings=has_earnings,
        has_analyst=has_analyst,
        volume_ratio=volume_ratio,
        has_risk=has_risk,
        has_news=has_news,
        price_change_pct=price_change_pct,
    )
    chart_path = assets.get("chart") or next((scene.chart_path for scene in scenes if scene.chart_path), None)
    source_label = story.sources[0] if story.sources else None
    return {
        "artifact_stem": artifact_stem,
        "ticker": story.ticker,
        "company_name": story.company,
        "headline": story.hook,
        "price": story.price_card.price,
        "price_change_pct": price_change_pct,
        "price_change_label": story.price_card.change_pct,
        "volume_ratio": volume_ratio,
        "chart_path": chart_path,
        "catalyst": _section_text(story, "catalyst") or event.get("reason"),
        "risk": _section_text(story, "risk"),
        "takeaway": story.takeaway,
        "source_label": source_label,
        "as_of": story.date,
        "story_type": story_type,
        "has_earnings": has_earnings,
        "has_analyst": has_analyst,
        "has_risk": has_risk,
        "has_news": has_news,
        "mixed_sentiment": mixed_sentiment,
    }


def select_video_template(
    context: Mapping[str, Any],
    *,
    templates: list[VideoTemplate] | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> TemplateSelectionResult:
    templates = templates or load_video_templates()
    template_ids = {template.template_id for template in templates}
    candidates = _candidate_template_ids(context)
    candidates = [template_id for template_id in candidates if template_id in template_ids]
    warnings = []
    if not candidates:
        candidates = [DEFAULT_TEMPLATE_ID]
        warnings.append("No compatible template candidates found; using three_things.")
    selected = _least_recently_used(candidates, context, db_path=db_path, now=now)
    story_type = str(context.get("story_type") or "general")
    reason = _selection_reason(selected, candidates, context)
    return TemplateSelectionResult(
        selected_template_id=selected,
        candidate_template_ids=candidates,
        story_type=story_type,
        reason=reason,
        warnings=warnings,
    )


def apply_template_to_scenes(
    scenes: list[Scene],
    template: VideoTemplate,
    story: Story,
    context: Mapping[str, Any] | None = None,
) -> list[Scene]:
    context = context or {}
    output: list[Scene] = []
    used_indices: set[int] = set()
    for slot in template.scene_slots:
        card_type = slot.card_type
        slot_warnings = _slot_warnings(slot, context)
        if slot_warnings and card_type not in {"hook_card", "outro_disclaimer_card"}:
            card_type = "three_bullet_card"
        scene = _scene_for_slot(scenes, used_indices, slot, card_type, story, context)
        requirements = _requirements_for_card(card_type, scene.visual_requirements)
        chart_path = scene.chart_path or (str(context["chart_path"]) if context.get("chart_path") else None)
        if card_type != "chart_card" and not scene.chart_path:
            chart_path = None
        update = {
            "scene_type": CARD_TYPE_TO_SCENE_TYPE.get(card_type, scene.scene_type),
            "template_id": template.template_id,
            "slot_id": slot.slot_id,
            "card_type": card_type,
            "visual_style": slot.visual_style,
            "motion": slot.motion,
            "caption_style": slot.caption_style,
            "chart_path": chart_path,
            "visual_requirements": requirements,
            "template_warnings": [*scene.template_warnings, *slot_warnings],
        }
        output.append(scene.model_copy(deep=True, update=update))
    _apply_progress(output)
    return output


def record_template_usage(
    selection: TemplateSelectionResult,
    context: Mapping[str, Any],
    *,
    artifact_stem: str | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> None:
    used_at = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    row = (
        selection.selected_template_id,
        context.get("ticker"),
        context.get("story_type"),
        artifact_stem or context.get("artifact_stem"),
        used_at,
    )
    try:
        _insert_usage(row, db_path=db_path)
    except sqlite3.OperationalError:
        init_db(db_path)
        _insert_usage(row, db_path=db_path)


def _insert_usage(row: tuple[Any, ...], *, db_path: Path | None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO template_usage (template_id, ticker, story_type, artifact_stem, used_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            row,
        )


def _candidate_template_ids(context: Mapping[str, Any]) -> list[str]:
    price_change_pct = context.get("price_change_pct")
    volume_ratio = context.get("volume_ratio")
    if context.get("has_earnings"):
        return ["earnings_snapshot", "bull_vs_bear"]
    if context.get("has_analyst"):
        return ["analyst_call", "why_stock_moved"]
    if _as_float(volume_ratio, 0.0) >= 2.5:
        return ["volume_alert", "mover_quick_hit"]
    if context.get("has_risk"):
        return ["risk_radar", "why_stock_moved"]
    if context.get("mixed_sentiment"):
        return ["bull_vs_bear", "three_things"]
    if context.get("has_news") and price_change_pct is not None:
        return ["why_stock_moved", "mover_quick_hit"]
    if abs(_as_float(price_change_pct, 0.0)) >= 3.0:
        return ["mover_quick_hit", "three_things"]
    return [DEFAULT_TEMPLATE_ID]


def _least_recently_used(
    candidates: list[str],
    context: Mapping[str, Any],
    *,
    db_path: Path | None,
    now: datetime | None,
) -> str:
    if len(candidates) == 1:
        return candidates[0]
    now = now or datetime.now(UTC)
    rows = _usage_rows(candidates, db_path=db_path)
    ticker = str(context.get("ticker") or "")
    recent_cutoff = now - timedelta(days=7)
    day_key = now.date().isoformat()
    recent_for_ticker = set()
    daily_counts = {template_id: 0 for template_id in candidates}
    last_used: dict[str, datetime] = {}
    for row in rows:
        used_at = _parse_dt(row.get("used_at"))
        template_id = str(row.get("template_id") or "")
        if not used_at or template_id not in daily_counts:
            continue
        if str(row.get("ticker") or "") == ticker and used_at >= recent_cutoff:
            recent_for_ticker.add(template_id)
        if used_at.date().isoformat() == day_key:
            daily_counts[template_id] += 1
        if template_id not in last_used or used_at > last_used[template_id]:
            last_used[template_id] = used_at
    viable = [item for item in candidates if item not in recent_for_ticker]
    if not viable:
        viable = candidates[:]
    under_daily_limit = [item for item in viable if daily_counts.get(item, 0) < 2]
    if under_daily_limit:
        viable = under_daily_limit
    candidate_rank = {template_id: index for index, template_id in enumerate(candidates)}
    return min(
        viable,
        key=lambda template_id: (
            template_id in last_used,
            last_used.get(template_id, datetime.min.replace(tzinfo=UTC)),
            candidate_rank[template_id],
        ),
    )


def _usage_rows(candidates: list[str], *, db_path: Path | None) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in candidates)
    try:
        with connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT template_id, ticker, story_type, artifact_stem, used_at
                FROM template_usage
                WHERE template_id IN ({placeholders})
                """,
                tuple(candidates),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _selection_reason(
    selected: str,
    candidates: list[str],
    context: Mapping[str, Any],
) -> str:
    story_type = context.get("story_type") or "general"
    if selected != candidates[0]:
        return f"Selected {selected} for {story_type}; rotated away from recent candidates."
    return f"Selected {selected} for {story_type} story signals."


def _slot_warnings(slot: SceneSlot, context: Mapping[str, Any]) -> list[str]:
    missing = [field for field in slot.required_fields if not _has_context_value(context, field)]
    if not missing:
        return []
    fields = ", ".join(missing)
    return [f"Required template data missing for {slot.slot_id}: {fields}; using safe fallback card."]


def _scene_for_slot(
    scenes: list[Scene],
    used_indices: set[int],
    slot: SceneSlot,
    card_type: str,
    story: Story,
    context: Mapping[str, Any],
) -> Scene:
    if card_type != "outro_disclaimer_card":
        matched = _matching_scene(scenes, used_indices, slot, card_type)
        if matched:
            return matched
    return _fallback_scene(slot, card_type, story, context)


def _matching_scene(
    scenes: list[Scene],
    used_indices: set[int],
    slot: SceneSlot,
    card_type: str,
) -> Scene | None:
    preferred_types = [
        *CARD_TYPE_TO_SCENE_TYPES.get(card_type, []),
        *ROLE_TO_SCENE_TYPES.get(slot.narration_role, []),
    ]
    for scene_type in dict.fromkeys(preferred_types):
        for index, scene in enumerate(scenes):
            if index in used_indices:
                continue
            if scene.scene_type == scene_type:
                used_indices.add(index)
                return scene
    for index, scene in enumerate(scenes):
        if index in used_indices:
            continue
        if card_type == "chart_card" and (scene.chart_path or "price_chart" in scene.visual_requirements):
            used_indices.add(index)
            return scene
        if card_type == "three_bullet_card" and scene.bullets:
            used_indices.add(index)
            return scene
    return None


def _fallback_scene(
    slot: SceneSlot,
    card_type: str,
    story: Story,
    context: Mapping[str, Any],
) -> Scene:
    scene_type = CARD_TYPE_TO_SCENE_TYPE.get(card_type, "bullet_reveal")
    headline, subheadline, bullets, narration = _fallback_copy(slot, card_type, story, context)
    return Scene(
        scene_type=scene_type,
        duration=max(3.0, slot.min_duration),
        headline=headline,
        subheadline=subheadline,
        bullets=bullets,
        narration=narration,
        caption_text=headline,
        chart_path=str(context["chart_path"]) if card_type == "chart_card" and context.get("chart_path") else None,
        visual_requirements=CARD_VISUAL_REQUIREMENTS.get(card_type, []),
        template_warnings=[f"Generated fallback scene for template slot {slot.slot_id}."],
    )


def _fallback_copy(
    slot: SceneSlot,
    card_type: str,
    story: Story,
    context: Mapping[str, Any],
) -> tuple[str, str, list[str], str]:
    if card_type == "hook_card":
        headline = story.hook
        subheadline = f"{story.company} | {story.price_card.change_pct}"
        return headline, subheadline, [], f"{story.hook}. {subheadline}."
    if card_type == "price_move_card":
        headline = f"{story.ticker} {story.price_card.change_pct}"
        subheadline = f"{story.price_card.price} {story.price_card.period} reaction"
        return headline, subheadline, [], f"{headline}. {subheadline}."
    if card_type == "chart_card":
        headline = "Chart check"
        subheadline = story.chart_insight
        return headline, subheadline, [], f"{headline}. {subheadline}."
    if card_type == "earnings_card":
        section = _first_section(story, ("catalyst", "context"))
        bullets = _section_bullets(section) or _all_bullets(story)
        headline = "Earnings snapshot"
        subheadline = section.title if section else story.hook
        return headline, subheadline, bullets, _narration(headline, subheadline, bullets)
    if card_type == "analyst_card":
        headline = "Analyst signal"
        subheadline = str(context.get("catalyst") or story.hook)
        return headline, subheadline, [], f"{headline}. {subheadline}."
    if card_type == "volume_spike_card":
        ratio = context.get("volume_ratio")
        ratio_text = f"{float(ratio):.1f}x normal volume" if ratio else "Volume above normal"
        headline = "Volume alert"
        return headline, ratio_text, [], f"{headline}. {ratio_text}."
    if card_type == "risk_card":
        section = _first_section(story, ("risk", "context"))
        bullets = _section_bullets(section) or [str(context.get("risk") or "Confirmation still matters")]
        headline = section.title if section else "Risk check"
        subheadline = bullets[0]
        return headline, subheadline, bullets[1:3], _narration(headline, subheadline, bullets)
    if card_type == "bull_bear_card":
        catalyst = str(context.get("catalyst") or story.hook)
        risk = str(context.get("risk") or "The setup still needs confirmation.")
        headline = "Bull vs bear"
        return headline, "Two-sided setup", [catalyst, risk], _narration(headline, "", [catalyst, risk])
    if card_type == "takeaway_card":
        headline = "Takeaway"
        subheadline = story.takeaway
        return headline, subheadline, [], f"{headline}. {subheadline}."
    if card_type == "outro_disclaimer_card":
        headline = "Market Brief Agents"
        subheadline = story.disclaimer
        return headline, subheadline, [], f"{story.disclaimer} Follow Market Brief Agents for market context."
    section = _first_section(story, ("catalyst", "context", "watch", "risk"))
    bullets = _section_bullets(section) or _all_bullets(story)
    headline = section.title if section else (story.hook or slot.narration_role.replace("_", " ").title())
    subheadline = str(context.get("catalyst") or story.takeaway)
    return headline, subheadline, bullets[:3], _narration(headline, subheadline, bullets[:3])


def _requirements_for_card(card_type: str, existing: list[str]) -> list[str]:
    merged = [*existing, *CARD_VISUAL_REQUIREMENTS.get(card_type, [])]
    return list(dict.fromkeys(item for item in merged if item))


def _apply_progress(scenes: list[Scene]) -> None:
    total = sum(scene.duration for scene in scenes) or 1.0
    elapsed = 0.0
    for scene in scenes:
        scene.progress_start = elapsed / total
        elapsed += scene.duration
        scene.progress_end = elapsed / total


def _story_type(
    *,
    has_earnings: bool,
    has_analyst: bool,
    volume_ratio: float | None,
    has_risk: bool,
    has_news: bool,
    price_change_pct: float | None,
) -> str:
    if has_earnings:
        return "earnings"
    if has_analyst:
        return "analyst"
    if _as_float(volume_ratio, 0.0) >= 2.5:
        return "volume"
    if has_risk:
        return "risk"
    if price_change_pct is not None and abs(price_change_pct) >= 3.0:
        return "price_move"
    if has_news:
        return "news"
    return "general"


def _search_text(
    story: Story,
    event: Mapping[str, Any],
    scenes: list[Scene],
    script_row: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> str:
    parts = [
        story.hook,
        story.chart_insight,
        story.takeaway,
        event.get("event_type"),
        event.get("reason"),
        script_row.get("title"),
        script_row.get("script"),
        json.dumps(analysis, sort_keys=True),
    ]
    for section in story.sections:
        parts.extend([section.type, section.title, *section.bullets])
    for scene in scenes:
        parts.extend([scene.scene_type, scene.headline, scene.subheadline, scene.narration, *scene.bullets])
    return " ".join(str(part or "") for part in parts).lower()


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_percent(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0))


def _first_float(payload: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                if key in keys:
                    parsed = _try_float(value)
                    if parsed is not None:
                        return parsed
                if isinstance(value, (Mapping, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _try_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float) -> float:
    parsed = _try_float(value)
    return default if parsed is None else parsed


def _has_context_value(context: Mapping[str, Any], field: str) -> bool:
    value = context.get(field)
    if field == "headline":
        value = value or context.get("catalyst")
    if field == "price_change_pct":
        return context.get("price_change_pct") is not None or bool(context.get("price_change_label"))
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _section_text(story: Story, section_type: str) -> str | None:
    section = _first_section(story, (section_type,), fallback=False)
    if not section:
        return None
    return " ".join([section.title, *section.bullets]).strip()


def _first_section(
    story: Story,
    types: tuple[str, ...],
    *,
    fallback: bool = True,
) -> StorySection | None:
    for section in story.sections:
        if section.type in types:
            return section
    return story.sections[0] if fallback and story.sections else None


def _section_bullets(section: StorySection | None) -> list[str]:
    return list(section.bullets) if section else []


def _all_bullets(story: Story) -> list[str]:
    bullets: list[str] = []
    for section in story.sections:
        bullets.extend(section.bullets)
    return bullets[:3] or [story.chart_insight, story.takeaway]


def _narration(headline: str, subheadline: str, bullets: list[str]) -> str:
    parts = [headline, subheadline, *bullets]
    return ". ".join(str(part).strip(". ") for part in parts if str(part or "").strip()) + "."


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
