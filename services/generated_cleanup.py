from __future__ import annotations

import shutil
from pathlib import Path

from media_engine.paths import ASSET_ROOT, AUDIO_ROOT, RENDER_ROOT, REVIEW_ROOT, SCRIPT_ROOT, VIDEO_ROOT
from models.database import execute


def clean_generated_content(root: Path | str = ".") -> dict[str, int]:
    base = Path(root)
    counts = {
        "video_rows": execute("DELETE FROM videos"),
        "script_rows": execute("DELETE FROM scripts"),
        "asset_rows": execute("DELETE FROM assets"),
        "asset_files": _remove_children(base / ASSET_ROOT),
        "script_audio_files": _remove_children(base / AUDIO_ROOT),
        "video_files": _remove_children(base / VIDEO_ROOT),
        "script_bundles": _remove_children(base / SCRIPT_ROOT),
        "review_bundles": _remove_children(base / REVIEW_ROOT),
        "render_files": _remove_children(base / RENDER_ROOT),
    }
    return counts


def _remove_children(folder: Path) -> int:
    if not folder.exists():
        return 0
    count = 0
    for path in folder.iterdir():
        if path.name == ".gitkeep":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        count += 1
    return count
