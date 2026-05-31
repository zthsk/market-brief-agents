from __future__ import annotations

import base64
import json
import os
import re
import wave
from pathlib import Path
from typing import Any

import requests

from services.voice_profile import briefing_tts_prompt


GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def generate_json(prompt: dict[str, Any], system_instruction: str) -> dict[str, Any]:
    text = generate_text(
        {
            "payload": prompt,
            "format": "Return a single valid JSON object. Do not wrap it in Markdown.",
        },
        system_instruction=system_instruction,
        response_mime_type="application/json",
    )
    return json.loads(_strip_json_fence(text))


def generate_text(
    prompt: Any,
    model: str | None = None,
    *,
    system_instruction: str | None = None,
    response_mime_type: str | None = None,
) -> str:
    model_name = model or os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    request_json: dict[str, Any] = {
        "contents": [{"parts": [{"text": json.dumps(prompt, default=str)}]}],
    }
    if system_instruction:
        request_json["system_instruction"] = {"parts": [{"text": system_instruction}]}
    if response_mime_type:
        request_json["generationConfig"] = {"responseMimeType": response_mime_type}
    response = requests.post(
        f"{GEMINI_API_BASE}/{model_name}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": _api_key()},
        json=request_json,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["candidates"][0]["content"]["parts"][0]["text"]


def generate_tts_wav(script: str, path: Path, voice_prompt: str | None = None) -> Path:
    model_name = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    voice_name = os.getenv("GEMINI_TTS_VOICE", "Kore")
    tts_prompt = voice_prompt or briefing_tts_prompt(script)
    response = requests.post(
        f"{GEMINI_API_BASE}/{model_name}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": _api_key()},
        json={
            "contents": [{"parts": [{"text": tts_prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice_name,
                        }
                    }
                },
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    encoded = payload["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    pcm = base64.b64decode(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(pcm)
    return path


def _api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return key


def _strip_json_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text.strip()


def _clean_script_for_speech(script: str) -> str:
    return re.sub(r"\b(HOOK|WHAT HAPPENED|WHY IT MATTERS|WHAT TO WATCH|CTA):\s*", "", script)
