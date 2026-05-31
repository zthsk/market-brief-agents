from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path("data/market_brief_agents.db")


def db_path() -> Path:
    return Path(os.getenv("MARKET_BRIEF_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect(path: Path | None = None):
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                sector TEXT,
                industry TEXT,
                market_cap REAL
            );

            CREATE TABLE IF NOT EXISTS universe_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                universe TEXT NOT NULL,
                source TEXT NOT NULL,
                seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, universe)
            );

            CREATE TABLE IF NOT EXISTS daily_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                average_volume INTEGER,
                current_price REAL,
                change_percent REAL,
                market_cap REAL,
                UNIQUE(ticker, date)
            );

            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                published_at TEXT,
                headline TEXT NOT NULL,
                url TEXT,
                source TEXT,
                summary TEXT,
                UNIQUE(ticker, headline, url)
            );

            CREATE TABLE IF NOT EXISTS sec_filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                filing_type TEXT NOT NULL,
                filing_date TEXT NOT NULL,
                filing_url TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, filing_type, filing_date, filing_url)
            );

            CREATE TABLE IF NOT EXISTS earnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                earnings_date TEXT NOT NULL,
                eps_actual REAL,
                eps_estimate REAL,
                revenue_actual REAL,
                revenue_estimate REAL,
                UNIQUE(ticker, earnings_date)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT,
                score INTEGER NOT NULL,
                reason TEXT NOT NULL,
                analysis_json TEXT,
                status TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, event_type, created_at),
                UNIQUE(ticker, event_type, event_date)
            );

            CREATE TABLE IF NOT EXISTS daily_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                event_id INTEGER,
                event_date TEXT NOT NULL,
                event_score INTEGER NOT NULL,
                video_score INTEGER DEFAULT 0,
                decision TEXT NOT NULL,
                primary_bucket TEXT,
                bucket_memberships_json TEXT,
                selection_reason TEXT,
                rank_in_bucket INTEGER,
                candidate_stage TEXT NOT NULL,
                catalyst_confidence TEXT,
                source_quality TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, event_date),
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS research_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                provider TEXT NOT NULL,
                title TEXT,
                url TEXT NOT NULL,
                source TEXT,
                published_at TEXT,
                highlights_json TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, provider, url),
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                title TEXT,
                script TEXT NOT NULL,
                description TEXT,
                tags TEXT,
                audio_path TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                asset_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, asset_type),
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id INTEGER NOT NULL,
                video_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued_for_review',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(script_id),
                FOREIGN KEY(script_id) REFERENCES scripts(id)
            );

            CREATE TABLE IF NOT EXISTS template_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id TEXT NOT NULL,
                ticker TEXT,
                story_type TEXT,
                artifact_stem TEXT,
                used_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _ensure_column(conn, "events", "event_date", "TEXT")
        _ensure_column(conn, "research_sources", "metadata_json", "TEXT")
        _ensure_unique_index(conn, "idx_events_stable_key", "events", "ticker, event_type, event_date")
        _dedupe_videos(conn)
        _ensure_unique_index(conn, "idx_videos_script_id", "videos", "script_id")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_template_usage_lookup
            ON template_usage (ticker, story_type, used_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_template_usage_template_used
            ON template_usage (template_id, used_at)
            """
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_unique_index(
    conn: sqlite3.Connection, index_name: str, table: str, columns: str
) -> None:
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})")


def _dedupe_videos(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM videos
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM videos
            GROUP BY script_id
        )
        """
    )


def execute_many(sql: str, rows: Iterable[dict[str, Any]]) -> int:
    items = list(rows)
    if not items:
        return 0
    with connect() as conn:
        conn.executemany(sql, items)
    return len(items)


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, tuple(params))
        return int(cur.lastrowid or cur.rowcount)


def upsert_companies(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT INTO companies (ticker, name, sector, industry, market_cap)
        VALUES (:ticker, :name, :sector, :industry, :market_cap)
        ON CONFLICT(ticker) DO UPDATE SET
            name=excluded.name,
            sector=excluded.sector,
            industry=excluded.industry,
            market_cap=COALESCE(excluded.market_cap, companies.market_cap)
        """,
        rows,
    )


def upsert_universe_memberships(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT INTO universe_memberships (ticker, universe, source, seen_at)
        VALUES (:ticker, :universe, :source, COALESCE(:seen_at, CURRENT_TIMESTAMP))
        ON CONFLICT(ticker, universe) DO UPDATE SET
            source=excluded.source,
            seen_at=excluded.seen_at
        """,
        rows,
    )


