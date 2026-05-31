from __future__ import annotations

from datetime import datetime
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
from PIL import Image, ImageDraw, ImageFont

from media_engine.paths import ASSET_ROOT
from models.database import execute, query


SIZE = (1080, 1920)


def generate_assets(event: dict, analysis: dict) -> dict[str, str]:
    event_id = int(event["id"])
    ticker = event["ticker"]
    target = ASSET_ROOT / str(event_id)
    target.mkdir(parents=True, exist_ok=True)
    assets = {
        "chart": str(_chart_image(ticker, target / "chart.png")),
        "company": str(_card(target / "company.png", ticker, event.get("reason", ""), "Company Focus")),
        "headline": str(_card(target / "headline.png", ticker, analysis.get("reason", ""), "Top Story")),
        "summary": str(_card(target / "summary.png", ticker, analysis.get("what_to_watch", ""), "What To Watch")),
    }
    for asset_type, file_path in assets.items():
        execute(
            """
            INSERT INTO assets (event_id, asset_type, file_path)
            VALUES (?, ?, ?)
            ON CONFLICT(event_id, asset_type) DO UPDATE SET file_path=excluded.file_path
            """,
            (event_id, asset_type, file_path),
        )
    return assets


def _chart_image(ticker: str, path: Path) -> Path:
    rows = query(
        """
        SELECT date, open, high, low, close, volume, average_volume, change_percent
        FROM daily_prices
        WHERE ticker = ? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 90
        """,
        (ticker,),
    )
    rows = list(reversed(rows))
    if len(rows) < 2:
        return _sparse_chart(path, ticker, rows)

    dates = [_parse_date(row["date"]) for row in rows]
    closes = [float(row["close"]) for row in rows]
    volumes = [int(row.get("volume") or 0) for row in rows]
    latest = rows[-1]
    latest_close = float(latest["close"])
    latest_move = float(latest.get("change_percent") or 0)
    period_change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0
    high = max(float(row.get("high") or row["close"]) for row in rows)
    low = min(float(row.get("low") or row["close"]) for row in rows)
    move_color = "#0f8a5f" if latest_move >= 0 else "#c44536"

    fig = plt.figure(figsize=(10.8, 19.2), dpi=100, facecolor="#f7f7f4")
    fig.text(0.07, 0.94, ticker, fontsize=58, color="#16202a", weight="bold")
    fig.text(0.07, 0.905, "90-day price trend", fontsize=26, color="#5d6b75")
    fig.text(0.07, 0.86, f"${latest_close:,.2f}", fontsize=52, color="#16202a", weight="bold")
    fig.text(0.43, 0.868, f"{latest_move:+.1f}% today", fontsize=30, color=move_color, weight="bold")
    fig.text(
        0.07,
        0.825,
        f"Period move {period_change:+.1f}%  |  Range ${low:,.2f}-${high:,.2f}",
        fontsize=22,
        color="#5d6b75",
    )

    chart_ax = fig.add_axes((0.07, 0.34, 0.86, 0.42), facecolor="#f7f7f4")
    chart_ax.plot(dates, closes, color="#1f6f8b", linewidth=5, solid_capstyle="round")
    chart_ax.fill_between(dates, closes, min(closes), color="#1f6f8b", alpha=0.10)
    chart_ax.scatter(dates[-1], closes[-1], s=170, color=move_color, zorder=5)
    chart_ax.annotate(
        f"{latest_move:+.1f}%",
        xy=(dates[-1], closes[-1]),
        xytext=(-105, 48),
        textcoords="offset points",
        fontsize=24,
        color="white",
        weight="bold",
        bbox={"boxstyle": "round,pad=0.45", "fc": move_color, "ec": "none"},
        arrowprops={"arrowstyle": "->", "color": move_color, "lw": 2},
    )
    chart_ax.grid(axis="y", color="#d8ddd8", linewidth=1.1)
    chart_ax.spines[["top", "right", "left"]].set_visible(False)
    chart_ax.spines["bottom"].set_color("#aab4b4")
    chart_ax.tick_params(axis="x", labelsize=16, colors="#5d6b75", rotation=0)
    chart_ax.tick_params(axis="y", labelsize=16, colors="#5d6b75")
    chart_ax.xaxis.set_major_formatter(DateFormatter("%b %d"))

    volume_ax = fig.add_axes((0.07, 0.20, 0.86, 0.10), facecolor="#f7f7f4", sharex=chart_ax)
    volume_colors = [move_color if index == len(volumes) - 1 else "#9fb8b8" for index in range(len(volumes))]
    volume_ax.bar(dates, volumes, color=volume_colors, width=1.0, alpha=0.85)
    volume_ax.set_title("Volume", loc="left", fontsize=18, color="#5d6b75", pad=8)
    volume_ax.spines[["top", "right", "left"]].set_visible(False)
    volume_ax.spines["bottom"].set_color("#aab4b4")
    volume_ax.tick_params(axis="x", labelsize=14, colors="#5d6b75")
    volume_ax.tick_params(axis="y", left=False, labelleft=False)
    volume_ax.xaxis.set_major_formatter(DateFormatter("%b %d"))

    fig.text(0.07, 0.105, "Market Brief Agents", fontsize=24, color="#16202a", weight="bold")
    fig.text(0.07, 0.08, "Financial news, not investment advice", fontsize=20, color="#5d6b75")
    fig.text(0.07, 0.055, f"Source: yfinance daily data through {rows[-1]['date']}", fontsize=17, color="#69757f")
    plt.savefig(path, facecolor=fig.get_facecolor())
    plt.close()
    return path


