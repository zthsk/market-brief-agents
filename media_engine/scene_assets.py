from __future__ import annotations

import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from media_engine.paths import LOGO_PATH
from media_engine.story_schema import Scene, Story


ASSET_SIZE = (1080, 1080)
BG = "#0f172a"
PANEL = "#f8fafc"
INK = "#0f172a"
MUTED = "#64748b"
TEAL = "#16d4d8"
GREEN = "#12b981"
RED = "#ef4444"
YELLOW = "#fbbf24"


def render_scene_assets(directory: Path, story: Story, scenes: list[Scene]) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    assets = []
    template_ids = []
    for index, scene in enumerate(scenes):
        for template_id in _templates_for_scene(scene):
            path = directory / f"scene_{index:02d}_{template_id}.png"
            _RENDERERS[template_id](path, story, scene)
            template_ids.append(template_id)
            assets.append(
                {
                    "scene_index": index,
                    "scene_type": scene.scene_type,
                    "slot_id": scene.slot_id,
                    "card_type": scene.card_type,
                    "template_id": template_id,
                    "path": str(path),
                }
            )
    return {
        "version": 1,
        "asset_root": str(directory),
        "templates": sorted(set(template_ids)),
        "assets": assets,
    }


def _templates_for_scene(scene: Scene) -> list[str]:
    card_type = scene.card_type or scene.scene_type
    templates = ["headline_card"]
    if card_type in {"hook_card", "price_move_card", "volume_spike_card"} or scene.scene_type in {
        "hook",
        "price_card",
        "price_action",
    }:
        templates.extend(["big_percentage", "dollar_card"])
    if card_type == "chart_card" or scene.scene_type in {"chart", "timeline"} or scene.chart_path:
        templates.append("chart_panel")
    if card_type in {"three_bullet_card", "news_headline_card"} or scene.bullets:
        templates.append("bullet_stack")
    if card_type in {"earnings_card", "analyst_card"} or scene.scene_type in {"earnings", "financials", "analyst"}:
        templates.append("metric_panel")
    if card_type in {"risk_card", "bull_bear_card"} or scene.scene_type in {"risk", "comparison"}:
        templates.append("risk_panel")
    if card_type in {"takeaway_card", "outro_disclaimer_card"} or scene.scene_type in {
        "takeaway",
        "conclusion",
        "outro",
    }:
        templates.append("takeaway_panel")
    return list(dict.fromkeys(templates))


