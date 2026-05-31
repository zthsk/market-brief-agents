from __future__ import annotations

from pathlib import Path

from media_engine.production import build_production_plan
from media_engine.remotion import build_remotion_render_input, video_renderer_mode
from media_engine.renderer import _mux_audio_with_background_music, render_video_bundle
from media_engine.story_schema import PriceCard, Scene, Story, StorySection
from media_engine.templates.selector import load_video_templates
from models.database import connect, init_db, query


def test_remotion_input_converts_production_plan_and_sparse_chart(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_prices (ticker, date, close, volume, change_percent)
            VALUES ('ADBE', '2026-05-29', 256.79, 1000, 6.4)
            """
        )
    scenes = [
        Scene(
            scene_type="hook",
            duration=4,
            headline="Adobe jumped",
            narration="Adobe jumped today.",
            caption_text="Adobe jumped",
            card_type="hook_card",
        ),
        Scene(
            scene_type="chart",
            duration=4,
            headline="Chart check",
            narration="The chart still matters.",
            caption_text="Chart check",
            card_type="chart_card",
        ),
    ]
    plan = build_production_plan(
        video_id="ADBE_2026-05-29_1",
        output_video_path="videos/ADBE_2026-05-29_1.mp4",
        scenes=scenes,
        narration_audio_path="storage/audio/ADBE_2026-05-29_1.wav",
        narration_audio_duration=8.0,
        template_id="three_things",
        template_name="Three Things",
    )

    payload = build_remotion_render_input(
        story=_story(),
        scenes=scenes,
        plan=plan,
        public_dir=tmp_path / "remotion_public",
    )

    assert payload.video.width == 1080
    assert payload.video.fps == 30
    assert payload.template.template_id == "three_things"
    assert [scene.card_type for scene in payload.scenes] == ["hook_card", "chart_card"]
    assert payload.scenes[1].chart is not None
    assert payload.scenes[1].chart.synthetic is True
    assert payload.scenes[1].chart.title == "Price context"
    assert len(payload.scenes[1].chart.points) == 12
    assert "Chart unavailable" not in payload.model_dump_json()


def test_remotion_public_assets_are_copied(tmp_path: Path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    asset = tmp_path / "asset.png"
    asset.write_bytes(b"asset")
    scenes = [
        Scene(
            scene_type="price_action",
            duration=4,
            headline="Price action",
            narration="The move stood out.",
            caption_text="Price action",
            card_type="price_move_card",
        )
    ]
    plan = build_production_plan(
        video_id="ADBE_2026-05-29_1",
        output_video_path="videos/ADBE_2026-05-29_1.mp4",
        scenes=scenes,
    )

    payload = build_remotion_render_input(
        story=_story(),
        scenes=scenes,
        plan=plan,
        assets={"chart": str(chart)},
        scene_asset_manifest={
            "assets": [
                {
                    "scene_index": 0,
                    "template": "big_percentage",
                    "path": str(asset),
                }
            ]
        },
        public_dir=tmp_path / "public",
    )

    assert {"chart", "big_percentage"}.issubset({item.asset_type for item in payload.assets})
    assert payload.scenes[0].asset_public_paths == ["/assets/big-percentage-asset.png"]
    for item in payload.assets:
        assert (tmp_path / "public" / item.public_path.lstrip("/")).exists()


def test_remotion_input_enriches_empty_bullets_and_backgrounds(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    background = tmp_path / "storage/remotion_backgrounds/market.mp4"
    background.parent.mkdir(parents=True)
    background.write_bytes(b"video")
    scenes = [
        Scene(
            scene_type="bullet_reveal",
            duration=4,
            headline="New AI Tools Unveiled",
            subheadline="Key Partnerships",
            narration=(
                "New generative AI tools, including the Otto assistant, were unveiled "
                "at the Knowledge event. Partnerships gave investors a clearer growth setup."
            ),
            caption_text="New AI Tools Unveiled",
            card_type="three_bullet_card",
        )
    ]
    plan = build_production_plan(
        video_id="NOW_2026-05-29_23",
        output_video_path="videos/NOW_2026-05-29_23.mp4",
        scenes=scenes,
    )

    payload = build_remotion_render_input(
        story=_story(),
        scenes=scenes,
        plan=plan,
        public_dir=tmp_path / "public",
    )

    scene = payload.scenes[0]
    assert scene.detail_text.startswith("New generative AI tools")
    assert any("Otto assistant" in bullet for bullet in scene.bullets)
    assert scene.background_public_path == "/backgrounds/market.mp4"
    assert (tmp_path / "public/backgrounds/market.mp4").exists()


def test_remotion_input_builds_intro_background_music_and_text_timeline(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    background_dir = tmp_path / "storage/remotion_backgrounds"
    background_dir.mkdir(parents=True)
    for name in ("1stFrame.mp4", "1.mp4", "2.mp4", "backgroundMusic1.mp3", "backgroundMusic2.mp3"):
        (background_dir / name).write_bytes(b"asset")
    scenes = [
        Scene(
            scene_type="chart",
            duration=4,
            headline="Chart check",
            subheadline="Price context",
            narration="Chart check.",
            caption_text="Chart check",
            card_type="chart_card",
        )
    ]
    plan = build_production_plan(
        video_id="ADBE_2026-05-29_1",
        output_video_path="videos/ADBE_2026-05-29_1.mp4",
        scenes=scenes,
    )

    payload = build_remotion_render_input(
        story=_story(),
        scenes=scenes,
        plan=plan,
        public_dir=tmp_path / "public",
    )

    assert payload.intro_video is not None
    assert payload.intro_video.public_path == "/backgrounds/1stFrame.mp4"
    assert payload.background_segments[0].segment_type == "intro"
    assert payload.background_segments[0].public_path == "/backgrounds/1stFrame.mp4"
    assert 2.0 <= payload.background_segments[0].duration_seconds <= 2.8
    assert payload.background_segments[-1].start_seconds + payload.background_segments[-1].duration_seconds >= plan.total_duration
    assert all(segment.duration_seconds <= 10.0 for segment in payload.background_segments[1:])
    assert payload.music_track is not None
    assert payload.music_track.volume == 0.4
    assert payload.music_track.public_path.startswith("/music/backgroundMusic")
    assert all(asset.public_path != "/backgrounds/1stFrame.mp4" for asset in payload.assets if asset.asset_type == "background_video")
    assert payload.text_beats
    assert payload.text_beats[0].text != "Chart check"
    assert "closed" in payload.text_beats[0].text


def test_background_music_mux_ducks_music_under_narration(tmp_path: Path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr("media_engine.renderer.subprocess.run", fake_run)

    _mux_audio_with_background_music(
        tmp_path / "video.mp4",
        tmp_path / "narration.wav",
        tmp_path / "backgroundMusic1.mp3",
        tmp_path / "out.mp4",
        duration=12.0,
        music_volume=0.4,
    )

    cmd, kwargs = calls[0]
    filtergraph = cmd[cmd.index("-filter_complex") + 1]
    assert "-stream_loop" in cmd
    assert "volume=0.400" in filtergraph
    assert "sidechaincompress" in filtergraph
    assert "amix=inputs=2" in filtergraph
    assert cmd[cmd.index("-t") + 1] == "12.000"
    assert kwargs["check"] is True


def test_all_yaml_card_types_have_remotion_coverage():
    supported = {
        "hook_card",
        "price_move_card",
        "news_headline_card",
        "chart_card",
        "three_bullet_card",
        "earnings_card",
        "analyst_card",
        "volume_spike_card",
        "risk_card",
        "bull_bear_card",
        "takeaway_card",
        "outro_disclaimer_card",
    }
    used = {
        slot.card_type
        for template in load_video_templates()
        for slot in template.scene_slots
    }

    assert used <= supported


def test_video_renderer_mode_defaults_to_python(monkeypatch):
    monkeypatch.delenv("VIDEO_RENDERER", raising=False)
    assert video_renderer_mode() == "python"
    monkeypatch.setenv("VIDEO_RENDERER", "remotion")
    assert video_renderer_mode() == "remotion"


def test_renderer_writes_remotion_artifacts_when_flag_is_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.setenv("VIDEO_RENDERER", "remotion")
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

    def fake_render_remotion_video(*, input_path, output_path, public_dir):
        assert input_path.exists()
        assert public_dir.exists()
        output_path.write_bytes(b"remotion video")
        return {"renderer": "remotion", "output_path": str(output_path)}

    monkeypatch.setattr("media_engine.renderer.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("media_engine.renderer._scene_tts_configured", lambda: False)
    monkeypatch.setattr("media_engine.renderer._audio_duration", lambda *args: None)
    monkeypatch.setattr("media_engine.renderer.render_remotion_video", fake_render_remotion_video)
    monkeypatch.setattr(
        "media_engine.renderer._append_outro",
        lambda content_video, output, script_id: output.write_bytes(b"video"),
    )

    result = render_video_bundle(query("SELECT * FROM scripts WHERE id = 1")[0], assets={})

    bundle = Path(result["bundle_path"])
    manifest = (bundle / "manifest.json").read_text(encoding="utf-8")
    assert result["renderer"] == "remotion"
    assert (bundle / "remotion_input.json").exists()
    assert (bundle / "remotion_render.json").exists()
    assert '"renderer": "remotion"' in manifest


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
                bullets=["Investors reacted to a new catalyst"],
            )
        ],
        chart_insight="Still weak on the 90-day chart",
        takeaway="Earnings decide if the rally lasts",
    )
