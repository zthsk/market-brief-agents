import json
from pathlib import Path

import requests

from models.database import connect, init_db
from services.audio_generator import generate_audio, generate_audio_result


def test_gemini_audio_updates_script_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.audio_generator.gemini_configured", lambda: True)

    def fake_tts(script, path, voice_prompt=None):
        path.write_bytes(b"fake wav")
        return path

    monkeypatch.setattr("services.audio_generator.generate_tts_wav", fake_tts)
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'AAPL', 'story_candidate', '2026-05-29', 80, 'reason')"
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'script')")

    path = generate_audio(1, "script")

    assert path == "storage/audio/AAPL_2026-05-29_1.wav"
    with connect() as conn:
        row = conn.execute("SELECT audio_path FROM scripts WHERE id = 1").fetchone()
    assert row["audio_path"] == "storage/audio/AAPL_2026-05-29_1.wav"


def test_gemini_audio_uses_script_package_delivery_cues(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.audio_generator.gemini_configured", lambda: True)
    calls = []

    def fake_tts(script, path, voice_prompt=None):
        calls.append({"script": script, "voice_prompt": voice_prompt})
        path.write_bytes(b"fake wav")
        return path

    monkeypatch.setattr("services.audio_generator.generate_tts_wav", fake_tts)
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'AAPL', 'story_candidate', '2026-05-29', 80, 'reason')"
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'plain script')")
    package_dir = Path("outputs/scripts/AAPL_2026-05-29_1")
    package_dir.mkdir(parents=True)
    (package_dir / "script.json").write_text(
        json.dumps(
            {
                "package": {
                    "script": "plain script",
                    "narration_segments": [
                        {
                            "segment": "hook",
                            "text": "Apple just made a sharp move.",
                            "tone": "curious_urgent",
                            "emphasis_terms": ["Apple", "sharp move"],
                        },
                        {
                            "segment": "risk",
                            "text": "The caveat is that the catalyst still needs confirmation.",
                            "tone": "cautious",
                            "emphasis_terms": ["caveat"],
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    generate_audio(1, "plain script")

    assert "[curious, crisp hook" in calls[0]["script"]
    assert "emphasize: Apple, sharp move" in calls[0]["script"]
    assert "Bracketed cues are delivery direction only" in calls[0]["voice_prompt"]


def test_gemini_audio_ignores_stale_script_package(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.audio_generator.gemini_configured", lambda: True)
    calls = []

    def fake_tts(script, path, voice_prompt=None):
        calls.append(script)
        path.write_bytes(b"fake wav")
        return path

    monkeypatch.setattr("services.audio_generator.generate_tts_wav", fake_tts)
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'NOW', 'story_candidate', '2026-05-29', 80, 'reason')"
        )
        conn.execute(
            "INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'new dashboard script')"
        )
    package_dir = Path("outputs/scripts/NOW_2026-05-29_1")
    package_dir.mkdir(parents=True)
    (package_dir / "script.json").write_text(
        json.dumps(
            {
                "package": {
                    "script": "old local script",
                    "narration_segments": [
                        {
                            "segment": "hook",
                            "text": "Old local script text.",
                            "tone": "curious_urgent",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    generate_audio(1, "new dashboard script")

    assert calls == ["new dashboard script"]


def test_gemini_audio_rate_limit_returns_clear_error_without_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MARKET_BRIEF_DB_PATH", str(tmp_path / "market_brief_agents.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("services.audio_generator.gemini_configured", lambda: True)

    def fake_tts(script, path, voice_prompt=None):
        response = requests.Response()
        response.status_code = 429
        response.url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-tts:generateContent"
        error = requests.HTTPError("429 Client Error: Too Many Requests")
        error.response = response
        raise error

    monkeypatch.setattr("services.audio_generator.generate_tts_wav", fake_tts)
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (id, ticker, event_type, event_date, score, reason) VALUES (1, 'NOW', 'story_candidate', '2026-05-29', 80, 'reason')"
        )
        conn.execute("INSERT INTO scripts (id, event_id, script) VALUES (1, 1, 'script')")

    result = generate_audio_result(1, "script")

    assert result.ok is False
    assert result.path is None
    assert result.provider == "gemini"
    assert result.status_code == 429
    assert result.retryable is True
    assert "rate limited" in result.error
    assert "No TTS provider configured" not in result.error
    assert query_audio_path() is None


def query_audio_path():
    with connect() as conn:
        row = conn.execute("SELECT audio_path FROM scripts WHERE id = 1").fetchone()
    return row["audio_path"]
