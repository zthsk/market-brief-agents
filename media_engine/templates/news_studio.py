from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from media_engine.paths import LOGO_PATH
from media_engine.story_schema import Scene, Story


SIZE = (1080, 1920)
BG = "#111827"
PANEL = "#f8fafc"
INK = "#0f172a"
MUTED = "#64748b"
TEAL = "#16d4d8"
GREEN = "#12b981"
RED = "#ef4444"
YELLOW = "#fbbf24"


def render_scene_frame(
    path: Path,
    story: Story,
    scene: Scene,
    index: int,
    total: int,
    captions: bool = True,
) -> Path:
    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    _background(draw, index, scene.visual_style)
    _progress(draw, scene.progress_end)
    _brand(image, draw)
    _ticker_watermark(draw, story.ticker)
    card_type = scene.card_type or scene.scene_type
    if card_type == "hook_card" or scene.scene_type == "hook":
        _hook(draw, story, scene)
    elif card_type == "price_move_card" or scene.scene_type in {"price_card", "price_action"}:
        _price_card(draw, story, scene)
    elif card_type == "chart_card" or scene.scene_type in {"chart", "timeline"}:
        _chart_scene(image, draw, story, scene)
    elif card_type == "volume_spike_card":
        _volume_scene(draw, story, scene)
    elif card_type == "bull_bear_card":
        _bull_bear_scene(draw, story, scene)
    elif card_type in {"news_headline_card", "three_bullet_card"} or scene.scene_type in {
        "bullet_reveal",
        "context",
        "news",
        "company",
        "industry",
        "comparison",
    }:
        _bullet_scene(draw, story, scene)
    elif card_type == "earnings_card" or scene.scene_type in {"earnings", "financials"}:
        _metric_scene(draw, story, scene)
    elif card_type == "analyst_card" or scene.scene_type == "analyst":
        _analyst_scene(draw, story, scene)
    elif card_type == "risk_card" or scene.scene_type == "risk":
        _risk_scene(draw, story, scene)
    elif card_type == "takeaway_card" or scene.scene_type in {"takeaway", "conclusion"}:
        _takeaway(draw, story, scene)
    else:
        _outro(draw, scene)
    if captions:
        _caption(draw, scene.caption_text or scene.headline, scene.caption_style)
    if scene.show_footer:
        _footer(draw, story)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def render_thumbnail(path: Path, story: Story, chart_path: str | None = None) -> Path:
    scene = Scene(
        scene_type="hook",
        duration=1,
        headline=story.hook,
        subheadline=f"{story.ticker} {story.price_card.change_pct}",
        narration=story.hook,
        caption_text=story.hook,
        chart_path=chart_path,
    )
    return render_scene_frame(path, story, scene, 0, 1, captions=False)


def _background(draw: ImageDraw.ImageDraw, index: int, visual_style: str = "dark_grid") -> None:
    accents = {
        "dark_grid": (TEAL, "#172554"),
        "blue_gradient": ("#60a5fa", "#1e3a8a"),
        "green_momentum": (GREEN, "#064e3b"),
        "red_risk": (RED, "#7f1d1d"),
        "neutral_news": ("#94a3b8", "#1f2937"),
        "gold_earnings": (YELLOW, "#713f12"),
        "purple_analyst": ("#a78bfa", "#4c1d95"),
        "gray_outro": ("#cbd5e1", "#1f2937"),
    }
    accent, panel = accents.get(visual_style, ([TEAL, "#60a5fa", YELLOW, GREEN][index % 4], "#172554"))
    draw.rectangle((0, 0, 1080, 1920), fill=BG)
    draw.rectangle((0, 0, 1080, 230), fill="#0b1220")
    draw.polygon([(680, 0), (1080, 0), (1080, 620), (850, 520)], fill=panel)
    draw.line((0, 232, 1080, 232), fill=accent, width=4)
    for x in range(-80, 1080, 135):
        draw.line((x, 1520, x + 360, 1920), fill="#1f2937", width=3)


def _progress(draw: ImageDraw.ImageDraw, progress: float) -> None:
    draw.rounded_rectangle((56, 72, 1024, 88), radius=8, fill="#334155")
    draw.rounded_rectangle((56, 72, 56 + int(968 * progress), 88), radius=8, fill=TEAL)


