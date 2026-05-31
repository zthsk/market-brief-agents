from __future__ import annotations

import json
import importlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import streamlit as st

import jobs.pipeline as pipeline_module
import media_engine.renderer as media_renderer_module
import services.video_renderer as video_renderer_module
from jobs.pipeline import event_context
from media_engine.paths import script_output_dir
from media_engine.prep import prepare_event_story, prepare_top_events, update_prepared_story
from media_engine.renderer import review_bundles
from media_engine.script_manifest import prepare_script_manifest
from models.database import execute, init_db, insert_script, project_status, query
from services.artifact_cleanup import (
    delete_event_audio_artifacts,
    delete_event_production_artifacts,
    delete_event_video_artifacts,
    delete_script_artifacts,
    delete_script_video_artifacts,
    event_production_artifact_summary,
    event_video_artifact_summary,
    script_production_artifact_summary,
    script_video_artifact_summary,
)
from services.audio_generator import generate_audio_result, tts_provider_status
from services.config import load_env_file
from services.research_digest import build_research_digest, build_research_digests, load_research_digest
from services.script_generator import generate_script_from_manifest_path
from services.web_research import (
    approve_research_bundle,
    collect_event_research,
    collect_research_for_events,
    research_bundle_detail,
    research_bundle_for_event,
    research_bundle_summaries,
)


