from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(OPENAI|GEMINI|EXA)_API_KEY\s*=\s*[\"']?[^\"'\s]+"),
]
PRIVATE_BRAND = "Fin" + "Analyst"
BLOCKED_TEXT = (PRIVATE_BRAND, PRIVATE_BRAND.lower(), PRIVATE_BRAND.upper())
BLOCKED_PATH_PARTS = (
    ".env",
    "data/market_brief_agents.db",
    "data/" + PRIVATE_BRAND.lower() + ".db",
)
GENERATED_SUFFIXES = (
    ".db",
    ".mp4",
    ".mov",
    ".wav",
    ".mp3",
    ".aac",
)
GENERATED_DIRS = (
    "outputs/",
    "storage/assets/",
    "storage/audio/",
    "storage/render/",
    "videos/",
    "logs/",
)
ALLOWED_GENERATED_PLACEHOLDERS = {
    "outputs/.gitkeep",
    "outputs/research/.gitkeep",
    "outputs/review/.gitkeep",
    "outputs/script_manifests/.gitkeep",
    "outputs/scripts/.gitkeep",
    "storage/.gitkeep",
    "storage/assets/.gitkeep",
    "storage/audio/.gitkeep",
    "storage/render/.gitkeep",
    "videos/.gitkeep",
}


def main() -> int:
    failures: list[str] = []
    for path in _tracked_files():
        if not path.exists():
            continue
        rel = path.as_posix()
        _check_path(rel, failures)
        if _is_text_file(path):
            _check_text(path, rel, failures)
    if failures:
        print("Public cleanliness check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Public cleanliness check passed.")
    return 0


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        text=True,
        capture_output=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _check_path(rel: str, failures: list[str]) -> None:
    if rel in ALLOWED_GENERATED_PLACEHOLDERS:
        return
    if rel in BLOCKED_PATH_PARTS or rel.endswith("/.env"):
        failures.append(f"blocked local config path is tracked: {rel}")
    if any(rel.startswith(prefix) for prefix in GENERATED_DIRS):
        failures.append(f"generated artifact path is tracked: {rel}")
    if rel.endswith(GENERATED_SUFFIXES) and not rel.startswith("examples/"):
        failures.append(f"generated/binary artifact is tracked: {rel}")


def _check_text(path: Path, rel: str, failures: list[str]) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    for blocked in BLOCKED_TEXT:
        if blocked in text:
            failures.append(f"old private branding remains in {rel}: {blocked}")
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if value.endswith('=""') or value.endswith("=") or value.endswith("=''"):
                continue
            if "API_KEY=\"\"" in value or "API_KEY=''" in value:
                continue
            failures.append(f"possible secret in {rel}: {pattern.pattern}")


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in {
        "",
        ".cfg",
        ".css",
        ".env",
        ".html",
        ".json",
        ".md",
        ".mjs",
        ".plist",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }


if __name__ == "__main__":
    sys.exit(main())
