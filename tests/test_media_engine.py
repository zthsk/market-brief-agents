from pathlib import Path
import json

from media_engine.captions import captions_for_scenes, captions_to_srt
from media_engine.quality import build_quality_report
from media_engine.production import (
    build_production_plan,
    build_sync_report,
    caption_beats_for_scene,
)
from media_engine.renderer import _package_with_card_bullets, _stretch_scenes_to_audio, render_video_bundle
from media_engine.scene_assets import render_scene_assets
from media_engine.scene_builder import build_scenes
from media_engine.script_schema import GeneratedScriptPackage
from media_engine.story_builder import build_story
from media_engine.story_schema import PriceCard, Scene, Story, StorySection
from models.database import connect, init_db, query
from services.voice_profile import MARKET_BRIEFING_VOICE, briefing_tts_prompt, scene_transcript


def test_story_schema_trims_long_visual_text():
    story = Story(
        ticker="ADBE",
        company="Adobe",
        date="2026-05-29",
        hook="Adobe moved sharply because a very long catalyst sentence should not become a full paragraph",
        price_card=PriceCard(price="$256.79", change_pct="+6.4%", direction="up"),
        sections=[
            StorySection(
                type="catalyst",
                title="Why it moved today",
                bullets=["Michael Burry disclosed an Adobe position and traders reacted quickly"],
            )
        ],
        chart_insight="Still down over the last 90 days despite the move",
        takeaway="The rally is real, but earnings decide if it lasts",
    )

    assert len(story.hook.split()) <= 12
    assert len(story.sections[0].bullets[0].split()) <= 9


def test_scene_builder_uses_required_narrative_order():
    story = _story()
    scenes = build_scenes(story, chart_path="storage/assets/1/chart.png")

    assert [scene.scene_type for scene in scenes] == [
        "hook",
        "price_card",
        "bullet_reveal",
        "context",
        "chart",
        "bullet_reveal",
        "takeaway",
    ]
    assert scenes[0].progress_start == 0
    assert scenes[-1].progress_end == 1
    assert 60 <= sum(scene.duration for scene in scenes) <= 75
    assert "outro" not in {scene.scene_type for scene in scenes}


def test_caption_timing_and_srt_export():
    scenes = [
        Scene(
            scene_type="hook",
            duration=4,
            headline="Adobe jumped today",
            narration="Adobe jumped today.",
            caption_text="Adobe jumped today",
        )
    ]

    captions = captions_for_scenes(scenes)
    srt = captions_to_srt(captions)

    assert captions[0].start == 0
    assert captions[0].end == 4
    assert "00:00:00,000 --> 00:00:04,000" in srt


def test_marketbrief_voice_profile_formats_scene_transcript():
    scenes = build_scenes(_story(), chart_path=None)
    transcript = scene_transcript(scenes)
    prompt = briefing_tts_prompt(transcript)

    assert "[curious, crisp hook]" in transcript
    assert "[slightly cautious context]" in transcript
    assert "Follow Market Brief Agents" not in transcript
    assert "145 to 165 words per minute" in prompt
    assert "Do not sound robotic" in prompt
    assert "hype trader" in prompt
    assert MARKET_BRIEFING_VOICE["recommended_gemini_voice"] == "Kore"


def test_scene_timeline_stretches_to_audio_duration():
    scenes = build_scenes(_story(), chart_path=None)

    _stretch_scenes_to_audio(scenes, 67.0)

    assert sum(scene.duration for scene in scenes) >= 67.0
    assert scenes[-1].progress_end == 1


def test_production_plan_uses_measured_audio_duration_and_padding():
    scene = Scene(
        scene_type="hook",
        duration=4,
        headline="Adobe jumped",
        narration="Adobe jumped today. The catalyst matters.",
        caption_text="Adobe jumped",
        audio_path="outputs/review/ADBE/audio/scene.wav",
        audio_duration=2.0,
        visual_requirements=["price_move"],
    )

    plan = build_production_plan(
        video_id="ADBE_2026-05-29_1",
        output_video_path="videos/ADBE_2026-05-29_1.mp4",
        scenes=[scene],
        assets={},
        tts_configured=True,
        audio_generation_attempted=True,
    )

    timing = plan.scenes[0]
    assert timing.measured_audio_duration == 2.0
    assert timing.padding_after == 0.35
    assert timing.final_duration == 2.35
    assert timing.caption_beats[0].start >= timing.start
    assert timing.caption_beats[-1].end <= timing.end