def _sparse_chart(path: Path, ticker: str, rows: list[dict]) -> Path:
    latest = rows[-1] if rows else {}
    latest_close = latest.get("close")
    price = float(latest_close) if latest_close else 100.0
    image = Image.new("RGB", SIZE, "#f7f7f4")
    draw = ImageDraw.Draw(image)
    label_font = _font(34)
    title_font = _font(86)
    stat_font = _font(58)
    body_font = _font(36)
    draw.rectangle((0, 0, SIZE[0], 220), fill="#16202a")
    draw.text((70, 70), "PRICE CONTEXT", font=label_font, fill="#b8d8d8")
    draw.text((70, 270), ticker, font=title_font, fill="#16202a")
    draw.text((70, 390), f"${price:,.2f}", font=stat_font, fill="#16202a")
    draw.text((70, 470), "Latest market snapshot", font=body_font, fill="#5d6b75")

    left, top, right, bottom = 90, 700, 990, 1220
    draw.rounded_rectangle((left, top, right, bottom), radius=32, fill="#ffffff", outline="#d8ddd8", width=3)
    points = [
        (left + 70, bottom - 120),
        (left + 190, bottom - 180),
        (left + 310, bottom - 145),
        (left + 450, bottom - 255),
        (left + 590, bottom - 220),
        (left + 730, bottom - 315),
        (right - 70, bottom - 280),
    ]
    draw.line(points, fill="#1f6f8b", width=9, joint="curve")
    draw.ellipse((right - 92, bottom - 302, right - 48, bottom - 258), fill="#0f8a5f")
    draw.text((left + 58, top + 56), "Price path updates as history builds", font=body_font, fill="#25313b")
    draw.text((70, 1760), "Market Brief Agents | financial news, not investment advice", font=label_font, fill="#5d6b75")
    image.save(path)
    return path


def _parse_date(value: str) -> datetime:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d")


def _card(path: Path, ticker: str, body: str, label: str) -> Path:
    image = Image.new("RGB", SIZE, "#f7f7f4")
    draw = ImageDraw.Draw(image)
    title_font = _font(86)
    label_font = _font(34)
    body_font = _font(52)
    draw.rectangle((0, 0, SIZE[0], 220), fill="#16202a")
    draw.text((70, 70), label.upper(), font=label_font, fill="#b8d8d8")
    draw.text((70, 270), ticker, font=title_font, fill="#16202a")
    wrapped = textwrap.wrap(body or "No summary available yet.", width=26)[:10]
    y = 430
    for line in wrapped:
        draw.text((70, y), line, font=body_font, fill="#25313b")
        y += 72
    draw.text((70, 1760), "Market Brief Agents | financial news, not investment advice", font=label_font, fill="#5d6b75")
    image.save(path)
    return path


def _font(size: int):
    for name in ("Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()
