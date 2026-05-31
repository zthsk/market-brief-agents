from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from media_engine.paths import (
    ASSET_ROOT,
    AUDIO_ROOT,
    RENDER_ROOT,
    REVIEW_ROOT,
    SCRIPT_ROOT,
    VIDEO_ROOT,
    artifact_stem_for_event,
)
from models.database import connect, query


GENERATED_ROOTS = (ASSET_ROOT, AUDIO_ROOT, RENDER_ROOT, REVIEW_ROOT, SCRIPT_ROOT, VIDEO_ROOT)


def event_production_artifact_summary(event_id: int) -> dict[str, Any]:
    event = _event(event_id)
    stem = artifact_stem_for_event(event)
    scripts = query("SELECT * FROM scripts WHERE event_id = ?", (event_id,))
    script_ids = [int(script["id"]) for script in scripts]
    videos = _videos_for_script_ids(script_ids)
    assets = query("SELECT * FROM assets WHERE event_id = ?", (event_id,))
    paths = _event_generated_paths(stem, event_id, scripts, videos, assets, include_assets=True)
    return {
        "event_id": event_id,
        "ticker": event["ticker"],
        "stem": stem,
        "scripts": len(scripts),
        "videos": len(videos),
        "assets": len(assets),
        "paths": [str(path) for path in paths],
    }


def event_video_artifact_summary(event_id: int) -> dict[str, Any]:
    event = _event(event_id)
    stem = artifact_stem_for_event(event)
    scripts = query("SELECT * FROM scripts WHERE event_id = ?", (event_id,))
    script_ids = [int(script["id"]) for script in scripts]
    videos = _videos_for_script_ids(script_ids)
    paths = _event_video_paths(stem, videos)
    return {
        "event_id": event_id,
        "ticker": event["ticker"],
        "stem": stem,
        "scripts": len(scripts),
        "videos": len(videos),
        "paths": [str(path) for path in paths],
    }


def script_production_artifact_summary(script_id: int) -> dict[str, Any]:
    rows = query(
        """
        SELECT s.*, e.ticker, e.event_date, e.created_at AS event_created_at
        FROM scripts s
        JOIN events e ON e.id = s.event_id
        WHERE s.id = ?
        """,
        (script_id,),
    )
    if not rows:
        return {"script_id": script_id, "paths": [], "error": f"No script found for id {script_id}"}
    script = rows[0]
    event = {
        "id": script["event_id"],
        "ticker": script["ticker"],
        "event_date": script.get("event_date") or script.get("event_created_at"),
    }
    stem = artifact_stem_for_event(event)
    videos = query("SELECT * FROM videos WHERE script_id = ?", (script_id,))
    paths = _dedupe_paths(
        [
            script.get("audio_path"),
            *[video.get("video_path") for video in videos],
            *AUDIO_ROOT.glob(f"{stem}*"),
            SCRIPT_ROOT / stem,
        ]
    )
    return {
        "script_id": script_id,
        "event_id": script["event_id"],
        "ticker": script["ticker"],
        "stem": stem,
        "videos": len(videos),
        "paths": [str(path) for path in paths],
        "error": None,
    }


def script_video_artifact_summary(script_id: int) -> dict[str, Any]:
    script = _script_with_event(script_id)
    if not script:
        return {"script_id": script_id, "paths": [], "error": f"No script found for id {script_id}"}
    stem = artifact_stem_for_event(_event_from_script(script))
    videos = query("SELECT * FROM videos WHERE script_id = ?", (script_id,))
    paths = _event_video_paths(stem, videos)
    return {
        "script_id": script_id,
        "event_id": script["event_id"],
        "ticker": script["ticker"],
        "stem": stem,
        "videos": len(videos),
        "paths": [str(path) for path in paths],
        "error": None,
    }


def delete_event_audio_artifacts(event_id: int) -> dict[str, Any]:
    event = _event(event_id)
    stem = artifact_stem_for_event(event)
    scripts = query("SELECT * FROM scripts WHERE event_id = ?", (event_id,))
    paths = _dedupe_paths(
        [
            *[script.get("audio_path") for script in scripts if script.get("audio_path")],
            *AUDIO_ROOT.glob(f"{stem}*"),
        ]
    )
    result = _remove_paths(paths)
    with connect() as conn:
        cur = conn.execute("UPDATE scripts SET audio_path = NULL WHERE event_id = ?", (event_id,))
        result["script_audio_rows_cleared"] = cur.rowcount
    return result


def delete_event_video_artifacts(event_id: int) -> dict[str, Any]:
    summary = event_video_artifact_summary(event_id)
    scripts = query("SELECT id FROM scripts WHERE event_id = ?", (event_id,))
    script_ids = [int(script["id"]) for script in scripts]
    result = _remove_paths([Path(path) for path in summary["paths"]])
    with connect() as conn:
        if script_ids:
            placeholders = ",".join("?" for _ in script_ids)
            video_cur = conn.execute(f"DELETE FROM videos WHERE script_id IN ({placeholders})", script_ids)
        else:
            video_cur = _ZeroRowCount()
    result["video_rows_deleted"] = video_cur.rowcount
    return result


def delete_script_video_artifacts(script_id: int) -> dict[str, Any]:
    summary = script_video_artifact_summary(script_id)
    if summary.get("error"):
        return _empty_result(error=summary["error"])
    result = _remove_paths([Path(path) for path in summary["paths"]])
    with connect() as conn:
        video_cur = conn.execute("DELETE FROM videos WHERE script_id = ?", (script_id,))
    result["video_rows_deleted"] = video_cur.rowcount
    return result


