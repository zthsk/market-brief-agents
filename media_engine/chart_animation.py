from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import textwrap

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter, FuncAnimation
from matplotlib.dates import DateFormatter
from matplotlib.patches import FancyBboxPatch

from media_engine.story_schema import Scene, Story
from models.database import query


FPS = 30
BG = "#0f172a"
HEADER = "#0b1220"
PANEL = "#f8fafc"
INK = "#0f172a"
MUTED = "#64748b"
GRID = "#d8dee7"
TEAL = "#16d4d8"
GREEN = "#12b981"
RED = "#ef4444"
YELLOW = "#fbbf24"


def render_price_chart_animation(
    story: Story,
    scene: Scene,
    output_path: Path,
    *,
    duration: float,
    fps: int = FPS,
) -> Path | None:
    rows = _price_rows(story.ticker)
    if shutil.which("ffmpeg") is None:
        return None
    synthetic = len(rows) < 2
    if synthetic:
        rows = _synthetic_price_rows(story, rows)

    dates = [_parse_date(row["date"]) for row in rows]
    closes = [float(row["close"]) for row in rows]
    volumes = [int(row.get("volume") or 0) for row in rows]
    latest_move = _float(rows[-1].get("change_percent"), 0.0)
    frame_count = _frame_count(duration, fps)
    move_color = _move_color(story, latest_move)
    palette = _palette(move_color)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(10.8, 19.2), dpi=100, facecolor=BG)
    _scene_chrome(fig, story, scene, move_color, synthetic=synthetic)

    chart_ax = fig.add_axes((0.08, 0.37, 0.84, 0.34), facecolor=PANEL)
    volume_ax = fig.add_axes((0.08, 0.25, 0.84, 0.08), facecolor=PANEL, sharex=chart_ax)
    _style_axes(chart_ax, volume_ax, dates, closes, volumes)

    glow_line, = chart_ax.plot([], [], color=palette["line"], linewidth=10, alpha=0.16)
    price_line, = chart_ax.plot([], [], color=palette["line"], linewidth=4.8, solid_capstyle="round")
    marker, = chart_ax.plot([], [], "o", color=move_color, markersize=13, markeredgecolor="white", markeredgewidth=2)
    volume_bars = volume_ax.bar(dates, [0] * len(volumes), color=palette["volume"], width=0.9, alpha=0.74)

    price_text = fig.text(0.08, 0.825, "", fontsize=54, color="#f8fafc", weight="bold")
    move_text = fig.text(0.44, 0.833, "", fontsize=30, color=move_color, weight="bold")
    range_text = fig.text(0.08, 0.795, "", fontsize=22, color="#cbd5e1")

    def update(frame: int):
        progress = min(1.0, (frame + 1) / frame_count)
        eased = 1 - (1 - progress) ** 3
        shown = max(2, min(len(dates), round(2 + eased * (len(dates) - 2))))
        shown_dates = dates[:shown]
        shown_closes = closes[:shown]

        glow_line.set_data(shown_dates, shown_closes)
        price_line.set_data(shown_dates, shown_closes)
        marker.set_data([shown_dates[-1]], [shown_closes[-1]])

        for index, bar in enumerate(volume_bars):
            visible = index < shown
            bar.set_height(volumes[index] if visible else 0)
            bar.set_alpha(0.94 if index == shown - 1 else 0.58 if visible else 0.0)

        current = shown_closes[-1]
        period_change = ((current - closes[0]) / closes[0]) * 100 if closes[0] else 0.0
        visible_high = max(shown_closes)
        visible_low = min(shown_closes)
        price_text.set_text(f"${current:,.2f}")
        move_text.set_text(f"{period_change:+.1f}% in view")
        if synthetic:
            range_text.set_text(f"Latest snapshot | Move {latest_move:+.1f}%")
        else:
            range_text.set_text(f"Range ${visible_low:,.2f}-${visible_high:,.2f} | Latest day {latest_move:+.1f}%")
        return [glow_line, price_line, marker, *volume_bars, price_text, move_text, range_text]

    animation = FuncAnimation(fig, update, frames=frame_count, interval=1000 / fps, blit=False)
    writer = FFMpegWriter(
        fps=fps,
        metadata={"artist": "Market Brief Agents"},
        bitrate=4200,
        extra_args=["-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "19"],
    )
    try:
        animation.save(str(output_path), writer=writer, dpi=100)
    finally:
        plt.close(fig)
    return output_path if output_path.exists() else None


def _price_rows(ticker: str) -> list[dict]:
    rows = query(
        """
        SELECT date, close, volume, change_percent
        FROM daily_prices
        WHERE ticker = ? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 90
        """,
        (ticker,),
    )
    return list(reversed(rows))


def _scene_chrome(fig, story: Story, scene: Scene, move_color: str, *, synthetic: bool) -> None:
    fig.patches.append(
        FancyBboxPatch(
            (0, 0.885),
            1,
            0.115,
            boxstyle="square,pad=0",
            facecolor=HEADER,
            transform=fig.transFigure,
        )
    )
    fig.patches.append(
        FancyBboxPatch(
            (0.64, 0.785),
            0.42,
            0.22,
            boxstyle="square,pad=0",
            facecolor="#172554",
            transform=fig.transFigure,
        )
    )
    fig.text(0.07, 0.94, "Market Brief Agents", fontsize=34, color="#f8fafc", weight="bold")
    fig.text(0.07, 0.915, f"{story.ticker} market brief", fontsize=21, color=TEAL)
    fig.text(0.07, 0.865, "CHART CHECK", fontsize=22, color=move_color, weight="bold")
    title = "Price context" if synthetic else "90-day price trend"
    fig.text(0.07, 0.744, title, fontsize=31, color="#f8fafc", weight="bold")
    fig.text(0.07, 0.724, story.company, fontsize=20, color="#cbd5e1")

    caption = scene.caption_text or scene.subheadline or scene.headline
    fig.patches.append(
        FancyBboxPatch(
            (0.08, 0.105),
            0.84,
            0.105,
            boxstyle="round,pad=0.014,rounding_size=0.02",
            facecolor="#020617",
            edgecolor="#1e293b",
            transform=fig.transFigure,
        )
    )
    fig.text(0.115, 0.155, _wrapped_text(caption, 34), fontsize=30, color="#f8fafc", weight="bold")
    source = "latest market snapshot" if synthetic else f"market data through {story.date}"
    fig.text(0.07, 0.055, f"Source: {source}", fontsize=17, color="#cbd5e1")
    fig.text(0.07, 0.035, story.disclaimer, fontsize=17, color="#94a3b8")


def _style_axes(chart_ax, volume_ax, dates: list[datetime], closes: list[float], volumes: list[int]) -> None:
    high = max(closes)
    low = min(closes)
    pad = max(1.0, (high - low) * 0.12)
    chart_ax.set_xlim(dates[0], dates[-1])
    chart_ax.set_ylim(low - pad, high + pad)
    chart_ax.grid(axis="y", color=GRID, linewidth=1.05)
    chart_ax.spines[["top", "right", "left"]].set_visible(False)
    chart_ax.spines["bottom"].set_color("#94a3b8")
    chart_ax.tick_params(axis="x", labelsize=13, colors=MUTED, pad=8)
    chart_ax.tick_params(axis="y", labelsize=14, colors=MUTED)
    chart_ax.xaxis.set_major_formatter(DateFormatter("%b %d"))
    chart_ax.set_ylabel("Close", color=MUTED, fontsize=14)

    volume_ax.set_ylim(0, max(volumes or [1]) * 1.18)
    volume_ax.spines[["top", "right", "left"]].set_visible(False)
    volume_ax.spines["bottom"].set_color("#94a3b8")
    volume_ax.tick_params(axis="x", labelsize=12, colors=MUTED)
    volume_ax.tick_params(axis="y", left=False, labelleft=False)
    volume_ax.set_title("Volume", loc="left", fontsize=14, color=MUTED, pad=8)


def _palette(move_color: str) -> dict[str, str]:
    try:
        import seaborn as sns

        colors = sns.color_palette("deep", 3).as_hex()
        return {"line": colors[0], "volume": colors[2]}
    except Exception:
        return {"line": TEAL if move_color != RED else RED, "volume": "#9fb8b8"}


def _synthetic_price_rows(story: Story, rows: list[dict]) -> list[dict]:
    latest_close = _latest_close(story, rows)
    latest_move = _parse_percent(story.price_card.change_pct)
    previous_close = latest_close / (1 + latest_move / 100) if latest_move != -100 else latest_close
    start_date = _story_date(story)
    points = 12
    values = []
    for index in range(points):
        progress = index / (points - 1)
        curve = progress * progress * (3 - 2 * progress)
        wiggle = ((index % 3) - 1) * max(0.05, latest_close * 0.0015)
        values.append(previous_close + (latest_close - previous_close) * curve + wiggle)
    values[-1] = latest_close
    return [
        {
            "date": (start_date - timedelta(days=points - index - 1)).strftime("%Y-%m-%d"),
            "close": max(0.01, value),
            "volume": 1_000 + index * 85,
            "change_percent": latest_move if index == points - 1 else 0.0,
        }
        for index, value in enumerate(values)
    ]


def _latest_close(story: Story, rows: list[dict]) -> float:
    if rows:
        parsed = _float(rows[-1].get("close"), 0.0)
        if parsed > 0:
            return parsed
    parsed_price = _parse_price(story.price_card.price)
    return parsed_price if parsed_price > 0 else 100.0


def _story_date(story: Story) -> datetime:
    try:
        return _parse_date(story.date)
    except ValueError:
        return datetime.now()


def _parse_price(value: str) -> float:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    return _float(text, 0.0)


def _parse_percent(value: str) -> float:
    text = str(value or "").replace("%", "").replace("+", "").strip()
    return _float(text, 0.0)


def _frame_count(duration: float, fps: int = FPS) -> int:
    return max(fps, int(round(max(0.5, duration) * fps)))


def _move_color(story: Story, latest_move: float) -> str:
    if story.price_card.direction == "up" or latest_move > 0:
        return GREEN
    if story.price_card.direction == "down" or latest_move < 0:
        return RED
    return YELLOW


def _parse_date(value: str) -> datetime:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d")


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _wrapped_text(text: str, width: int) -> str:
    lines = textwrap.wrap(str(text or ""), width=width)[:2]
    return "\n".join(lines)
