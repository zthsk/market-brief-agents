from pathlib import Path

from models.database import init_db, upsert_prices
from services.asset_generator import _chart_image


def test_chart_image_handles_sparse_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()

    path = _chart_image("AAPL", tmp_path / "sparse.png")

    assert path.exists()
    assert path.stat().st_size > 0


def test_chart_image_uses_price_history(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    init_db()
    upsert_prices(
        [
            {
                "ticker": "AAPL",
                "date": f"2026-03-{day:02d}",
                "open": 100 + day,
                "high": 101 + day,
                "low": 99 + day,
                "close": 100 + day,
                "volume": 1000 + day,
                "average_volume": 1000,
                "current_price": 100 + day,
                "change_percent": 1,
                "market_cap": 1_000_000_000,
            }
            for day in range(1, 31)
        ]
    )

    path = _chart_image("AAPL", tmp_path / "history.png")

    assert path.exists()
    assert path.stat().st_size > 0