def test_production_plan_estimates_duration_when_audio_missing():
    scene = Scene(
        scene_type="news",
        duration=4,
        headline="Catalyst",
        narration="A catalyst changed the setup and investors reacted quickly.",
        caption_text="Catalyst",
        visual_requirements=[],
    )

    plan = build_production_plan(
        video_id="NOW_2026-05-29_23",
        output_video_path="videos/NOW_2026-05-29_23.mp4",
        scenes=[scene],
        tts_configured=False,
    )

    timing = plan.scenes[0]
    assert timing.measured_audio_duration is None
    assert timing.estimated_audio_duration is not None
    assert "TTS unavailable" in " ".join(timing.warnings)
    assert "Scene has no visual requirements" in " ".join(timing.warnings)


def test_caption_beats_split_long_narration_inside_scene_bounds():
    scene = Scene(
        scene_type="news",
        duration=6,
        headline="Catalyst",
        narration=(
            "First, the company gave investors a cleaner reason to revisit the story. "
            "Then volume confirmed that traders were paying attention. "
            "But the next update still matters."
        ),
        caption_text="Catalyst",
    )

    beats = caption_beats_for_scene(
        scene,
        scene_index=0,
        scene_start=10.0,
        narration_duration=6.0,
        scene_end=16.25,
    )

    assert len(beats) > 1
    assert beats[0].start >= 10.0
    assert beats[-1].end <= 16.25
    assert beats[-1].end == 16.0


def test_sync_report_fails_when_content_duration_drifts():
    scene = Scene(
        scene_type="hook",
        duration=4,
        headline="Adobe jumped",
        narration="Adobe jumped today.",
        caption_text="Adobe jumped",
        audio_path="scene.wav",
        audio_duration=2.0,
        visual_requirements=["price_move"],
    )
    plan = build_production_plan(
        video_id="ADBE_2026-05-29_1",
        output_video_path="videos/ADBE_2026-05-29_1.mp4",
        scenes=[scene],
        tts_configured=True,
        audio_generation_attempted=True,
    )

    report = build_sync_report(
        video_id=plan.video_id,
        plan=plan,
        content_video_duration=plan.total_duration + 0.5,
    )

    assert report.passed is False
    assert report.drift_seconds == 0.5
    assert "drift" in report.warnings[0]


def test_production_plan_supports_continuous_full_audio():
    scenes = [
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
            narration="Investors reacted to the catalyst.",
            caption_text="Catalyst",
        ),
    ]

    plan = build_production_plan(
        video_id="ADBE_2026-05-29_1",
        output_video_path="videos/ADBE_2026-05-29_1.mp4",
        scenes=scenes,
        narration_audio_path="storage/audio/ADBE_2026-05-29_1.wav",
        narration_audio_duration=10.0,
    )
    report = build_sync_report(
        video_id=plan.video_id,
        plan=plan,
        content_video_duration=plan.total_duration,
    )

    assert plan.audio_mode == "continuous_audio"
    assert plan.narration_audio_duration == 10.0
    assert all(scene.timing_source == "continuous_audio" for scene in plan.scenes)
    assert plan.total_duration > 10.0
    assert report.passed is True


def test_quality_report_warns_for_missing_video():
    report = build_quality_report(
        Path("missing.mp4"),
        _story(),
        build_scenes(_story(), chart_path=None),
        [],
        max_duration=60,
    )

    assert report.passed is False
    assert "Captions are missing." in report.warnings
    assert "Audio is missing." in report.warnings


