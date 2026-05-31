from __future__ import annotations

from pathlib import Path
import re


BRAND_ROOT = Path("assets/brand")
LOGO_PATH = BRAND_ROOT / "logo.png"
OUTRO_PATH = BRAND_ROOT / "outro.mp4"

DATA_ROOT = Path("data")
OUTPUT_ROOT = Path("outputs")
RESEARCH_ROOT = OUTPUT_ROOT / "research"
REVIEW_ROOT = OUTPUT_ROOT / "review"
SCRIPT_MANIFEST_ROOT = OUTPUT_ROOT / "script_manifests"
SCRIPT_ROOT = OUTPUT_ROOT / "scripts"

STORAGE_ROOT = Path("storage")
ASSET_ROOT = STORAGE_ROOT / "assets"
AUDIO_ROOT = STORAGE_ROOT / "audio"
RENDER_ROOT = STORAGE_ROOT / "render"
VIDEO_ROOT = Path("videos")


def artifact_stem(ticker: str, date: str, event_id: int) -> str:
    return _bundle_name(ticker, date, event_id)


def artifact_stem_for_event(event: dict) -> str:
    return artifact_stem(
        str(event.get("ticker") or "UNKNOWN"),
        str(event.get("event_date") or event.get("created_at") or "unknown"),
        int(event["id"]),
    )


def review_bundle_dir(ticker: str, date: str, event_id: int) -> Path:
    return REVIEW_ROOT / _bundle_name(ticker, date, event_id)


def research_dir(ticker: str, date: str, event_id: int) -> Path:
    return RESEARCH_ROOT / _bundle_name(ticker, date, event_id)


def script_manifest_dir(ticker: str, date: str, event_id: int) -> Path:
    return SCRIPT_MANIFEST_ROOT / _bundle_name(ticker, date, event_id)


def script_output_dir(ticker: str, date: str, event_id: int) -> Path:
    return SCRIPT_ROOT / _bundle_name(ticker, date, event_id)


def _bundle_name(ticker: str, date: str, event_id: int) -> str:
    safe_ticker = re.sub(r"[^A-Z0-9.-]+", "-", str(ticker or "UNKNOWN").upper()).strip("-")
    safe_date = str(date or "unknown")[:10]
    return f"{safe_ticker}_{safe_date}_{event_id}"