def _headline_card(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base(scene.visual_style)
    _brand(image, draw, story)
    _label(draw, scene.card_type or scene.scene_type, 88)
    _wrapped(draw, scene.headline, 80, 260, 920, _font(72, bold=True), "#f8fafc", max_lines=3)
    if scene.subheadline:
        _wrapped(draw, scene.subheadline, 84, 570, 900, _font(42), "#cbd5e1", max_lines=3)
    _footer(draw, story)
    _save(image, path)


def _big_percentage(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base(scene.visual_style)
    _brand(image, draw, story)
    value = story.price_card.change_pct or _first_number(scene.headline) or "0.0%"
    color = _move_color(story)
    draw.rounded_rectangle((70, 245, 1010, 790), radius=36, fill=PANEL)
    draw.text((120, 310), "Move", font=_font(42, bold=True), fill=MUTED)
    font = _fit_font(value, 170, 82, 820)
    draw.text((120, 385), value, font=font, fill=color)
    period = story.price_card.period.capitalize()
    _wrapped(
        draw,
        f"{story.ticker} {period} reaction",
        126,
        650,
        780,
        _font(48, bold=True),
        INK,
        max_lines=2,
    )
    _footer(draw, story)
    _save(image, path)


def _dollar_card(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base(scene.visual_style)
    _brand(image, draw, story)
    price = story.price_card.price or _first_dollar(scene.narration) or "$0.00"
    draw.rounded_rectangle((70, 235, 1010, 825), radius=36, fill=PANEL)
    draw.text((120, 302), story.ticker, font=_font(52, bold=True), fill=MUTED)
    font = _fit_font(price, 138, 76, 820)
    draw.text((120, 390), price, font=font, fill=INK)
    _wrapped(
        draw,
        scene.subheadline or scene.headline or "Latest market price",
        126,
        640,
        800,
        _font(44, bold=True),
        "#334155",
        max_lines=3,
    )
    _footer(draw, story)
    _save(image, path)


def _chart_panel(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base("dark_grid")
    _brand(image, draw, story)
    draw.rounded_rectangle((58, 215, 1022, 890), radius=34, fill=PANEL)
    if scene.chart_path and Path(scene.chart_path).exists():
        chart = Image.open(scene.chart_path).convert("RGB")
        chart = ImageOps.fit(chart, (884, 560), method=Image.Resampling.LANCZOS, centering=(0.5, 0.35))
        image.paste(chart, (98, 275))
    else:
        _placeholder_chart(draw, story)
    draw.rounded_rectangle((58, 215, 1022, 890), radius=34, outline=TEAL, width=5)
    _wrapped(draw, scene.subheadline or story.chart_insight, 86, 925, 900, _font(36), "#cbd5e1", max_lines=2)
    _save(image, path)


def _bullet_stack(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base(scene.visual_style)
    _brand(image, draw, story)
    _label(draw, "Key points", 96)
    bullets = scene.bullets[:3] or [scene.subheadline or scene.headline]
    y = 250
    for index, bullet in enumerate(bullets):
        accent = [TEAL, YELLOW, GREEN][index % 3]
        draw.rounded_rectangle((70, y, 1010, y + 180), radius=28, fill=PANEL)
        draw.ellipse((112, y + 52, 188, y + 128), fill=accent)
        draw.text((140, y + 70), str(index + 1), font=_font(30, bold=True), fill=INK)
        _wrapped(draw, bullet, 220, y + 50, 720, _font(43, bold=True), INK, max_lines=2)
        y += 210
    _footer(draw, story)
    _save(image, path)


def _metric_panel(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base(scene.visual_style)
    _brand(image, draw, story)
    _label(draw, "Numbers", 96)
    metrics = scene.bullets[:4] or [scene.subheadline or scene.headline, story.price_card.change_pct]
    boxes = [(72, 250), (552, 250), (72, 540), (552, 540)]
    colors = [TEAL, GREEN, YELLOW, "#60a5fa"]
    for index, metric in enumerate(metrics[:4]):
        x, y = boxes[index]
        draw.rounded_rectangle((x, y, x + 456, y + 240), radius=28, fill=PANEL)
        draw.rectangle((x, y, x + 456, y + 18), fill=colors[index % len(colors)])
        _wrapped(draw, metric, x + 34, y + 62, 380, _font(45, bold=True), INK, max_lines=2)
    _footer(draw, story)
    _save(image, path)


def _risk_panel(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base("red_risk")
    _brand(image, draw, story)
    draw.rounded_rectangle((70, 235, 1010, 820), radius=36, fill=PANEL)
    draw.rounded_rectangle((120, 300, 250, 430), radius=28, fill=YELLOW)
    draw.text((166, 323), "!", font=_font(76, bold=True), fill=INK)
    _wrapped(draw, scene.headline or "Risk check", 120, 500, 800, _font(56, bold=True), INK, max_lines=2)
    _wrapped(
        draw,
        scene.subheadline or (scene.bullets[0] if scene.bullets else story.disclaimer),
        124,
        660,
        790,
        _font(38),
        MUTED,
        max_lines=3,
    )
    _footer(draw, story)
    _save(image, path)


def _takeaway_panel(path: Path, story: Story, scene: Scene) -> None:
    image, draw = _base(scene.visual_style)
    _brand(image, draw, story)
    _label(draw, "Takeaway", 96)
    draw.rounded_rectangle((70, 245, 1010, 810), radius=36, fill=PANEL)
    _wrapped(
        draw,
        scene.subheadline or scene.headline or story.takeaway,
        126,
        330,
        820,
        _font(58, bold=True),
        INK,
        max_lines=4,
    )
    _footer(draw, story)
    _save(image, path)


def _base(visual_style: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
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
    accent, panel = accents.get(visual_style, (TEAL, "#172554"))
    image = Image.new("RGB", ASSET_SIZE, BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1080, 178), fill="#0b1220")
    draw.polygon([(690, 0), (1080, 0), (1080, 410), (860, 340)], fill=panel)
    draw.line((0, 180, 1080, 180), fill=accent, width=5)
    for x in range(-100, 1080, 138):
        draw.line((x, 900, x + 260, 1080), fill="#1f2937", width=3)
    return image, draw


def _brand(image: Image.Image, draw: ImageDraw.ImageDraw, story: Story) -> None:
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGB").resize((64, 64))
        image.paste(logo, (70, 58))
    draw.text((152, 58), "Market Brief Agents", font=_font(32, bold=True), fill="#f8fafc")
    draw.text((152, 98), f"{story.ticker} market brief", font=_font(24), fill=TEAL)


def _label(draw: ImageDraw.ImageDraw, text: str, y: int) -> None:
    clean = str(text or "Scene").replace("_", " ").title()
    draw.rounded_rectangle((72, y, 392, y + 58), radius=18, fill=TEAL)
    draw.text((104, y + 16), clean[:18], font=_font(23, bold=True), fill=INK)


def _footer(draw: ImageDraw.ImageDraw, story: Story) -> None:
    draw.text((72, 990), f"{story.date} | educational only", font=_font(25), fill="#cbd5e1")


def _placeholder_chart(draw: ImageDraw.ImageDraw, story: Story) -> None:
    left, top, right, bottom = 120, 330, 940, 780
    draw.rectangle((left, top, right, bottom), fill="#eef2f7")
    draw.line((left + 30, bottom - 40, right - 20, bottom - 40), fill="#94a3b8", width=3)
    draw.line((left + 30, top + 35, left + 30, bottom - 40), fill="#94a3b8", width=3)
    points = [(150, 680), (270, 600), (390, 640), (510, 500), (650, 535), (790, 410), (910, 455)]
    draw.line(points, fill=TEAL, width=9, joint="curve")
    draw.ellipse((890, 435, 930, 475), fill=_move_color(story))
    draw.text((150, 260), "Chart template", font=_font(36, bold=True), fill=INK)


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
    for line in textwrap.wrap(str(text or ""), width=chars)[:max_lines]:
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


def _fit_font(text: str, max_size: int, min_size: int, max_width: int):
    for size in range(max_size, min_size - 1, -4):
        font = _font(size, bold=True)
        left, _top, right, _bottom = font.getbbox(str(text))
        if right - left <= max_width:
            return font
    return _font(min_size, bold=True)


def _move_color(story: Story) -> str:
    direction = story.price_card.direction
    if direction == "up":
        return GREEN
    if direction == "down":
        return RED
    return YELLOW


def _first_number(text: str) -> str | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?%", str(text or ""))
    return match.group(0) if match else None


def _first_dollar(text: str) -> str | None:
    match = re.search(r"\$[\d,]+(?:\.\d+)?", str(text or ""))
    return match.group(0) if match else None


def _save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


_RENDERERS = {
    "headline_card": _headline_card,
    "big_percentage": _big_percentage,
    "dollar_card": _dollar_card,
    "chart_panel": _chart_panel,
    "bullet_stack": _bullet_stack,
    "metric_panel": _metric_panel,
    "risk_panel": _risk_panel,
    "takeaway_panel": _takeaway_panel,
}
