from __future__ import annotations

from datetime import UTC, datetime

from media_engine.templates.selector import (
    apply_template_to_scenes,
    build_template_context,
    load_video_template,
    load_video_templates,
    record_template_usage,
    select_video_template,
)
from media_engine.story_schema import PriceCard, Scene, Story, StorySection
from models.database import init_db


def test_all_starter_video_templates_load_and_end_with_outro():
    templates = load_video_templates()

    assert {template.template_id for template in templates} == {
        "mover_quick_hit",
        "why_stock_moved",
        "earnings_snapshot",
        "analyst_call",
        "volume_alert",
        "risk_radar",
        "bull_vs_bear",
        "three_things",
    }
    for template in templates:
        assert len(template.scene_slots) >= 5
        assert template.scene_slots[-1].card_type == "outro_disclaimer_card"
        for slot in template.scene_slots:
            assert slot.card_type
            assert slot.narration_role
            assert slot.motion
            assert slot.caption_style


def test_template_selector_uses_story_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()

    assert _select({"has_earnings": True, "story_type": "earnings"}) == "earnings_snapshot"
    assert _select({"has_analyst": True, "story_type": "analyst"}) == "analyst_call"
    assert _select({"volume_ratio": 3.1, "story_type": "volume"}) == "volume_alert"
    assert _select({"has_risk": True, "story_type": "risk"}) == "risk_radar"
    assert _select({"story_type": "general"}) == "three_things"


def test_template_selector_rotates_away_from_recent_ticker_usage(tmp_path, monkeypatch):
    db = tmp_path / "market_brief_agents.db"
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(db))
    init_db(db)
    now = datetime(2026, 5, 30, tzinfo=UTC)
    context = {
        "ticker": "ADBE",
        "story_type": "news",
        "has_news": True,
        "price_change_pct": 4.2,
    }
    first = select_video_template(context, db_path=db, now=now)
    record_template_usage(first, context, artifact_stem="ADBE_1", db_path=db, now=now)

    second = select_video_template(context, db_path=db, now=now)

    assert first.selected_template_id == "why_stock_moved"
    assert second.selected_template_id == "mover_quick_hit"


def test_apply_template_to_scenes_orders_slots_and_falls_back_safely():
    story = _story()
    context = build_template_context(story=story, scenes=_scenes(), assets={}, artifact_stem="ADBE_1")
    template = load_video_template("why_stock_moved")

    scenes = apply_template_to_scenes(_scenes(), template, story, context)

    assert [scene.card_type for scene in scenes] == [
        "hook_card",
        "news_headline_card",
        "price_move_card",
        "three_bullet_card",
        "risk_card",
        "outro_disclaimer_card",
    ]
    assert scenes[-1].scene_type == "outro"
    assert scenes[-1].subheadline == story.disclaimer


def _select(context: dict) -> str:
    payload = {"ticker": "ADBE", **context}
    return select_video_template(payload).selected_template_id


def _story() -> Story:
    return Story(
        ticker="ADBE",
        company="Adobe",
        date="2026-05-29",
        hook="Adobe jumped 6% today",
        price_card=PriceCard(price="$256.79", change_pct="+6.4%", direction="up"),
        sections=[
            StorySection(
                type="catalyst",
                title="Why it moved",
                bullets=["Investors reacted to a new catalyst", "Volume improved"],
            ),
            StorySection(
                type="risk",
                title="Risk check",
                bullets=["The bigger trend still matters"],
            ),
        ],
        chart_insight="Still weak on the 90-day chart",
        takeaway="Earnings decide if the rally lasts",
    )


def _scenes() -> list[Scene]:
    return [
        Scene(
            scene_type="hook",
            duration=4,
            headline="Adobe jumped",
            narration="Adobe jumped today.",
            caption_text="Adobe jumped",
        ),
        Scene(
            scene_type="news",
            duration=4,
            headline="Catalyst",
            subheadline="Investors reacted to fresh news",
            bullets=["Fresh catalyst", "Volume improved"],
            narration="Investors reacted to fresh news.",
            caption_text="Catalyst",
        ),
        Scene(
            scene_type="price_action",
            duration=4,
            headline="Price action",
            narration="The price move stood out.",
            caption_text="Price action",
        ),
        Scene(
            scene_type="risk",
            duration=4,
            headline="Risk check",
            narration="The bigger trend still matters.",
            caption_text="Risk check",
        ),
    ]