def test_story_builder_uses_event_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe Inc.')")
        conn.execute(
            """
            INSERT INTO daily_prices (ticker, date, close, current_price, change_percent)
            VALUES ('ADBE', '2026-05-29', 256.79, 256.79, 6.4)
            """
        )
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'Burry stake', '{}')
            """
        )

    story = build_story(query("SELECT * FROM events WHERE id = 1")[0], {"title": "ADBE move"}, [])

    assert story.company == "Adobe Inc."
    assert story.price_card.change_pct == "+6.4%"
    assert story.hook == "ADBE jumped 6.4% today"


def test_renderer_writes_review_bundle_without_ffmpeg(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe Inc.')")
        conn.execute(
            """
            INSERT INTO daily_prices (ticker, date, close, current_price, change_percent)
            VALUES ('ADBE', '2026-05-29', 256.79, 256.79, 6.4)
            """
        )
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'Burry stake', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO scripts (id, event_id, title, script)
            VALUES (1, 1, 'ADBE move', 'script')
            """
        )

    monkeypatch.setattr("media_engine.renderer.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("media_engine.renderer._scene_tts_configured", lambda: False)
    monkeypatch.setattr("media_engine.renderer._scene_audio", lambda *args: None)
    monkeypatch.setattr("media_engine.renderer._audio_duration", lambda *args: None)
    monkeypatch.setattr(
        "media_engine.renderer._render_scene_clip",
        lambda script_id, index, frame, duration: frame,
    )
    monkeypatch.setattr("media_engine.renderer._render_animated_chart_clip", lambda *args: None)
    monkeypatch.setattr(
        "media_engine.renderer._concat_clips",
        lambda clips, output, script_id, label: output.write_bytes(b"video"),
    )
    monkeypatch.setattr(
        "media_engine.renderer._append_outro",
        lambda content_video, output, script_id: output.write_bytes(b"video"),
    )

    result = render_video_bundle(query("SELECT * FROM scripts WHERE id = 1")[0], assets={})

    bundle = Path(result["bundle_path"])
    assert (bundle / "video.mp4").exists()
    assert (bundle / "story.json").exists()
    assert (bundle / "scenes.json").exists()
    assert (bundle / "template_selection.json").exists()
    assert (bundle / "timing_plan.json").exists()
    assert (bundle / "production_plan.json").exists()
    assert (bundle / "render_plan.json").exists()
    assert (bundle / "scene_assets.json").exists()
    assert (bundle / "animated_clips.json").exists()
    assert (bundle / "source_map.json").exists()
    assert (bundle / "sync_report.json").exists()
    assert (bundle / "contact_sheet.png").exists()
    assert (bundle / "captions.json").exists()
    assert (bundle / "captions.srt").exists()
    assert (bundle / "quality_report.json").exists()
    render_plan = json.loads((bundle / "render_plan.json").read_text(encoding="utf-8"))
    assert "headline_card" in render_plan["scene_asset_templates"]
    assert render_plan["layouts"][0]["scene_assets"]


def test_renderer_uses_generated_script_scenes(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO companies (ticker, name) VALUES ('ADBE', 'Adobe Inc.')")
        conn.execute(
            """
            INSERT INTO daily_prices (ticker, date, close, current_price, change_percent)
            VALUES ('ADBE', '2026-05-29', 256.79, 256.79, 6.4)
            """
        )
        conn.execute(
            """
            INSERT INTO events (id, ticker, event_type, event_date, score, reason, analysis_json)
            VALUES (1, 'ADBE', 'story_candidate', '2026-05-29', 80, 'Burry stake', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO scripts (id, event_id, title, script)
            VALUES (1, 1, 'ADBE move', 'script')
            """
        )
    package_dir = Path("outputs/scripts/ADBE_2026-05-29_1")
    package_dir.mkdir(parents=True)
    (package_dir / "script.json").write_text(
        json.dumps(
            {
                "package": {
                    "video_metadata": {
                        "title": "ADBE move",
                        "ticker": "ADBE",
                        "estimated_duration_seconds": 68,
                    },
                    "asset_requests": [{"asset_type": "price_move"}],
                    "scenes": [
                        _generated_scene(
                            1,
                            "hook",
                            "Why ADBE jumped",
                            "A sharp move needs context",
                            source_ids=["S1"],
                        ),
                        _generated_scene(2, "price_action", "Price action", "The move stands out"),
                        _generated_scene(3, "news", "Catalyst", "Evidence matters here"),
                        _generated_scene(4, "risk", "The caveat", "Confirmation still matters"),
                        _generated_scene(5, "conclusion", "Takeaway", "Separate price from facts"),
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    manifest_dir = Path("outputs/script_manifests/ADBE_2026-05-29_1")
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "citable_sources": [
                    {
                        "source_id": "S1",
                        "title": "Adobe source",
                        "url": "https://example.com/adbe",
                        "publisher": "Example",
                    }
                ],
                "context_sources": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("media_engine.renderer.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("media_engine.renderer._scene_tts_configured", lambda: False)
    monkeypatch.setattr("media_engine.renderer._scene_audio", lambda *args: None)
    monkeypatch.setattr("media_engine.renderer._audio_duration", lambda *args: None)
    monkeypatch.setattr(
        "media_engine.renderer._render_scene_clip",
        lambda script_id, index, frame, duration: frame,
    )
    monkeypatch.setattr("media_engine.renderer._render_animated_chart_clip", lambda *args: None)
    monkeypatch.setattr(
        "media_engine.renderer._concat_clips",
        lambda clips, output, script_id, label: output.write_bytes(b"video"),
    )
    monkeypatch.setattr(
        "media_engine.renderer._append_outro",
        lambda content_video, output, script_id: output.write_bytes(b"video"),
    )

    result = render_video_bundle(query("SELECT * FROM scripts WHERE id = 1")[0], assets={})

    scenes = json.loads((Path(result["bundle_path"]) / "scenes.json").read_text(encoding="utf-8"))
    assert [scene["card_type"] for scene in scenes] == [
        "hook_card",
        "price_move_card",
        "news_headline_card",
        "risk_card",
        "takeaway_card",
        "outro_disclaimer_card",
    ]
    assert [scene["scene_type"] for scene in scenes] == [
        "hook",
        "price_action",
        "news",
        "risk",
        "conclusion",
        "outro",
    ]
    assert scenes[0]["confidence_level"] == "medium"
    assert scenes[0]["source_ids"] == ["S1"]
    template_selection = json.loads(
        (Path(result["bundle_path"]) / "template_selection.json").read_text(encoding="utf-8")
    )
    assert template_selection["selected_template_id"] == "risk_radar"
    production_plan = json.loads(
        (Path(result["bundle_path"]) / "production_plan.json").read_text(encoding="utf-8")
    )
    assert production_plan["template_id"] == "risk_radar"
    source_map = json.loads((Path(result["bundle_path"]) / "source_map.json").read_text(encoding="utf-8"))
    assert source_map["S1"]["title"] == "Adobe source"


def test_renderer_repairs_legacy_script_package_bullets():
    payload = {
        "video_metadata": {
            "title": "ADBE move",
            "ticker": "ADBE",
            "estimated_duration_seconds": 68,
        },
        "asset_requests": [{"asset_type": "price_move"}],
        "scenes": [
            _generated_scene(1, "hook", "Why ADBE jumped", "A sharp move needs context"),
            _generated_scene(2, "price_action", "Price action", "The move stands out"),
            _generated_scene(3, "news", "Catalyst", "Evidence matters here"),
            _generated_scene(4, "risk", "The caveat", "Confirmation still matters"),
            _generated_scene(5, "conclusion", "Takeaway", "Separate price from facts"),
        ],
    }
    payload["scenes"][0]["highlights"] = ["ADBE"]
    payload["scenes"][1]["highlights"] = []
    payload["scenes"][2]["highlights"] = [
        "Evidence matters here",
        "Source trail matters",
        "Catalyst may still develop",
        "Extra item",
    ]

    package = GeneratedScriptPackage.model_validate(_package_with_card_bullets(payload))

    assert all(2 <= len(scene.highlights) <= 3 for scene in package.scenes)
    assert package.scenes[0].highlights[0] == "A sharp move needs context"


def test_scene_audio_segments_measure_and_pad_each_scene(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    scenes = [
        Scene(
            scene_type="hook",
            duration=4,
            headline="Adobe jumped",
            narration="Adobe jumped today.",
            caption_text="Adobe jumped",
        ),
        Scene(
            scene_type="risk",
            duration=4,
            headline="Risk check",
            narration="The move still needs confirmation.",
            caption_text="Risk check",
        ),
        Scene(
            scene_type="conclusion",
            duration=4,
            headline="Takeaway",
            narration="Separate the catalyst from valuation.",
            caption_text="Takeaway",
        ),
        Scene(
            scene_type="news",
            duration=4,
            headline="News",
            narration="The report changed the setup.",
            caption_text="News",
        ),
    ]

    monkeypatch.setattr("media_engine.renderer.gemini_configured", lambda: True)
    monkeypatch.setattr(
        "media_engine.renderer.generate_tts_wav",
        lambda text, path, voice_prompt=None: Path(path).write_bytes(b"audio"),
    )
    monkeypatch.setattr("media_engine.renderer._audio_duration", lambda path: 2.5)
    monkeypatch.setattr("media_engine.renderer.execute", lambda *args, **kwargs: None)

    from media_engine.renderer import _scene_audio_segments

    segments = _scene_audio_segments(1, scenes, None, "ADBE_2026-05-29_1")

    assert len(segments) == 4
    assert all(scene.audio_path for scene in scenes)
    assert scenes[0].duration == 3.2
    assert scenes[1].duration == 3.2


def test_renderer_keeps_existing_full_audio_continuous_without_tts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    full_audio = Path("storage/audio/NOW_2026-05-29_23.wav")
    full_audio.parent.mkdir(parents=True)
    full_audio.write_bytes(b"ready audio")
    scenes = [
        Scene(
            scene_type="hook",
            duration=4,
            headline="NOW jumped",
            narration="NOW jumped today.",
            caption_text="NOW jumped",
        ),
        Scene(
            scene_type="news",
            duration=4,
            headline="Catalyst",
            narration="Investors reacted to the catalyst.",
            caption_text="Catalyst",
        ),
    ]

    def fake_audio_duration(path):
        if str(path).endswith("NOW_2026-05-29_23.wav"):
            return 10.0
        return 5.0

    monkeypatch.setattr("media_engine.renderer._audio_duration", fake_audio_duration)
    monkeypatch.setattr(
        "media_engine.renderer._slice_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audio was sliced")),
    )
    monkeypatch.setattr("media_engine.renderer.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "media_engine.renderer.generate_tts_wav",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TTS should not run")),
    )

    from media_engine.renderer import _attach_scene_audio

    result = _attach_scene_audio(
        9,
        scenes,
        {"audio_path": None},
        "NOW_2026-05-29_23",
        Path("outputs/review/NOW_2026-05-29_23/audio"),
    )

    assert result["attempted"] is False
    assert result["full_audio_path"] == str(full_audio)
    assert result["full_audio_duration"] == 10.0
    assert all(scene.audio_path is None for scene in scenes)
    assert all(scene.audio_duration is None for scene in scenes)


def test_scene_asset_templates_generate_number_and_chart_cards(tmp_path: Path):
    story = _story()
    scenes = [
        Scene(
            scene_type="price_action",
            duration=4,
            headline="Price action",
            subheadline="The move stands out",
            narration="The price move stood out.",
            caption_text="Price action",
            card_type="price_move_card",
        ),
        Scene(
            scene_type="chart",
            duration=4,
            headline="Chart check",
            subheadline="Still weak on the chart",
            narration="The chart still matters.",
            caption_text="Chart check",
            card_type="chart_card",
        ),
    ]

    manifest = render_scene_assets(tmp_path / "scene_assets", story, scenes)

    assert {"big_percentage", "dollar_card", "chart_panel"}.issubset(manifest["templates"])
    assert all(Path(asset["path"]).exists() for asset in manifest["assets"])


def test_renderer_builds_animated_chart_clip_when_scene_needs_chart(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    story = _story()
    scene = Scene(
        scene_type="chart",
        duration=4,
        headline="Chart check",
        subheadline="Still weak on the chart",
        narration="The chart still matters.",
        caption_text="Chart check",
        card_type="chart_card",
    )

    def fake_animation(_story, _scene, output_path, *, duration):
        assert duration == 4
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"chart video")
        return output_path

    monkeypatch.setattr("media_engine.renderer.render_price_chart_animation", fake_animation)

    from media_engine.renderer import _render_animated_chart_clip

    clip = _render_animated_chart_clip("ADBE_2026-05-29_1", 2, story, scene)

    assert clip is not None
    assert clip.exists()
    assert clip.name == "ADBE_2026-05-29_1_media_scene_2_chart.mp4"


def test_renderer_does_not_replace_price_move_card_with_chart_animation():
    from media_engine.renderer import _should_animate_chart_scene

    scenes = [
        Scene(
            scene_type="price_action",
            duration=4,
            headline="Price action",
            narration="The price move stood out.",
            caption_text="Price action",
            card_type="price_move_card",
            visual_requirements=["price_move", "stock_chart", "volume_chart"],
        ),
        Scene(
            scene_type="comparison",
            duration=4,
            headline="Bull vs bear",
            narration="The setup is two-sided.",
            caption_text="Bull vs bear",
            card_type="bull_bear_card",
            visual_requirements=["price_chart"],
        ),
    ]

    assert all(_should_animate_chart_scene(scene) is False for scene in scenes)


def test_price_chart_animation_synthesizes_sparse_price_context():
    from media_engine.chart_animation import _synthetic_price_rows

    rows = _synthetic_price_rows(_story(), [])

    assert len(rows) == 12
    assert rows[-1]["close"] == 256.79
    assert rows[-1]["change_percent"] == 6.4
    assert all(row["close"] > 0 for row in rows)


def test_price_chart_animation_skips_when_ffmpeg_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    monkeypatch.setattr("media_engine.chart_animation.shutil.which", lambda name: None)
    from media_engine.chart_animation import render_price_chart_animation

    scene = Scene(
        scene_type="chart",
        duration=4,
        headline="Chart check",
        narration="The chart still matters.",
        caption_text="Chart check",
        card_type="chart_card",
    )

    result = render_price_chart_animation(_story(), scene, tmp_path / "chart.mp4", duration=4)

    assert result is None
    assert not (tmp_path / "chart.mp4").exists()


def test_caption_parts_follow_spoken_phrases():
    scenes = [
        Scene(
            scene_type="news",
            duration=6,
            headline="Catalyst",
            narration="First thing happened. Then investors reacted. But there is a risk.",
            caption_text="Catalyst",
        )
    ]

    captions = captions_for_scenes(scenes)

    assert [caption.text for caption in captions] == [
        "First thing happened.",
        "Then investors reacted.",
        "But there is a risk.",
    ]
    assert captions[-1].end == 6


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
                bullets=["Burry disclosed a position", "Investors reacted"],
            ),
            StorySection(
                type="context",
                title="The catch",
                bullets=["The larger trend still matters", "Risks have not disappeared"],
            ),
            StorySection(
                type="watch",
                title="Watch next",
                bullets=["Next earnings report", "AI product adoption"],
            ),
        ],
        chart_insight="Still weak on the 90-day chart",
        takeaway="Earnings decide if the rally lasts",
    )


def _generated_scene(
    scene_id: int,
    scene_type: str,
    headline: str,
    subheadline: str,
    source_ids: list[str] | None = None,
) -> dict:
    return {
        "id": scene_id,
        "type": scene_type,
        "importance": "high" if scene_id == 1 else "medium",
        "confidence_level": "medium",
        "narration": f"{headline}. {subheadline}.",
        "on_screen_text": {"headline": headline, "subheadline": subheadline},
        "highlights": ["Move needs context", "Watch confirmation next"],
        "source_ids": source_ids or [],
        "visual_requirements": [{"asset_type": "price_move"}],
    }
