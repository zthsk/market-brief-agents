from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from media_engine.paths import AUDIO_ROOT, SCRIPT_ROOT, artifact_stem_for_event
from models.database import execute, query
from services.gemini import gemini_configured, generate_tts_wav
from services.logging_utils import get_logger
from services.voice_profile import briefing_tts_prompt, package_transcript


LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class AudioGenerationResult:
    ok: bool
    path: str | None
    provider: str
    error: str | None = None
    retryable: bool = False
    status_code: int | None = None


def tts_provider_status() -> dict[str, str | bool]:
    provider = os.getenv("TTS_PROVIDER", "").lower().strip()
    if provider == "gemini":
        configured = gemini_configured()
        return {
            "provider": "gemini",
            "ready": configured,
            "message": "Gemini TTS ready" if configured else "Gemini TTS selected, but GEMINI_API_KEY is missing.",
        }
    if os.getenv("OPENAI_API_KEY"):
        return {"provider": "openai", "ready": True, "message": "OpenAI TTS ready"}
    return {
        "provider": provider or "none",
        "ready": False,
        "message": "No TTS provider configured. Set TTS_PROVIDER=gemini with GEMINI_API_KEY, or set OPENAI_API_KEY.",
    }


def generate_audio(script_id: int, script: str) -> str | None:
    return generate_audio_result(script_id, script).path


def generate_audio_result(script_id: int, script: str) -> AudioGenerationResult:
    stem = _script_artifact_stem(script_id)
    provider = os.getenv("TTS_PROVIDER", "").lower().strip()
    if provider == "gemini":
        if not gemini_configured():
            return AudioGenerationResult(
                ok=False,
                path=None,
                provider="gemini",
                error="Gemini TTS is selected, but GEMINI_API_KEY is missing.",
            )
        try:
            AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
            path = AUDIO_ROOT / f"{stem}.wav"
            transcript = _tts_transcript(stem, script)
            generate_tts_wav(transcript, path, voice_prompt=briefing_tts_prompt(transcript))
            execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (str(path), script_id))
            return AudioGenerationResult(ok=True, path=str(path), provider="gemini")
        except Exception as exc:
            LOGGER.warning("Skipping Gemini audio for script %s after error: %s", script_id, exc)
            return _error_result("gemini", exc)

    if provider and provider != "openai":
        return AudioGenerationResult(
            ok=False,
            path=None,
            provider=provider,
            error=f"Unsupported TTS_PROVIDER={provider!r}. Use 'gemini', 'openai', or leave it blank for OpenAI fallback.",
        )

    if not os.getenv("OPENAI_API_KEY"):
        LOGGER.info(
            "Skipping audio for script %s: no supported TTS provider is configured.",
            script_id,
        )
        return AudioGenerationResult(
            ok=False,
            path=None,
            provider="none",
            error="No TTS provider configured. Set TTS_PROVIDER=gemini with GEMINI_API_KEY, or set OPENAI_API_KEY.",
        )
    try:
        from openai import OpenAI

        AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
        path = AUDIO_ROOT / f"{stem}.mp3"
        client = OpenAI()
        response = client.audio.speech.create(
            model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
            input=script,
        )
        response.write_to_file(path)
        execute("UPDATE scripts SET audio_path = ? WHERE id = ?", (str(path), script_id))
        return AudioGenerationResult(ok=True, path=str(path), provider="openai")
    except Exception as exc:
        LOGGER.warning("Skipping audio for script %s after OpenAI error: %s", script_id, exc)
        return _error_result("openai", exc)


def _error_result(provider: str, exc: Exception) -> AudioGenerationResult:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    retryable = status_code in {408, 409, 425, 429, 500, 502, 503, 504}
    if status_code == 429:
        message = (
            f"{provider.title()} TTS is rate limited right now (HTTP 429). "
            "Wait a bit and retry, or switch TTS_PROVIDER to another configured provider."
        )
    elif status_code:
        message = f"{provider.title()} TTS failed with HTTP {status_code}: {exc}"
    else:
        message = f"{provider.title()} TTS failed: {exc}"
    return AudioGenerationResult(
        ok=False,
        path=None,
        provider=provider,
        error=message,
        retryable=retryable,
        status_code=status_code,
    )


def _script_artifact_stem(script_id: int) -> str:
    rows = query(
        """
        SELECT e.*
        FROM scripts s
        JOIN events e ON e.id = s.event_id
        WHERE s.id = ?
        """,
        (script_id,),
    )
    if rows:
        return artifact_stem_for_event(rows[0])
    return f"UNKNOWN_unknown_{script_id}"


def _tts_transcript(stem: str, fallback_script: str) -> str:
    package_path = SCRIPT_ROOT / stem / "script.json"
    package = _read_script_package(package_path)
    if package and _package_matches_script(package, fallback_script):
        transcript = package_transcript(package)
        if transcript:
            return transcript
    return fallback_script


def _package_matches_script(package: dict, script: str) -> bool:
    package_script = package.get("script")
    if not isinstance(package_script, str) or not package_script.strip():
        return False
    return _normalize_script_text(package_script) == _normalize_script_text(script)


def _normalize_script_text(value: str) -> str:
    return " ".join(value.split())


def _read_script_package(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    package = payload.get("package")
    return package if isinstance(package, dict) else None