st.set_page_config(page_title="Market Brief Agents", layout="wide")
load_env_file()
init_db()

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.25rem; padding-bottom: 3rem; max-width: 1500px; }
    div[data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 8px;
        padding: 0.75rem 0.85rem;
    }
    div[data-testid="stMetricValue"] { font-size: 1.35rem; }
    .workflow-step {
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 8px;
        padding: 0.82rem;
        background: var(--secondary-background-color);
        min-height: 108px;
    }
    .workflow-step strong { font-size: 0.94rem; color: var(--text-color); }
    .workflow-step span { color: rgba(128, 128, 128, 0.95); font-size: 0.82rem; }
    .status-pill {
        display: inline-block;
        border-radius: 999px;
        padding: 0.15rem 0.55rem;
        font-size: 0.78rem;
        border: 1px solid rgba(128, 128, 128, 0.28);
        background: var(--secondary-background-color);
        margin-right: 0.25rem;
        margin-bottom: 0.25rem;
    }
    .status-ok { background: #ecfdf5; border-color: #a7f3d0; color: #065f46; }
    .status-warn { background: #fffbeb; border-color: #fde68a; color: #92400e; }
    .status-missing { background: var(--secondary-background-color); border-color: rgba(128, 128, 128, 0.28); color: rgba(128, 128, 128, 0.95); }
    .section-note { color: rgba(128, 128, 128, 0.95); font-size: 0.88rem; margin-top: -0.45rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_dashboard_notice() -> None:
    notice = st.session_state.pop("dashboard_notice", None)
    if not notice:
        return
    level, message = notice
    if level == "success":
        st.success(message)
    elif level == "warning":
        st.warning(message)
    else:
        st.info(message)


st.title("Market Brief Agents Operations")
render_dashboard_notice()
page = st.sidebar.radio(
    "Workspace",
    [
        "Command Center",
        "Daily Candidates",
        "Events",
        "Research Review",
        "Scripts & Videos",
        "Review Bundles",
        "Analytics",
    ],
)


def app_status() -> dict[str, Any]:
    return project_status()


def latest_candidate_date() -> str | None:
    return query("SELECT MAX(event_date) AS date FROM daily_candidates")[0]["date"]


def metric_grid(metrics: list[tuple[str, Any]], columns: int | None = None) -> None:
    cols = st.columns(columns or len(metrics))
    for col, (label, value) in zip(cols, metrics, strict=False):
        col.metric(label, value)


def workflow_steps() -> None:
    steps = [
        ("1. Universe", "Refresh S&P 500 and Yahoo watchlists."),
        ("2. Prices", "Collect latest prices and volume context."),
        ("3. Movers", "Rank gainers, losers, unusual volume, active, short-interest names."),
        ("4. Research", "Collect tiered sources only for balanced candidates."),
        ("5. Digest", "Create readable bullets, caveats, watch-items, and post drafts."),
        ("6. Approve", "Manual review gates script generation."),
        ("7. Script", "Build manifest, ask Gemini, validate output."),
        ("8. Produce", "Create TTS, render video, review artifacts."),
    ]
    for row_start in range(0, len(steps), 4):
        cols = st.columns(4)
        for col, (title, body) in zip(cols, steps[row_start : row_start + 4], strict=False):
            col.markdown(
                f'<div class="workflow-step"><strong>{title}</strong><br><span>{body}</span></div>',
                unsafe_allow_html=True,
            )


def candidate_rows(limit: int = 300) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT dc.*, e.reason, e.status AS event_status
        FROM daily_candidates dc
        LEFT JOIN events e ON e.id = dc.event_id
        ORDER BY dc.event_date DESC, dc.video_score DESC, dc.event_score DESC
        LIMIT ?
        """,
        (limit,),
    )
    for row in rows:
        row["bucket_memberships"] = parse_json(row.get("bucket_memberships_json"), [])
    return rows


def candidate_display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": row.get("event_id"),
            "ticker": row.get("ticker"),
            "date": row.get("event_date"),
            "decision": row.get("decision"),
            "stage": row.get("candidate_stage"),
            "event_score": row.get("event_score"),
            "video_score": row.get("video_score"),
            "bucket": row.get("primary_bucket"),
            "memberships": ", ".join(row.get("bucket_memberships") or []),
            "source_quality": row.get("source_quality"),
            "catalyst": row.get("catalyst_confidence"),
        }
        for row in rows
    ]


def event_rows(limit: int = 200) -> list[dict[str, Any]]:
    return query(
        """
        SELECT id AS event_id, ticker, event_date, event_type, score, status, reason, created_at
        FROM events
        ORDER BY COALESCE(event_date, date(created_at)) DESC, score DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )


def selectable_table(
    rows: list[dict[str, Any]],
    key: str,
    *,
    height: int = 340,
    note: str = "Select a row to inspect details below.",
) -> dict[str, Any] | None:
    if not rows:
        st.info("No rows to show.")
        return None
    event = st.dataframe(
        rows,
        width="stretch",
        height=height,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    selection = event.selection.rows
    if not selection:
        st.caption(note)
        return None
    return rows[selection[0]]


def selected_event_from_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    event_id = row.get("event_id") or row.get("id")
    if not event_id:
        return None
    rows = query("SELECT * FROM events WHERE id = ?", (int(event_id),))
    return rows[0] if rows else None


def script_manifest_path_for_event(event: dict[str, Any]) -> Path:
    from media_engine.paths import script_manifest_dir

    return script_manifest_dir(
        event["ticker"],
        str(event.get("event_date") or event.get("created_at") or "unknown")[:10],
        int(event["id"]),
    ) / "manifest.json"


def script_manifest_status(manifest_path: Path, bundle: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"ready": False, "message": "Build a script manifest first."}
    manifest = read_json_file(manifest_path)
    if bool(manifest.get("ready_for_gemini_script")):
        return {"ready": True, "message": "Script manifest is ready."}
    return {
        "ready": False,
        "message": script_manifest_blocked_message(manifest, bundle),
    }


def script_manifest_blocked_message(
    manifest: dict[str, Any],
    bundle: dict[str, Any] | None,
) -> str:
    review = manifest.get("research_review") if isinstance(manifest, dict) else {}
    review = review if isinstance(review, dict) else {}
    bundle_manifest = (bundle or {}).get("manifest", {})
    bundle_manifest = bundle_manifest if isinstance(bundle_manifest, dict) else {}
    manifest_approved = bool(review.get("approved"))
    bundle_approved = bool((bundle or {}).get("ready_for_script_generation"))
    manifest_sources = int(review.get("source_count") or 0)
    bundle_sources = int((bundle or {}).get("accepted_count") or bundle_manifest.get("accepted_count") or 0)
    reasons: list[str] = []
    if bundle_approved and not manifest_approved:
        reasons.append("the manifest was built before research approval; click Manifest again")
    elif not manifest_approved:
        reasons.append("approve the research bundle first")
    if bundle_sources > 0 and manifest_sources == 0:
        reasons.append("the manifest was built before accepted sources were available; click Manifest again")
    elif manifest_sources == 0:
        reasons.append("collect and accept at least one research source")
    if not reasons:
        reasons.append("click Manifest again to refresh the blocked manifest")
    return "; ".join(dict.fromkeys(reasons)) + "."


def latest_script_for_event(event_id: int) -> dict[str, Any] | None:
    rows = query(
        "SELECT * FROM scripts WHERE event_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (event_id,),
    )
    return rows[0] if rows else None


def scripts_for_event(event_id: int) -> list[dict[str, Any]]:
    return query("SELECT * FROM scripts WHERE event_id = ? ORDER BY created_at DESC, id DESC", (event_id,))


def videos_for_event(event_id: int) -> list[dict[str, Any]]:
    return query(
        """
        SELECT v.*
        FROM videos v
        JOIN scripts s ON s.id = v.script_id
        WHERE s.event_id = ?
        ORDER BY v.created_at DESC, v.id DESC
        """,
        (event_id,),
    )


def render_event_detail(event: dict[str, Any], key_prefix: str) -> None:
    event_id = int(event["id"])
    context = event_context(event)
    candidate = query(
        "SELECT * FROM daily_candidates WHERE event_id = ? ORDER BY updated_at DESC LIMIT 1",
        (event_id,),
    )
    scripts = scripts_for_event(event_id)
    videos = videos_for_event(event_id)
    bundle = research_bundle_for_event(event)
    manifest_path = script_manifest_path_for_event(event)
    latest_script = latest_script_for_event(event_id)
    digest = load_research_digest(bundle["bundle_path"]) if bundle else None

    st.markdown(f"### {event['ticker']} Workflow")
    metric_grid(
        [
            ("Event ID", event_id),
            ("Event Score", event.get("score")),
            ("Date", event.get("event_date") or str(event.get("created_at", ""))[:10]),
            ("Status", event.get("status")),
            ("Scripts", len(scripts)),
            ("Videos", len(videos)),
        ],
        columns=6,
    )
    st.caption(plain_text(event.get("reason")))
    render_pipeline_status(event, bundle, digest, manifest_path, latest_script, videos)
    render_event_workflow_controls(event, key_prefix)

    tabs = st.tabs(["Candidate", "Digest", "Research", "Manifest", "Script", "Media", "Audit"])
    with tabs[0]:
        if candidate:
            candidate[0]["bucket_memberships"] = parse_json(candidate[0].get("bucket_memberships_json"), [])
            st.json(candidate[0])
        else:
            st.info("No daily candidate decision stored for this event.")
        st.markdown("#### Price Context")
        st.json(context.get("price") or {})
        if event.get("analysis_json"):
            st.markdown("#### Analysis")
            st.json(parse_json(event.get("analysis_json"), {}))
    with tabs[1]:
        render_digest_panel(bundle, key_prefix)
    with tabs[2]:
        render_event_research_panel(event, context, key_prefix)
    with tabs[3]:
        if manifest_path.exists():
            st.json(read_json_file(manifest_path))
        else:
            st.info("No script manifest yet.")
    with tabs[4]:
        render_event_scripts(event_id)
    with tabs[5]:
        render_event_media(event_id)
    with tabs[6]:
        render_event_audit(event_id, bundle)


def render_pipeline_status(
    event: dict[str, Any],
    bundle: dict[str, Any] | None,
    digest: dict[str, Any] | None,
    manifest_path: Path,
    latest_script: dict[str, Any] | None,
    videos: list[dict[str, Any]],
) -> None:
    bundle_ready = bool(bundle)
    approved = bool((bundle or {}).get("ready_for_script_generation"))
    approval_mode = (bundle or {}).get("manifest", {}).get("approval_mode", "manual_required")
    scripts = scripts_for_event(int(event["id"]))
    discovered_audio = discover_audio_paths(event)
    has_audio = any(bool(script.get("audio_path")) for script in scripts) or bool(discovered_audio)
    statuses = [
        ("Research", bundle_ready),
        ("Digest", bool(digest)),
        ("Approved", approved),
        ("Manifest", manifest_path.exists()),
        ("Script", bool(latest_script)),
        ("Audio", has_audio),
        ("Video", bool(videos)),
    ]
    st.markdown(
        " ".join(
            f'<span class="status-pill {"status-ok" if ok else "status-missing"}">{label}: {"yes" if ok else "no"}</span>'
            for label, ok in statuses
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Research approval mode: "
        + (
            "auto-approved daily candidate"
            if approval_mode == "auto_daily_research_candidate"
            else "manual approval required"
        )
    )


def run_tts_for_script(script: dict[str, Any]) -> None:
    status = tts_provider_status()
    if not status["ready"]:
        st.session_state["dashboard_notice"] = ("warning", str(status["message"]))
        st.rerun()
    result = generate_audio_result(int(script["id"]), script["script"])
    if result.ok:
        st.session_state["dashboard_notice"] = (
            "success",
            f"Created {result.provider} audio for script #{script['id']}: {result.path}",
        )
    else:
        st.session_state["dashboard_notice"] = (
            "warning",
            f"No audio was created for script #{script['id']}. {result.error}",
        )
    st.rerun()


def render_delete_confirmation(
    *,
    title: str,
    warning: str,
    action_label: str,
    action,
    summary: dict[str, Any] | None = None,
) -> None:
    @st.dialog(title)
    def _dialog() -> None:
        st.warning(warning)
        if summary:
            st.caption("Artifacts that may be removed or detached:")
            st.json(summary, expanded=False)
        cols = st.columns(2)
        if cols[0].button(action_label, type="primary", key=f"{title}-{action_label}"):
            result = action()
            st.session_state["dashboard_notice"] = ("success", cleanup_result_message(result))
            st.rerun()
        if cols[1].button("Cancel", key=f"{title}-cancel"):
            st.rerun()

    _dialog()


def cleanup_result_message(result: dict[str, Any]) -> str:
    if result.get("error"):
        return str(result["error"])
    parts = [
        f"{result.get('files_removed', 0)} file(s)",
        f"{result.get('dirs_removed', 0)} folder(s)",
    ]
    for key, label in (
        ("script_rows_deleted", "script row(s)"),
        ("video_rows_deleted", "video row(s)"),
        ("asset_rows_deleted", "asset row(s)"),
        ("script_audio_rows_cleared", "script audio link(s)"),
    ):
        if key in result:
            parts.append(f"{result.get(key, 0)} {label}")
    if result.get("skipped_paths"):
        parts.append(f"{len(result['skipped_paths'])} unsafe path(s) skipped")
    return "Deleted " + ", ".join(parts) + "."


def dashboard_render_pipeline():
    importlib.reload(media_renderer_module)
    importlib.reload(video_renderer_module)
    return importlib.reload(pipeline_module)


def dashboard_render_script_video(script_id: int) -> dict[str, Any]:
    pipeline = dashboard_render_pipeline()
    return pipeline.render_script_video(script_id, renderer="remotion", force=True)


def dashboard_render_missing_videos() -> dict[str, Any]:
    pipeline = dashboard_render_pipeline()
    return pipeline.render_existing_videos(renderer="remotion")


def render_event_workflow_controls(event: dict[str, Any], key_prefix: str) -> None:
    event_id = int(event["id"])
    bundle = research_bundle_for_event(event)
    manifest_path = script_manifest_path_for_event(event)
    manifest_status = script_manifest_status(manifest_path, bundle)
    latest_script = latest_script_for_event(event_id)

    st.markdown("#### Workflow Actions")
    st.caption("Buttons may call configured AI, TTS, render, or research services only when clicked.")
    cols = st.columns(6)
    if cols[0].button("Collect Research", key=f"{key_prefix}-collect-research"):
        count = collect_event_research(
            event,
            force=True,
            providers=["exa", "google_news", "press_releases"],
        )
        st.success(f"Stored {count} source(s).")
        st.rerun()
    if cols[1].button("Approve", key=f"{key_prefix}-approve-research", disabled=not bundle):
        approve_research_bundle(bundle["bundle_path"])
        st.success("Research approved for script generation.")
        st.rerun()
    if cols[2].button("Manifest", key=f"{key_prefix}-build-manifest", disabled=not bundle):
        result = prepare_script_manifest(event_id)
        status = script_manifest_status(Path(result["manifest_path"]), research_bundle_for_event(event))
        if status["ready"]:
            st.session_state["dashboard_notice"] = (
                "success",
                f"Prepared manifest: {result['manifest_path']}",
            )
        else:
            st.session_state["dashboard_notice"] = (
                "warning",
                f"Prepared manifest, but Gemini Script is blocked: {status['message']}",
            )
        st.rerun()
    if cols[3].button(
        "Gemini Script",
        key=f"{key_prefix}-generate-script",
        disabled=not manifest_status["ready"],
    ):
        try:
            result = generate_script_from_manifest_path(manifest_path)
        except ValueError as exc:
            st.session_state["dashboard_notice"] = (
                "warning",
                f"{exc}. {script_manifest_status(manifest_path, bundle)['message']}",
            )
            st.rerun()
        script_id = insert_script(result["event_id"], result["db_fields"])
        st.success(f"Created script #{script_id}.")
        st.rerun()
    if manifest_path.exists() and not manifest_status["ready"]:
        st.warning(f"Gemini Script is disabled: {manifest_status['message']}")
    tts_status = tts_provider_status()
    if cols[4].button("TTS", key=f"{key_prefix}-tts", disabled=not latest_script or not tts_status["ready"]):
        run_tts_for_script(latest_script)
    if cols[5].button("Render", key=f"{key_prefix}-render", disabled=not latest_script):
        result = dashboard_render_script_video(int(latest_script["id"]))
        st.success("Rendered Remotion video.") if result["videos"] else st.warning("No video rendered.")
        st.rerun()
    discovered_audio = discover_audio_paths(event)
    if latest_script and discovered_audio and not latest_script.get("audio_path"):
        if st.button("Attach Existing Audio", key=f"{key_prefix}-attach-audio"):
            execute(
                "UPDATE scripts SET audio_path = ? WHERE id = ?",
                (str(discovered_audio[0]), latest_script["id"]),
            )
            st.success(f"Attached existing audio to script #{latest_script['id']}.")
            st.rerun()
    st.caption(f"TTS status: {tts_status['message']}")
    render_event_cleanup_controls(event_id, key_prefix)


def render_event_cleanup_controls(event_id: int, key_prefix: str) -> None:
    st.markdown("#### Cleanup")
    st.caption("Research, manifests, and digests are kept. These controls clear production artifacts so you can rebuild.")
    cols = st.columns(3)
    if cols[0].button("Delete Event Audio", key=f"{key_prefix}-delete-audio"):
        render_delete_confirmation(
            title="Confirm Audio Delete",
            warning="This clears generated audio files for this event and removes audio_path links from its scripts.",
            action_label="Delete audio",
            action=lambda: delete_event_audio_artifacts(event_id),
        )
    if cols[1].button("Delete Event Videos", key=f"{key_prefix}-delete-videos"):
        render_delete_confirmation(
            title="Confirm Video Delete",
            warning=(
                "This deletes rendered video files, review/render bundles, and video DB rows for this event. "
                "Scripts, audio, visual assets, research, manifests, and digests are kept."
            ),
            action_label="Delete videos",
            action=lambda: delete_event_video_artifacts(event_id),
            summary=event_video_artifact_summary(event_id),
        )
    if cols[2].button("Delete Production Artifacts", key=f"{key_prefix}-delete-production"):
        render_delete_confirmation(
            title="Confirm Production Artifact Delete",
            warning=(
                "This deletes event scripts, audio, videos, visual assets, script audit bundles, "
                "review/render bundles, and related DB rows. Research artifacts stay intact."
            ),
            action_label="Delete production artifacts",
            action=lambda: delete_event_production_artifacts(event_id),
            summary=event_production_artifact_summary(event_id),
        )


def render_digest_panel(bundle: dict[str, Any] | None, key_prefix: str) -> None:
    if not bundle:
        st.info("No research bundle yet.")
        return
    digest = load_research_digest(bundle["bundle_path"])
    if not digest:
        st.info("No digest yet. Daily refresh generates digests automatically for auto-approved research candidates.")
    else:
        st.caption(
            f"{digest.get('provider', 'unknown')} digest | source: {digest.get('digest_source', 'research_bundle')} | "
            f"confidence: {digest.get('confidence', 'unknown')} | generated: {str(digest.get('generated_at', ''))[:19]}"
        )
        cols = st.columns([2, 1])
        with cols[0]:
            st.markdown("#### Key Bullets")
            for bullet in digest.get("key_bullets") or []:
                st.write(f"- {plain_text(bullet)}")
            st.markdown("#### Why It Matters")
            st.write(plain_text(digest.get("why_it_matters")))
            st.markdown("#### Text Post Draft")
            st.text_area(
                "Saved text post",
                value=plain_text(digest.get("text_post")),
                height=150,
                key=f"{key_prefix}-text-post",
            )
        with cols[1]:
            st.markdown("#### Caveats")
            for caveat in digest.get("caveats") or []:
                st.write(f"- {plain_text(caveat)}")
            st.markdown("#### Watch")
            for item in digest.get("watch_items") or []:
                st.write(f"- {plain_text(item)}")
        with st.expander("Source Notes"):
            st.json(digest.get("source_notes") or [])
    with st.expander("Advanced Digest Regeneration"):
        cols = st.columns(3)
        if cols[0].button("Create Local Digest", key=f"{key_prefix}-local-digest"):
            build_research_digest(bundle["bundle_path"], use_gemini=False, force=True)
            st.success("Created local research digest.")
            st.rerun()
        if cols[1].button("Ask Gemini for Digest", key=f"{key_prefix}-gemini-digest"):
            build_research_digest(bundle["bundle_path"], use_gemini=True, force=True)
            st.success("Created Gemini research digest.")
            st.rerun()
        if cols[2].button("Batch Gemini Digests", key=f"{key_prefix}-batch-digest"):
            result = build_research_digests(limit=200, use_gemini=True, force=False)
            st.success(f"Created {result['digests']} digest(s) using {result['provider']}.")
            st.rerun()


def render_event_research_panel(event: dict[str, Any], context: dict[str, Any], key_prefix: str) -> None:
    research = query(
        """
        SELECT provider, title, source, published_at, url, metadata_json
        FROM research_sources
        WHERE event_id = ?
        ORDER BY provider, created_at DESC
        """,
        (int(event["id"]),),
    )
    st.dataframe(research, width="stretch", hide_index=True)
    bundle = research_bundle_for_event(event)
    if bundle:
        st.caption(f"Bundle: {bundle['bundle_path']}")
        accepted_tab, rejected_tab, news_tab, filings_tab = st.tabs(
            ["Accepted Sources", "Rejected Sources", "News", "Filings"]
        )
        with accepted_tab:
            for item in bundle["accepted"]:
                render_research_item(item)
        with rejected_tab:
            for item in bundle["rejected"]:
                render_research_item(item)
        with news_tab:
            st.dataframe(context.get("news") or [], width="stretch", hide_index=True)
        with filings_tab:
            st.dataframe(context.get("filings") or [], width="stretch", hide_index=True)


def render_event_scripts(event_id: int) -> None:
    scripts = scripts_for_event(event_id)
    if not scripts:
        st.info("No script generated yet.")
        return
    audit_summary = script_audit_summary(script_audit_bundle_for_event(event_id))
    selected = selectable_table(
        [
            {
                "script_id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "source": audit_summary.get("provider", "unknown"),
                "fallback": audit_summary.get("fallback", False),
                "audio": bool(item.get("audio_path")),
                "created_at": item.get("created_at"),
            }
            for item in scripts
        ],
        f"scripts-for-event-{event_id}",
        height=220,
    )
    script_id = int((selected or {}).get("script_id") or scripts[0]["id"])
    script = next(item for item in scripts if int(item["id"]) == script_id)
    st.markdown(f"#### #{script['id']} {plain_text(script.get('title'))}")
    st.write(script["script"])
    tts_status = tts_provider_status()
    st.caption(f"TTS status: {tts_status['message']}")
    cols = st.columns(6)
    if cols[0].button("Approve Script", key=f"approve-script-{script['id']}"):
        execute("UPDATE scripts SET status = 'approved' WHERE id = ?", (script["id"],))
        st.rerun()
    if cols[1].button("Reject Script", key=f"reject-script-{script['id']}"):
        execute("UPDATE scripts SET status = 'rejected' WHERE id = ?", (script["id"],))
        st.rerun()
    if cols[2].button("Create TTS", key=f"create-tts-script-{script['id']}", disabled=not tts_status["ready"]):
        run_tts_for_script(script)
    if cols[3].button("Prepare Story Bundle", key=f"prepare-story-{script['id']}"):
        result = prepare_event_story(event_id)
        st.success(f"Prepared bundle: {result['bundle_path']}")
        st.rerun()
    if cols[4].button("Delete Videos", key=f"delete-script-videos-{script['id']}"):
        render_delete_confirmation(
            title="Confirm Script Video Delete",
            warning=(
                "This deletes rendered video files, review/render bundles, and video DB rows for this script. "
                "The script row, audio, visual assets, research, manifests, and digests are kept."
            ),
            action_label="Delete script videos",
            action=lambda: delete_script_video_artifacts(int(script["id"])),
            summary=script_video_artifact_summary(int(script["id"])),
        )
    if cols[5].button("Delete Script", key=f"delete-script-{script['id']}"):
        render_delete_confirmation(
            title="Confirm Script Delete",
            warning=(
                "This deletes the selected script row, videos attached to it, event audio files, "
                "and the event script audit bundle so stale script/audio packages are not reused."
            ),
            action_label="Delete selected script",
            action=lambda: delete_script_artifacts(int(script["id"])),
            summary=script_production_artifact_summary(int(script["id"])),
        )
    if script.get("description"):
        st.markdown("#### Description")
        st.write(script["description"])
    if script.get("tags"):
        st.caption(f"Tags: {script['tags']}")


def render_event_media(event_id: int) -> None:
    event = query("SELECT * FROM events WHERE id = ?", (event_id,))[0]
    scripts = scripts_for_event(event_id)
    videos = videos_for_event(event_id)
    discovered_audio = discover_audio_paths(event)
    audit_summary = script_audit_summary(script_audit_bundle_for_event(event_id))
    st.markdown("#### Script / Audio Sources")
    st.dataframe(
        [
            {
                "script_id": script["id"],
                "status": script.get("status"),
                "script_source": audit_summary.get("provider", "unknown"),
                "script_fallback": audit_summary.get("fallback", False),
                "tts_source": tts_source_for_path(script.get("audio_path")),
                "audio_path": script.get("audio_path") or "missing",
                "audio_file_exists": bool(script.get("audio_path") and Path(script["audio_path"]).exists()),
                "created_at": script.get("created_at"),
            }
            for script in scripts
        ],
        width="stretch",
        hide_index=True,
    )
    audio_paths = [script.get("audio_path") for script in scripts if script.get("audio_path")]
    all_audio_paths = list(dict.fromkeys([*audio_paths, *[str(path) for path in discovered_audio]]))
    if all_audio_paths:
        st.markdown("#### Audio")
        if discovered_audio and not audio_paths:
            st.warning("Audio exists on disk but is not attached to the script row yet. Use Attach Existing Audio above.")
        for audio in all_audio_paths:
            render_audio_player(audio)
    else:
        st.warning("No audio path is stored for this event. Create TTS to generate and attach audio.")
    st.markdown("#### Video Sources")
    if not videos:
        st.info("No video rendered yet.")
    else:
        st.dataframe(
            [
                {
                    "video_id": item["id"],
                    "status": item["status"],
                    "source": "video_renderer",
                    "video_path": item["video_path"],
                    "file_exists": Path(item["video_path"]).exists(),
                    "created_at": item.get("created_at"),
                }
                for item in videos
            ],
            width="stretch",
            hide_index=True,
        )
        for item in videos:
            if Path(item["video_path"]).exists():
                st.video(item["video_path"])
            else:
                st.warning(f"Missing file: {item['video_path']}")


def render_event_audit(event_id: int, bundle: dict[str, Any] | None) -> None:
    tabs = st.tabs(["Artifact Sources", "Script Audit", "Research Files", "Assets"])
    with tabs[0]:
        render_artifact_sources(event_id, bundle)
    with tabs[1]:
        render_script_audit_panel(event_id)
    with tabs[2]:
        if not bundle:
            st.info("No research files yet.")
        else:
            render_bundle_files(bundle["bundle_path"])
    with tabs[3]:
        assets = query("SELECT asset_type, file_path FROM assets WHERE event_id = ?", (event_id,))
        if not assets:
            st.info("No assets generated yet.")
        for asset in assets:
            st.write(f"{asset['asset_type']}: {asset['file_path']}")
            if Path(asset["file_path"]).exists():
                st.image(asset["file_path"])


def render_artifact_sources(event_id: int, bundle: dict[str, Any] | None) -> None:
    event = query("SELECT * FROM events WHERE id = ?", (event_id,))[0]
    manifest_path = script_manifest_path_for_event(event)
    script_bundle = script_audit_bundle_for_event(event_id)
    digest_path = Path(bundle["bundle_path"]) / "research_digest.json" if bundle else None
    digest = load_research_digest(bundle["bundle_path"]) if bundle else None
    rows = [
        {
            "artifact": "research_bundle",
            "source": "web_research",
            "path_or_table": bundle["bundle_path"] if bundle else "missing",
            "exists": bool(bundle and Path(bundle["bundle_path"]).exists()),
            "action": "Review accepted/rejected sources; approve before scripting.",
        },
        {
            "artifact": "research_digest",
            "source": (
                f"{(digest or {}).get('provider', 'unknown')}:{(digest or {}).get('digest_source', 'missing')}"
                if digest
                else "missing"
            ),
            "path_or_table": str(digest_path) if digest_path else "missing",
            "exists": bool(digest_path and digest_path.exists()),
            "action": "Use for readable bullets and text posts.",
        },
        {
            "artifact": "script_manifest",
            "source": "script_manifest_builder",
            "path_or_table": str(manifest_path),
            "exists": manifest_path.exists(),
            "action": "Source-controlled prompt package for Gemini script generation.",
        },
        {
            "artifact": "script_audit_bundle",
            "source": "script_generator",
            "path_or_table": str(script_bundle) if script_bundle else "missing",
            "exists": bool(script_bundle and script_bundle.exists()),
            "action": "Inspect prompt, raw response, validation, repair, final script.",
        },
    ]
    audit_summary = script_audit_summary(script_bundle)
    for script in scripts_for_event(event_id):
        rows.append(
            {
                "artifact": f"script_{script['id']}",
                "source": f"script_generator:{audit_summary.get('provider', 'unknown')}",
                "path_or_table": f"scripts.id={script['id']}",
                "exists": True,
                "action": (
                    f"status={script.get('status')} "
                    f"fallback={audit_summary.get('fallback', False)} "
                    f"audio_path={script.get('audio_path') or 'missing'}"
                ),
            }
        )
        if script.get("audio_path"):
            rows.append(
                {
                    "artifact": f"audio_for_script_{script['id']}",
                    "source": f"tts:{tts_source_for_path(script['audio_path'])}",
                    "path_or_table": script["audio_path"],
                    "exists": Path(script["audio_path"]).exists(),
                    "action": "Create TTS again if missing or stale.",
                }
            )
    for audio_path in discover_audio_paths(event):
        rows.append(
            {
                "artifact": "discovered_audio",
                "source": f"storage/audio scan ({tts_source_for_path(str(audio_path))})",
                "path_or_table": str(audio_path),
                "exists": audio_path.exists(),
                "action": "Attach to script if scripts.audio_path is missing.",
            }
        )
    for video in videos_for_event(event_id):
        rows.append(
            {
                "artifact": f"video_{video['id']}",
                "source": "video_renderer",
                "path_or_table": video["video_path"],
                "exists": Path(video["video_path"]).exists(),
                "action": f"status={video.get('status')}",
            }
        )
    for asset in query("SELECT asset_type, file_path FROM assets WHERE event_id = ?", (event_id,)):
        rows.append(
            {
                "artifact": asset["asset_type"],
                "source": "assets table",
                "path_or_table": asset["file_path"],
                "exists": Path(asset["file_path"]).exists(),
                "action": "Visual/video support artifact.",
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)


def render_research_item(item: dict[str, Any]) -> None:
    title_col, source_col, tier_col = st.columns([4, 1, 1])
    title = plain_text(item.get("title") or "Untitled")
    source = plain_text(item.get("source") or item.get("author") or item.get("provider") or "Unknown")
    url = plain_text(item.get("url"))
    tier = (item.get("source_quality") or {}).get("tier") or item.get("source_tier") or "?"
    with title_col:
        if url:
            st.markdown(f"[{markdown_link_text(title)}]({url})")
        else:
            st.text(title)
    source_col.caption(source)
    tier_col.caption(f"Tier {tier}")
    policy = item.get("claim_use_policy")
    if policy:
        st.caption(plain_text(policy))
    for highlight in (item.get("highlights") or [])[:3]:
        st.text(f"- {plain_text(highlight)}")


def render_research_review_page() -> None:
    st.subheader("Research Review")
    st.caption("Select a bundle row to inspect digest, accepted sources, rejected sources, manifest, and files.")
    toolbar = st.columns([1, 1, 3])
    if toolbar[0].button("Collect Research Sources", key="collect-research-review"):
        result = collect_research_for_events(limit=5, providers=["exa", "google_news", "press_releases"])
        st.success(f"Collected research for {result['researched']} event(s), {result['sources']} source(s).")
        st.rerun()
    max_bundles = toolbar[1].selectbox("Bundles", [50, 100, 200, 300], index=1)
    summaries = research_bundle_summaries(limit=max_bundles)
    rows = research_summary_rows(summaries)
    selected = selectable_table(rows, "research-bundles-table", height=310)
    if not selected and rows:
        return
    bundle = research_bundle_detail(selected["bundle_path"]) if selected else None
    if bundle:
        render_bundle_detail(bundle, key_prefix=f"bundle-{selected['event_id']}")


def research_summary_rows(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for bundle in summaries:
        manifest = bundle["manifest"]
        digest = load_research_digest(bundle["bundle_path"])
        rows.append(
            {
                "event_id": manifest.get("event_id"),
                "ticker": manifest.get("ticker"),
                "date": manifest.get("date"),
                "stage": manifest.get("automation_stage", "unknown"),
                "approval_mode": manifest.get("approval_mode", "manual_required"),
                "approved": bundle["ready_for_script_generation"],
                "digest": (digest or {}).get("provider") if digest else "none",
                "accepted": bundle["accepted_count"],
                "rejected": bundle["rejected_count"],
                "bundle_path": bundle["bundle_path"],
            }
        )
    return rows


def render_bundle_detail(bundle: dict[str, Any], key_prefix: str) -> None:
    manifest = bundle["manifest"]
    st.markdown(f"### {manifest.get('ticker', 'Unknown')} Research Bundle")
    metric_grid(
        [
            ("Event ID", manifest.get("event_id")),
            ("Accepted", bundle["accepted_count"]),
            ("Rejected", bundle["rejected_count"]),
            ("Approved", str(bundle["ready_for_script_generation"])),
            ("Approval Mode", manifest.get("approval_mode", "manual_required")),
        ],
        columns=5,
    )
    tabs = st.tabs(["Digest", "Accepted", "Rejected", "Manifest", "Files"])
    with tabs[0]:
        render_digest_panel(bundle, key_prefix)
    with tabs[1]:
        cols = st.columns(2)
        is_auto = manifest.get("approval_mode") == "auto_daily_research_candidate"
        if cols[0].button("Approve Research", key=f"{key_prefix}-approve", disabled=is_auto):
            approve_research_bundle(bundle["bundle_path"], approval_mode="manual")
            st.success("Research approved.")
            st.rerun()
        if cols[1].button("Build Script Manifest", key=f"{key_prefix}-manifest"):
            result = prepare_script_manifest(int(manifest["event_id"]))
            st.success(f"Prepared manifest: {result['manifest_path']}")
            st.rerun()
        if is_auto:
            st.caption("Research was auto-approved because this event was in the daily research candidate pool.")
        for item in bundle["accepted"]:
            render_research_item(item)
    with tabs[2]:
        for item in bundle["rejected"]:
            render_research_item(item)
    with tabs[3]:
        event_rows = query("SELECT * FROM events WHERE id = ?", (manifest.get("event_id"),))
        if event_rows:
            manifest_path = script_manifest_path_for_event(event_rows[0])
            if manifest_path.exists():
                st.json(read_json_file(manifest_path))
                status = script_manifest_status(manifest_path, bundle)
                if st.button(
                    "Ask Gemini / Create Script",
                    key=f"{key_prefix}-generate-script",
                    disabled=not status["ready"],
                ):
                    result = generate_script_from_manifest_path(manifest_path)
                    script_id = insert_script(result["event_id"], result["db_fields"])
                    st.success(f"Created script #{script_id}.")
                    st.rerun()
                if not status["ready"]:
                    st.warning(f"Gemini Script is disabled: {status['message']}")
            else:
                st.info("No script manifest generated yet.")
    with tabs[4]:
        render_bundle_files(bundle["bundle_path"])


def render_bundle_files(bundle_path: str | Path) -> None:
    st.caption(f"Bundle: {bundle_path}")
    for path in sorted(Path(bundle_path).glob("*")):
        if path.is_file():
            with st.expander(path.name):
                show_json_or_text(path)


def render_scripts_videos_page() -> None:
    st.subheader("Scripts & Videos")
    cols = st.columns(2)
    if cols[0].button("Render Missing Videos", key="render-from-scripts"):
        result = dashboard_render_missing_videos()
        st.success(f"Rendered {result['videos']} video(s) from {result['eligible']} eligible script(s).")
        st.rerun()
    scripts = query(
        """
        SELECT s.id AS script_id, s.event_id, e.ticker, s.title, s.status,
               s.audio_path, s.created_at, v.video_path, v.status AS video_status
        FROM scripts s
        JOIN events e ON e.id = s.event_id
        LEFT JOIN videos v ON v.script_id = s.id
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT 150
        """
    )
    selected = selectable_table(scripts, "scripts-videos-table", height=340)
    if selected:
        event = selected_event_from_row(selected)
        if event:
            render_event_detail(event, f"script-page-{event['id']}")


def render_review_bundles_page() -> None:
    st.subheader("Rendered Video Review Bundles")
    if st.button("Prepare Story Bundles", key="prepare-from-review"):
        result = prepare_top_events(limit=5)
        st.success(f"Prepared {result['prepared']} story bundle(s).")
        st.rerun()
    bundles = review_bundles()
    rows = []
    for bundle in bundles:
        story = bundle["story"]
        manifest = bundle.get("manifest") or {}
        quality = bundle.get("quality") or {}
        sync = bundle.get("sync") or {}
        template_selection = bundle.get("template_selection") or {}
        rows.append(
            {
                "ticker": story.get("ticker"),
                "date": story.get("date"),
                "stage": manifest.get("automation_stage", "rendered"),
                "template": manifest.get("video_template_id")
                or template_selection.get("selected_template_id"),
                "story_type": manifest.get("template_story_type")
                or template_selection.get("story_type"),
                "ready": manifest.get("ready_for_posting", False),
                "sync": sync.get("passed"),
                "quality": quality.get("passed"),
                "warnings": len(quality.get("warnings") or []) + len(sync.get("warnings") or []),
                "duration": quality.get("final_duration_sec") or quality.get("duration_sec"),
                "bundle_path": bundle["bundle_path"],
            }
        )
    selected = selectable_table(rows, "review-bundles-table", height=300)
    if not selected:
        return
    bundle = next(item for item in bundles if item["bundle_path"] == selected["bundle_path"])
    render_review_bundle_detail(bundle)


def render_review_bundle_detail(bundle: dict[str, Any]) -> None:
    story = bundle["story"]
    quality = bundle["quality"]
    sync = bundle.get("sync") or {}
    production = bundle.get("production") or {}
    manifest = bundle.get("manifest") or {}
    template_selection = bundle.get("template_selection") or {}
    st.markdown(f"### {story.get('ticker', 'Unknown')} Review Bundle")
    video_path = Path(bundle["video_path"])
    if video_path.exists():
        st.video(str(video_path))
    elif Path(bundle["thumbnail_path"]).exists():
        st.image(bundle["thumbnail_path"])
    metric_grid(
        [
            ("Stage", manifest.get("automation_stage", "rendered")),
            (
                "Template",
                manifest.get("video_template_id")
                or template_selection.get("selected_template_id")
                or production.get("template_id")
                or "n/a",
            ),
            (
                "Story Type",
                manifest.get("template_story_type") or template_selection.get("story_type") or "n/a",
            ),
            ("Scenes", len(production.get("scenes") or [])),
            ("Content Est.", f"{float(manifest.get('content_duration_estimate_sec') or quality.get('content_duration_sec') or 0):.1f}s"),
            ("Final", f"{float(quality.get('final_duration_sec') or quality.get('duration_sec') or 0):.1f}s"),
            ("Sync", "PASS" if sync.get("passed") else "FAIL"),
            ("Quality", "PASS" if quality.get("passed") else "FAIL"),
            ("Ready", str(manifest.get("ready_for_posting", False))),
        ],
        columns=9,
    )
    if manifest.get("template_selection_reason") or template_selection.get("reason"):
        st.caption(f"Template reason: {manifest.get('template_selection_reason') or template_selection.get('reason')}")
    st.caption(
        f"Final video: {manifest.get('final_video_path') or bundle.get('video_path')} | "
        f"Review bundle: {bundle['bundle_path']}"
    )
    warnings = [*(quality.get("warnings") or []), *(sync.get("warnings") or [])]
    if warnings:
        st.warning("\n".join(f"- {warning}" for warning in warnings))
    story_tab, scenes_tab, captions_tab, template_tab, production_tab, sync_tab, quality_tab = st.tabs(
        ["Story", "Scenes", "Captions", "Template", "Production", "Sync", "Quality"]
    )
    with story_tab:
        st.json(story)
        hook = st.text_area("Edit hook", value=story.get("hook", ""), key=f"hook-{bundle['bundle_path']}")
        takeaway = st.text_area("Edit takeaway", value=story.get("takeaway", ""), key=f"takeaway-{bundle['bundle_path']}")
        if st.button("Save Story Edits", key=f"save-story-{bundle['bundle_path']}"):
            update_prepared_story(bundle["bundle_path"], hook=hook, takeaway=takeaway)
            st.success("Updated story, scenes, captions, and thumbnail.")
            st.rerun()
    with scenes_tab:
        show_json_or_text(Path(bundle["bundle_path"]) / "scenes.json")
    with captions_tab:
        path = Path(bundle["bundle_path"]) / "captions.srt"
        if path.exists():
            st.code(path.read_text(encoding="utf-8"), language="srt")
        captions_json = Path(bundle["bundle_path"]) / "captions.json"
        if captions_json.exists():
            with st.expander("Caption JSON"):
                show_json_or_text(captions_json)
    with template_tab:
        show_json_or_text(Path(bundle["bundle_path"]) / "template_selection.json")
    with production_tab:
        show_json_or_text(Path(bundle["bundle_path"]) / "production_plan.json")
    with sync_tab:
        show_json_or_text(Path(bundle["bundle_path"]) / "sync_report.json")
    with quality_tab:
        st.json(quality)


def render_audio_player(audio_path: str) -> None:
    path = Path(audio_path)
    st.caption(f"Audio: {audio_path}")
    if not path.exists():
        st.warning(f"Missing audio file: {audio_path}")
        return
    playable_path = browser_audio_path(path)
    mime = audio_mime(playable_path)
    st.audio(playable_path.read_bytes(), format=mime)


def discover_audio_paths(event: dict[str, Any]) -> list[Path]:
    event_date = str(event.get("event_date") or event.get("created_at") or "unknown")[:10]
    stem = f"{event['ticker']}_{event_date}_{int(event['id'])}"
    candidates = []
    for root in (Path("storage/audio"), Path("outputs/audio")):
        if not root.exists():
            continue
        for suffix in (".wav", ".mp3", ".m4a"):
            path = root / f"{stem}{suffix}"
            if path.exists():
                candidates.append(path)
        for path in root.glob(f"{stem}*"):
            if path.is_file() and path.suffix.lower() in {".wav", ".mp3", ".m4a"}:
                candidates.append(path)
    return list(dict.fromkeys(candidates))


def tts_source_for_path(audio_path: str | None) -> str:
    if not audio_path:
        return "missing"
    suffix = Path(audio_path).suffix.lower()
    if suffix == ".wav":
        return "gemini_tts"
    if suffix == ".mp3":
        return "openai_tts_or_preview"
    if suffix == ".m4a":
        return "tts_m4a"
    return "unknown_tts"


def browser_audio_path(path: Path) -> Path:
    if path.suffix.lower() != ".wav" or shutil.which("ffmpeg") is None:
        return path
    preview_dir = path.parent / "previews"
    preview = preview_dir / f"{path.stem}.mp3"
    if preview.exists() and preview.stat().st_mtime >= path.stat().st_mtime:
        return preview
    try:
        preview_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-vn", "-codec:a", "libmp3lame", "-b:a", "128k", str(preview)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return preview
    except Exception:
        return path


def audio_mime(path: Path) -> str:
    return {"mp3": "audio/mp3", "m4a": "audio/mp4", "wav": "audio/wav"}.get(path.suffix.lower().lstrip("."), "audio/*")


def script_audit_bundle_for_event(event_id: int) -> Path | None:
    rows = query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not rows:
        return None
    event = rows[0]
    return script_output_dir(
        event["ticker"],
        str(event.get("event_date") or event.get("created_at") or "unknown")[:10],
        int(event["id"]),
    )


def script_audit_summary(bundle: Path | None) -> dict[str, Any]:
    if not bundle:
        return {"provider": "unknown", "fallback": False, "errors": 0}
    script_json = read_json_file(bundle / "script.json")
    errors = read_json_file(bundle / "validation_errors.json")
    provider = script_json.get("provider") or "unknown"
    return {
        "provider": provider,
        "fallback": provider == "local" and (bundle / "raw_response_parsed.json").exists(),
        "errors": len(errors) if isinstance(errors, list) else 0,
    }


def render_script_audit_panel(event_id: int) -> None:
    bundle = script_audit_bundle_for_event(event_id)
    summary = script_audit_summary(bundle)
    metric_grid(
        [
            ("Generator", str(summary["provider"]).upper()),
            ("Fallback", "Yes" if summary["fallback"] else "No"),
            ("Validation Errors", summary["errors"]),
            ("Audit Bundle", "Yes" if bundle and bundle.exists() else "No"),
        ],
        columns=4,
    )
    if not bundle or not bundle.exists():
        st.info("No script audit bundle found for this event.")
        return
    audit_tabs = st.tabs(["Saved", "Validated", "Gemini Raw", "Repair", "Errors", "Prompt"])
    files = [
        bundle / "script.json",
        bundle / "validated_package.json",
        bundle / "raw_response_parsed.json" if (bundle / "raw_response_parsed.json").exists() else bundle / "raw_response.json",
        bundle / "repair_attempt.json",
        bundle / "validation_errors.json",
        bundle / "prompt.json",
    ]
    for tab, path in zip(audit_tabs, files, strict=False):
        with tab:
            show_json_or_text(path)


def show_json_or_text(path: Path) -> None:
    st.caption(str(path))
    if not path.exists():
        st.info("File not found.")
        return
    if path.suffix == ".json":
        payload = read_json_file(path)
        st.json(payload) if payload != {} else st.code(path.read_text(encoding="utf-8")[:8000])
        return
    st.code(path.read_text(encoding="utf-8")[:8000])


def read_json_file(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def plain_text(value: Any) -> str:
    return str(value or "").replace("<", "").replace(">", "")


def markdown_link_text(value: Any) -> str:
    return plain_text(value).replace("[", "\\[").replace("]", "\\]")


def render_command_center() -> None:
    st.subheader("Command Center")
    st.caption("End-to-end view of the daily market-to-video workflow.")
    status = app_status()
    metric_grid(
        [
            ("Companies", query("SELECT COUNT(*) AS count FROM companies")[0]["count"]),
            ("Latest Date", latest_candidate_date() or "none"),
            ("Candidates", query("SELECT COUNT(*) AS count FROM daily_candidates")[0]["count"]),
            ("Video Ready", query("SELECT COUNT(*) AS count FROM daily_candidates WHERE decision = 'video_ready'")[0]["count"]),
            ("Scripts", query("SELECT COUNT(*) AS count FROM scripts")[0]["count"]),
            ("Videos", query("SELECT COUNT(*) AS count FROM videos")[0]["count"]),
        ],
        columns=6,
    )
    st.caption(
        f"History: {status['historical_price_rows']} rows across {status['tickers_with_prices']} tickers. "
        f"AI: {status.get('ai_provider', 'openai')} | TTS: {status.get('tts_provider', 'openai')} | "
        f"Research: {status.get('web_search_provider', 'exa')}."
    )
    workflow_steps()
    st.markdown("### Video Candidate Queue")
    rows = candidate_rows(limit=100)
    ready_first = sorted(rows, key=lambda item: (item.get("decision") != "video_ready", -(item.get("video_score") or 0)))
    tickers = sorted({row["ticker"] for row in ready_first if row.get("ticker")})
    ticker_filter = st.multiselect("Filter ticker", tickers, default=[])
    display_rows = [
        row for row in ready_first[:100] if not ticker_filter or row.get("ticker") in ticker_filter
    ]
    selected = selectable_table(candidate_display_rows(display_rows), "command-center-candidates", height=310)
    event = selected_event_from_row(selected)
    if event:
        render_event_detail(event, f"command-{event['id']}")


def render_daily_candidates_page() -> None:
    st.subheader("Daily Candidates")
    rows = candidate_rows(limit=400)
    if not rows:
        st.info("No daily candidates yet.")
        return
    metric_grid(
        [
            ("Candidates", len(rows)),
            ("Video Ready", sum(1 for row in rows if row.get("decision") == "video_ready")),
            ("Needs Review", sum(1 for row in rows if row.get("decision") == "needs_manual_review")),
            ("Research Only", sum(1 for row in rows if row.get("decision") == "research_only")),
            ("Latest Date", rows[0].get("event_date") or "unknown"),
        ],
        columns=5,
    )
    cols = st.columns(3)
    decisions = sorted({row["decision"] for row in rows if row.get("decision")})
    buckets = sorted({row["primary_bucket"] for row in rows if row.get("primary_bucket")})
    tickers = sorted({row["ticker"] for row in rows if row.get("ticker")})
    decision_filter = cols[0].multiselect("Decision", decisions)
    bucket_filter = cols[1].multiselect("Bucket", buckets)
    ticker_filter = cols[2].multiselect("Ticker", tickers)
    filtered = [
        row
        for row in rows
        if (not decision_filter or row.get("decision") in decision_filter)
        and (not bucket_filter or row.get("primary_bucket") in bucket_filter)
        and (not ticker_filter or row.get("ticker") in ticker_filter)
    ]
    selected = selectable_table(candidate_display_rows(filtered), "daily-candidates-table", height=380)
    event = selected_event_from_row(selected)
    if event:
        render_event_detail(event, f"candidate-{event['id']}")


def render_events_page() -> None:
    st.subheader("Events")
    rows = event_rows(limit=250)
    selected = selectable_table(rows, "events-table", height=410)
    event = selected_event_from_row(selected)
    if event:
        render_event_detail(event, f"event-{event['id']}")


def render_analytics_page() -> None:
    st.subheader("Analytics")
    metric_grid(
        [
            ("Companies", query("SELECT COUNT(*) AS count FROM companies")[0]["count"]),
            ("Events", query("SELECT COUNT(*) AS count FROM events")[0]["count"]),
            ("Research Sources", query("SELECT COUNT(*) AS count FROM research_sources")[0]["count"]),
            ("Scripts", query("SELECT COUNT(*) AS count FROM scripts")[0]["count"]),
            ("Videos", query("SELECT COUNT(*) AS count FROM videos")[0]["count"]),
        ],
        columns=5,
    )
    tabs = st.tabs(["Publishing", "Candidate Decisions", "Source Tiers"])
    with tabs[0]:
        st.dataframe(
            query(
                """
                SELECT date(created_at) AS day, COUNT(*) AS videos
                FROM videos
                GROUP BY date(created_at)
                ORDER BY day DESC
                """
            ),
            width="stretch",
            hide_index=True,
        )
    with tabs[1]:
        st.dataframe(
            query(
                """
                SELECT event_date, decision, COUNT(*) AS count
                FROM daily_candidates
                GROUP BY event_date, decision
                ORDER BY event_date DESC, count DESC
                """
            ),
            width="stretch",
            hide_index=True,
        )
    with tabs[2]:
        rows = query("SELECT metadata_json FROM research_sources")
        counts: dict[str, int] = {}
        for row in rows:
            metadata = parse_json(row.get("metadata_json"), {})
            tier = f"Tier {metadata.get('source_tier', 'unknown')}"
            counts[tier] = counts.get(tier, 0) + 1
        st.dataframe([{"tier": tier, "count": count} for tier, count in sorted(counts.items())], width="stretch", hide_index=True)


if page == "Command Center":
    render_command_center()
elif page == "Daily Candidates":
    render_daily_candidates_page()
elif page == "Events":
    render_events_page()
elif page == "Research Review":
    render_research_review_page()
elif page == "Scripts & Videos":
    render_scripts_videos_page()
elif page == "Review Bundles":
    render_review_bundles_page()
elif page == "Analytics":
    render_analytics_page()