def delete_script_artifacts(script_id: int) -> dict[str, Any]:
    script = _script_with_event(script_id)
    if not script:
        return _empty_result(error=f"No script found for id {script_id}")

    event = _event_from_script(script)
    stem = artifact_stem_for_event(event)
    videos = query("SELECT * FROM videos WHERE script_id = ?", (script_id,))
    paths = _dedupe_paths(
        [
            script.get("audio_path"),
            *[video.get("video_path") for video in videos],
            *AUDIO_ROOT.glob(f"{stem}*"),
            SCRIPT_ROOT / stem,
        ]
    )
    result = _remove_paths(paths)
    with connect() as conn:
        video_cur = conn.execute("DELETE FROM videos WHERE script_id = ?", (script_id,))
        script_cur = conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        conn.execute(
            "UPDATE scripts SET audio_path = NULL WHERE event_id = ? AND audio_path IS NOT NULL",
            (script["event_id"],),
        )
    result["video_rows_deleted"] = video_cur.rowcount
    result["script_rows_deleted"] = script_cur.rowcount
    return result


def delete_event_production_artifacts(event_id: int) -> dict[str, Any]:
    summary = event_production_artifact_summary(event_id)
    scripts = query("SELECT * FROM scripts WHERE event_id = ?", (event_id,))
    script_ids = [int(script["id"]) for script in scripts]
    result = _remove_paths([Path(path) for path in summary["paths"]])
    with connect() as conn:
        if script_ids:
            placeholders = ",".join("?" for _ in script_ids)
            video_cur = conn.execute(f"DELETE FROM videos WHERE script_id IN ({placeholders})", script_ids)
        else:
            video_cur = _ZeroRowCount()
        script_cur = conn.execute("DELETE FROM scripts WHERE event_id = ?", (event_id,))
        asset_cur = conn.execute("DELETE FROM assets WHERE event_id = ?", (event_id,))
    result["video_rows_deleted"] = video_cur.rowcount
    result["script_rows_deleted"] = script_cur.rowcount
    result["asset_rows_deleted"] = asset_cur.rowcount
    return result


def _event(event_id: int) -> dict[str, Any]:
    rows = query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not rows:
        raise ValueError(f"No event found for id {event_id}")
    return rows[0]


def _script_with_event(script_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT s.*, e.ticker, e.event_date, e.created_at AS event_created_at
        FROM scripts s
        JOIN events e ON e.id = s.event_id
        WHERE s.id = ?
        """,
        (script_id,),
    )
    return rows[0] if rows else None


def _event_from_script(script: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": script["event_id"],
        "ticker": script["ticker"],
        "event_date": script.get("event_date") or script.get("event_created_at"),
    }


def _videos_for_script_ids(script_ids: list[int]) -> list[dict[str, Any]]:
    if not script_ids:
        return []
    placeholders = ",".join("?" for _ in script_ids)
    return query(f"SELECT * FROM videos WHERE script_id IN ({placeholders})", script_ids)


def _event_video_paths(stem: str, videos: list[dict[str, Any]]) -> list[Path]:
    return _dedupe_paths(
        [
            REVIEW_ROOT / stem,
            *RENDER_ROOT.glob(f"{stem}*"),
            *VIDEO_ROOT.glob(f"{stem}*"),
            *[video.get("video_path") for video in videos if video.get("video_path")],
        ]
    )


def _event_generated_paths(
    stem: str,
    event_id: int,
    scripts: list[dict[str, Any]],
    videos: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    *,
    include_assets: bool,
) -> list[Path]:
    paths = [
        SCRIPT_ROOT / stem,
        REVIEW_ROOT / stem,
        *AUDIO_ROOT.glob(f"{stem}*"),
        *RENDER_ROOT.glob(f"{stem}*"),
        *VIDEO_ROOT.glob(f"{stem}*"),
        *[script.get("audio_path") for script in scripts if script.get("audio_path")],
        *[video.get("video_path") for video in videos if video.get("video_path")],
    ]
    if include_assets:
        paths.append(ASSET_ROOT / str(event_id))
        paths.extend(asset.get("file_path") for asset in assets if asset.get("file_path"))
    return _dedupe_paths(paths)


def _dedupe_paths(paths: list[Any]) -> list[Path]:
    deduped: dict[str, Path] = {}
    for value in paths:
        if not value:
            continue
        path = Path(value)
        deduped[str(path)] = path
    return list(deduped.values())


def _remove_paths(paths: list[Path]) -> dict[str, Any]:
    result = _empty_result()
    for path in paths:
        if not _is_safe_generated_path(path):
            result["skipped_paths"].append(str(path))
            continue
        if not path.exists():
            result["missing_paths"].append(str(path))
            continue
        if path.is_dir():
            shutil.rmtree(path)
            result["dirs_removed"] += 1
        else:
            path.unlink()
            result["files_removed"] += 1
        result["removed_paths"].append(str(path))
    return result


def _is_safe_generated_path(path: Path) -> bool:
    base = Path.cwd().resolve()
    candidate = (base / path if not path.is_absolute() else path).resolve()
    allowed_roots = [(base / root).resolve() for root in GENERATED_ROOTS]
    return any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots)


def _empty_result(error: str | None = None) -> dict[str, Any]:
    return {
        "files_removed": 0,
        "dirs_removed": 0,
        "removed_paths": [],
        "missing_paths": [],
        "skipped_paths": [],
        "error": error,
    }


class _ZeroRowCount:
    rowcount = 0