def _brand(image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGB").resize((92, 92))
        image.paste(logo, (58, 112))
    draw.text((166, 124), "Market Brief Agents", font=_font(38, bold=True), fill="#f8fafc")
    draw.text((166, 168), "MARKET BRIEF", font=_font(24), fill=TEAL)


def _ticker_watermark(draw: ImageDraw.ImageDraw, ticker: str) -> None:
    draw.text((590, 1550), ticker, font=_font(190, bold=True), fill="#1f2937")


def _hook(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "TOP STORY", 320)
    _big_text(draw, scene.headline, 120, 430, 820, 84)
    _highlight_strip(draw, scene)
    _lower_card(draw, f"{story.company}", f"{story.ticker} | {story.price_card.change_pct} today")


def _price_card(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    color = GREEN if story.price_card.direction == "up" else RED if story.price_card.direction == "down" else YELLOW
    _label(draw, story.ticker, 320)
    draw.rounded_rectangle((96, 430, 984, 1040), radius=34, fill=PANEL)
    draw.text((150, 500), story.price_card.price, font=_font(108, bold=True), fill=INK)
    draw.rounded_rectangle((150, 650, 600, 760), radius=28, fill=color)
    draw.text((188, 670), story.price_card.change_pct, font=_font(58, bold=True), fill="white")
    draw.text((150, 840), "Price movement", font=_font(34), fill=MUTED)
    draw.text((150, 892), f"{story.price_card.period.capitalize()} market reaction", font=_font(42, bold=True), fill=INK)
    _source_chip(draw, story)


def _chart_scene(image: Image.Image, draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "CHART CHECK", 295)
    if scene.chart_path and Path(scene.chart_path).exists():
        chart = Image.open(scene.chart_path).convert("RGB").resize((900, 1600))
        chart = chart.crop((0, 0, 900, 1230))
        chart = chart.resize((880, 1200))
        image.paste(chart, (100, 390))
        draw.rounded_rectangle((96, 390, 984, 1590), radius=30, outline=TEAL, width=5)
    else:
        _fallback_chart(draw, story, scene)


def _fallback_chart(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    draw.rounded_rectangle((96, 430, 984, 1200), radius=34, fill=PANEL)
    draw.text((150, 500), "Price context", font=_font(54, bold=True), fill=INK)
    draw.text((150, 580), story.price_card.price, font=_font(86, bold=True), fill=INK)
    color = GREEN if story.price_card.direction == "up" else RED if story.price_card.direction == "down" else YELLOW
    draw.rounded_rectangle((150, 710, 460, 800), radius=24, fill=color)
    draw.text((182, 732), story.price_card.change_pct, font=_font(42, bold=True), fill="white")
    left, top, right, bottom = 150, 890, 930, 1110
    draw.line((left, bottom, right, bottom), fill="#cbd5e1", width=4)
    draw.line((left, top, left, bottom), fill="#cbd5e1", width=4)
    points = [
        (left + 20, bottom - 45),
        (left + 145, bottom - 92),
        (left + 270, bottom - 70),
        (left + 410, bottom - 138),
        (left + 555, bottom - 118),
        (right - 20, bottom - 172),
    ]
    draw.line(points, fill=TEAL, width=8, joint="curve")
    draw.ellipse((right - 42, bottom - 194, right + 2, bottom - 150), fill=color)
    _wrapped(draw, scene.subheadline or story.chart_insight, 150, 1134, 790, _font(34, bold=True), MUTED, max_lines=2)

def _bullet_scene(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, scene.headline.upper(), 300)
    _wrapped(draw, scene.subheadline, 100, 392, 860, _font(42, bold=True), "#e2e8f0", max_lines=2)
    y = 545
    colors = [TEAL, YELLOW, GREEN]
    bullets = scene.bullets[:3] or [scene.subheadline or scene.narration]
    for idx, bullet in enumerate(bullets[:3]):
        draw.rounded_rectangle((90, y, 990, y + 210), radius=28, fill=PANEL)
        draw.ellipse((130, y + 65, 210, y + 145), fill=colors[idx % len(colors)])
        draw.text((157, y + 80), str(idx + 1), font=_font(34, bold=True), fill=INK)
        _wrapped(draw, bullet, 250, y + 58, 650, _font(46, bold=True), INK, max_lines=2)
        y += 250
    _source_chip(draw, story, scene)


def _metric_scene(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "NUMBERS", 300)
    _wrapped(draw, scene.headline, 96, 395, 880, _font(66, bold=True), "#f8fafc", max_lines=2)
    metrics = scene.bullets[:4] or [scene.subheadline]
    positions = [(92, 620), (552, 620), (92, 900), (552, 900)]
    colors = [TEAL, GREEN, YELLOW, "#60a5fa"]
    for index, metric in enumerate(metrics[:4]):
        x, y = positions[index]
        draw.rounded_rectangle((x, y, x + 436, y + 230), radius=28, fill=PANEL)
        draw.rectangle((x, y, x + 436, y + 16), fill=colors[index % len(colors)])
        _wrapped(draw, metric, x + 36, y + 58, 360, _font(48, bold=True), INK, max_lines=2)
    _source_chip(draw, story, scene)


def _analyst_scene(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "STREET VIEW", 300)
    draw.rounded_rectangle((88, 430, 992, 1090), radius=34, fill=PANEL)
    draw.text((140, 500), "Analyst signal", font=_font(38), fill=MUTED)
    _wrapped(draw, scene.headline, 140, 570, 800, _font(72, bold=True), INK, max_lines=2)
    draw.rounded_rectangle((140, 820, 940, 955), radius=30, fill="#0b1220")
    _wrapped(draw, scene.subheadline, 180, 855, 720, _font(40, bold=True), "#f8fafc", max_lines=2)
    _highlight_strip(draw, scene, y=1140)
    _source_chip(draw, story, scene)


def _volume_scene(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "VOLUME ALERT", 300)
    draw.rounded_rectangle((88, 430, 992, 1080), radius=34, fill=PANEL)
    draw.text((140, 505), story.ticker, font=_font(52, bold=True), fill=MUTED)
    _wrapped(draw, scene.headline, 140, 590, 760, _font(82, bold=True), INK, max_lines=2)
    draw.rounded_rectangle((140, 835, 940, 965), radius=30, fill="#064e3b")
    _wrapped(draw, scene.subheadline, 180, 872, 720, _font(44, bold=True), "#ecfdf5", max_lines=2)
    _source_chip(draw, story, scene, y=1160)


def _bull_bear_scene(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "BULL VS BEAR", 300)
    draw.rounded_rectangle((74, 440, 524, 1120), radius=32, fill="#ecfdf5")
    draw.rounded_rectangle((556, 440, 1006, 1120), radius=32, fill="#fef2f2")
    draw.text((124, 505), "Bull case", font=_font(42, bold=True), fill="#065f46")
    draw.text((606, 505), "Bear case", font=_font(42, bold=True), fill="#991b1b")
    bullets = scene.bullets[:2] or [scene.subheadline, scene.narration]
    _wrapped(draw, bullets[0] if bullets else story.hook, 124, 615, 340, _font(46, bold=True), INK, max_lines=4)
    bear_text = bullets[1] if len(bullets) > 1 else story.disclaimer
    _wrapped(draw, bear_text, 606, 615, 340, _font(46, bold=True), INK, max_lines=4)
    _source_chip(draw, story, scene, y=1190)


def _takeaway(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "TAKEAWAY", 320)
    draw.rounded_rectangle((88, 450, 992, 1120), radius=34, fill=PANEL)
    _wrapped(draw, scene.subheadline, 140, 560, 800, _font(64, bold=True), INK, max_lines=4)
    draw.rounded_rectangle((140, 1220, 940, 1350), radius=30, fill="#0b1220")
    draw.text((182, 1260), _truncate(scene.headline, 30), font=_font(40, bold=True), fill=TEAL)
    _source_chip(draw, story, scene, y=1430)


def _risk_scene(draw: ImageDraw.ImageDraw, story: Story, scene: Scene) -> None:
    _label(draw, "RISK CHECK", 300)
    draw.rounded_rectangle((88, 450, 992, 1160), radius=34, fill=PANEL)
    draw.rounded_rectangle((138, 510, 278, 650), radius=28, fill=YELLOW)
    draw.text((184, 535), "!", font=_font(82, bold=True), fill=INK)
    _wrapped(draw, scene.headline, 140, 720, 800, _font(62, bold=True), INK, max_lines=2)
    _wrapped(draw, scene.subheadline, 140, 900, 800, _font(42), MUTED, max_lines=3)
    if scene.bullets:
        _wrapped(draw, " | ".join(scene.bullets[:3]), 140, 1060, 800, _font(34, bold=True), RED, max_lines=2)
    _source_chip(draw, story, scene, y=1240)


def _outro(draw: ImageDraw.ImageDraw, scene: Scene) -> None:
    _label(draw, "MARKET BRIEF AGENTS", 430)
    _big_text(draw, scene.headline, 120, 560, 820, 82)
    draw.text((150, 900), scene.subheadline, font=_font(44), fill="#cbd5e1")


def _caption(draw: ImageDraw.ImageDraw, text: str, caption_style: str = "default_caption") -> None:
    fill = "#020617"
    accent = "#f8fafc"
    if caption_style == "risk_caption":
        fill = "#450a0a"
        accent = "#fee2e2"
    elif caption_style == "emphasis_caption":
        fill = "#082f49"
        accent = "#e0f2fe"
    draw.rounded_rectangle((94, 1268, 986, 1438), radius=28, fill=fill)
    _wrapped(draw, text, 140, 1310, 800, _font(44, bold=True), accent, max_lines=2)


def _footer(draw: ImageDraw.ImageDraw, story: Story) -> None:
    source = " | ".join(story.sources[:2]) if story.sources else "public market data"
    draw.text((72, 1760), f"Source: {source} | {story.date}", font=_font(25), fill="#cbd5e1")
    draw.text((72, 1805), f"Market Brief Agents | {story.disclaimer}", font=_font(27), fill="#e2e8f0")


def _lower_card(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.rounded_rectangle((84, 1200, 996, 1485), radius=32, fill=PANEL)
    _wrapped(draw, title, 140, 1265, 760, _font(50, bold=True), INK, max_lines=2)
    draw.text((140, 1405), subtitle, font=_font(36), fill=MUTED)


def _highlight_strip(draw: ImageDraw.ImageDraw, scene: Scene, y: int = 1040) -> None:
    x = 96
    for item in scene.bullets[:3]:
        width = min(320, max(140, len(str(item)) * 22))
        draw.rounded_rectangle((x, y, x + width, y + 78), radius=24, fill="#0b1220")
        draw.text((x + 28, y + 22), str(item)[:18], font=_font(29, bold=True), fill=TEAL)
        x += width + 18


def _source_chip(draw: ImageDraw.ImageDraw, story: Story, scene: Scene | None = None, y: int = 1190) -> None:
    confidence = getattr(scene, "confidence_level", "medium") if scene else "medium"
    sources = getattr(scene, "source_ids", []) if scene else []
    source_text = ",".join(sources[:2]) if sources else "source checked"
    draw.rounded_rectangle((100, y, 980, y + 120), radius=28, fill="#0b1220")
    draw.text(
        (144, y + 38),
        f"{story.ticker} | {confidence.upper()} confidence | {source_text}",
        font=_font(32),
        fill="#f8fafc",
    )


def _label(draw: ImageDraw.ImageDraw, text: str, y: int) -> None:
    draw.rounded_rectangle((92, y, 430, y + 64), radius=18, fill=TEAL)
    draw.text((128, y + 17), text[:18], font=_font(25, bold=True), fill=INK)


def _big_text(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, width: int, size: int) -> None:
    _wrapped(draw, text, x, y, width, _font(size, bold=True), "#f8fafc", max_lines=4)


def _wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    font,
    fill: str,
    max_lines: int,
) -> None:
    avg = max(12, int(font.size * 0.52))
    chars = max(8, width // avg)
    for line in textwrap.wrap(str(text), width=chars)[:max_lines]:
        draw.text((x, y), line, font=font, fill=fill)
        y += int(font.size * 1.18)


def _font(size: int, bold: bool = False):
    names = (
        ("Arial Bold.ttf", "Helvetica-Bold.ttf", "DejaVuSans-Bold.ttf")
        if bold
        else ("Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _truncate(text: str, limit: int) -> str:
    return str(text or "")[:limit].rstrip()