def upsert_prices(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT INTO daily_prices (
            ticker, date, open, high, low, close, volume, average_volume,
            current_price, change_percent, market_cap
        )
        VALUES (
            :ticker, :date, :open, :high, :low, :close, :volume, :average_volume,
            :current_price, :change_percent, :market_cap
        )
        ON CONFLICT(ticker, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            average_volume=excluded.average_volume,
            current_price=excluded.current_price,
            change_percent=excluded.change_percent,
            market_cap=excluded.market_cap
        """,
        rows,
    )


def upsert_news(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT OR IGNORE INTO news (ticker, published_at, headline, url, source, summary)
        VALUES (:ticker, :published_at, :headline, :url, :source, :summary)
        """,
        rows,
    )


def upsert_filings(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT OR IGNORE INTO sec_filings (ticker, filing_type, filing_date, filing_url)
        VALUES (:ticker, :filing_type, :filing_date, :filing_url)
        """,
        rows,
    )


def upsert_research_sources(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT INTO research_sources (
            event_id, ticker, provider, title, url, source, published_at, highlights_json, metadata_json
        )
        VALUES (
            :event_id, :ticker, :provider, :title, :url, :source, :published_at, :highlights_json, :metadata_json
        )
        ON CONFLICT(event_id, provider, url) DO UPDATE SET
            title=excluded.title,
            source=excluded.source,
            published_at=excluded.published_at,
            highlights_json=excluded.highlights_json,
            metadata_json=excluded.metadata_json
        """,
        rows,
    )


def upsert_event(ticker: str, event_type: str, event_date: str, score: int, reason: str) -> int:
    return execute(
        """
        INSERT INTO events (ticker, event_type, event_date, score, reason)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker, event_type, event_date) DO UPDATE SET
            score=excluded.score,
            reason=excluded.reason,
            status='candidate'
        """,
        (ticker, event_type, event_date, score, reason),
    )


def upsert_daily_candidates(rows: Iterable[dict[str, Any]]) -> int:
    return execute_many(
        """
        INSERT INTO daily_candidates (
            ticker, event_id, event_date, event_score, video_score, decision,
            primary_bucket, bucket_memberships_json, selection_reason, rank_in_bucket,
            candidate_stage, catalyst_confidence, source_quality
        )
        VALUES (
            :ticker, :event_id, :event_date, :event_score, :video_score, :decision,
            :primary_bucket, :bucket_memberships_json, :selection_reason, :rank_in_bucket,
            :candidate_stage, :catalyst_confidence, :source_quality
        )
        ON CONFLICT(ticker, event_date) DO UPDATE SET
            event_id=excluded.event_id,
            event_score=excluded.event_score,
            video_score=excluded.video_score,
            decision=excluded.decision,
            primary_bucket=excluded.primary_bucket,
            bucket_memberships_json=excluded.bucket_memberships_json,
            selection_reason=excluded.selection_reason,
            rank_in_bucket=excluded.rank_in_bucket,
            candidate_stage=excluded.candidate_stage,
            catalyst_confidence=excluded.catalyst_confidence,
            source_quality=excluded.source_quality,
            updated_at=CURRENT_TIMESTAMP
        """,
        rows,
    )


def insert_event(ticker: str, event_type: str, score: int, reason: str) -> int:
    from datetime import date

    return upsert_event(ticker, event_type, date.today().isoformat(), score, reason)


def update_event_analysis(event_id: int, analysis: dict[str, Any]) -> None:
    execute(
        "UPDATE events SET analysis_json = ? WHERE id = ?",
        (json.dumps(analysis, sort_keys=True), event_id),
    )


def insert_script(event_id: int, payload: dict[str, Any]) -> int:
    return execute(
        """
        INSERT INTO scripts (event_id, title, script, description, tags, status)
        VALUES (?, ?, ?, ?, ?, 'draft')
        """,
        (
            event_id,
            payload.get("title"),
            payload["script"],
            payload.get("description"),
            json.dumps(payload.get("tags", [])),
        ),
    )


def upsert_video(script_id: int, video_path: str, status: str = "queued_for_review") -> int:
    return execute(
        """
        INSERT INTO videos (script_id, video_path, status)
        VALUES (?, ?, ?)
        ON CONFLICT(script_id) DO UPDATE SET
            video_path=excluded.video_path,
            status=excluded.status
        """,
        (script_id, video_path, status),
    )


def project_status() -> dict[str, Any]:
    init_db()
    latest_price = query("SELECT MAX(date) AS latest_price_date FROM daily_prices")[0][
        "latest_price_date"
    ]
    history = query(
        """
        SELECT
            COALESCE(SUM(price_days), 0) AS historical_price_rows,
            COUNT(DISTINCT ticker) AS tickers_with_prices,
            COALESCE(MAX(price_days), 0) AS max_price_days
        FROM (
            SELECT ticker, COUNT(DISTINCT date) AS price_days
            FROM daily_prices
            GROUP BY ticker
        )
        """
    )[0]
    counts = {
        "companies": query("SELECT COUNT(*) AS count FROM companies")[0]["count"],
        "daily_prices": query("SELECT COUNT(*) AS count FROM daily_prices")[0]["count"],
        "news": query("SELECT COUNT(*) AS count FROM news")[0]["count"],
        "filings": query("SELECT COUNT(*) AS count FROM sec_filings")[0]["count"],
        "earnings": query("SELECT COUNT(*) AS count FROM earnings")[0]["count"],
        "events": query("SELECT COUNT(*) AS count FROM events")[0]["count"],
        "research_sources": query("SELECT COUNT(*) AS count FROM research_sources")[0]["count"],
        "pending_events": query(
            """
            SELECT COUNT(*) AS count
            FROM events e
            LEFT JOIN scripts s ON s.event_id = e.id
            WHERE s.id IS NULL
            """
        )[0]["count"],
        "scripts": query("SELECT COUNT(*) AS count FROM scripts")[0]["count"],
        "videos": query("SELECT COUNT(*) AS count FROM videos")[0]["count"],
    }
    return {
        "db_path": str(db_path()),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        "exa_configured": bool(os.getenv("EXA_API_KEY")),
        "ai_provider": os.getenv("AI_PROVIDER", "openai"),
        "tts_provider": os.getenv("TTS_PROVIDER", "openai"),
        "web_search_provider": os.getenv("WEB_SEARCH_PROVIDER", "exa"),
        "sec_user_agent_configured": bool(os.getenv("SEC_USER_AGENT")),
        "latest_price_date": latest_price,
        "historical_price_rows": history["historical_price_rows"],
        "tickers_with_prices": history["tickers_with_prices"],
        "max_price_days": history["max_price_days"],
        "counts": counts,
    }
