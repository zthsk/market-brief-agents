from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def media_duration(path: str | Path | None) -> float | None:
    target = _existing_path(path)
    if not target:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def media_resolution(path: str | Path | None) -> str:
    target = _existing_path(path)
    if not target:
        return "unknown"
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        match = re.search(r"(\d+)x(\d+)", output)
        if match:
            return f"{match.group(1)}x{match.group(2)}"
        return output or "unknown"
    except Exception:
        return "unknown"


def media_has_audio(path: str | Path | None) -> bool:
    target = _existing_path(path)
    if not target:
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return "audio" in result.stdout
    except Exception:
        return False


def probe_media(path: str | Path | None) -> tuple[float, str, bool]:
    return (
        media_duration(path) or 0.0,
        media_resolution(path),
        media_has_audio(path),
    )


def _existing_path(path: str | Path | None) -> Path | None:
    if shutil.which("ffprobe") is None or not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    return target
