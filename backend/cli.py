from __future__ import annotations

import argparse

from agentic.graph import inspect_agent_run, list_agent_runs, run_agent_pipeline
from jobs.autopilot import run_weekday_autopilot
from jobs.daily_refresh import daily_market_refresh, movers_report
from jobs.pipeline import as_json, generate_for_events, render_existing_videos, run_pipeline, status_report
from media_engine.prep import prepare_event_story, prepare_top_events
from media_engine.script_manifest import (
    normalize_script_manifests,
    prepare_script_manifest,
    prepare_script_manifests,
)
from models.database import init_db, insert_script
from services.config import load_env_file
from services.company_universe import seed_companies
from services.earnings import collect_earnings
from services.event_detector import detect_events
from services.generated_cleanup import clean_generated_content
from services.market_data import collect_history, collect_news, collect_prices, company_tickers
from services.research_digest import DEFAULT_DIGEST_BATCH_SIZE, build_research_digest, build_research_digests
from services.sec_filings import collect_filings
from services.demo_data import load_synthetic_demo_data
from services.script_generator import generate_script_from_manifest_path
from services.web_research import collect_event_research, collect_research_for_events


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(prog="marketbrief")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")
    subparsers.add_parser("status")
    subparsers.add_parser("clean-generated-content")
    subparsers.add_parser("seed-companies")
    subparsers.add_parser("load-demo-data")
    _limit_parser(subparsers, "collect-prices")
    history = _limit_parser(subparsers, "collect-history")
    history.add_argument("--period", default="6mo")
    _limit_parser(subparsers, "collect-news")
    research = _limit_parser(subparsers, "collect-research")
    research.add_argument("--event-id", type=int, default=None)
    research.add_argument("--force", action="store_true")
    research.add_argument(
        "--providers",
        default=None,
        help="Comma-separated providers: all, exa, google-news, press-releases.",
    )
    _limit_parser(subparsers, "collect-filings")
    _limit_parser(subparsers, "collect-earnings")
    subparsers.add_parser("detect-events")
    generate = _limit_parser(subparsers, "generate-content")
    generate.add_argument("--skip-video", action="store_true")
    prepare = _limit_parser(subparsers, "prepare-video-story")
    prepare.add_argument("--event-id", type=int, default=None)
    prepare.add_argument("--template", default="news-studio")
    prepare.add_argument("--max-duration", type=int, default=75)
    script_manifest = _limit_parser(subparsers, "prepare-script-manifest")
    script_manifest.add_argument("--event-id", type=int, default=None)
    normalize_manifest = subparsers.add_parser("normalize-script-manifests")
    normalize_manifest.add_argument("--root", default="outputs/script_manifests")
    script_from_manifest = _limit_parser(subparsers, "generate-script-from-manifest")
    script_from_manifest.add_argument("--event-id", type=int, default=None)
    script_from_manifest.add_argument("--manifest-path", default=None)
    digest = _limit_parser(subparsers, "build-research-digests")
    digest.add_argument("--bundle-path", default=None)
    digest.add_argument("--gemini", action="store_true")
    digest.add_argument("--batch-size", type=int, default=DEFAULT_DIGEST_BATCH_SIZE)
    digest.add_argument("--force", action="store_true")
    render = _limit_parser(subparsers, "render-videos")
    render.add_argument("--approved-only", action="store_true")
    render.add_argument("--template", default="news-studio")
    render.add_argument("--max-duration", type=int, default=75)
    render.add_argument("--captions", dest="captions", action="store_true", default=True)
    render.add_argument("--no-captions", dest="captions", action="store_false")
    render.add_argument("--renderer", choices=["python", "remotion"], default=None)
    pipeline = _limit_parser(subparsers, "run-pipeline")
    pipeline.add_argument("--skip-video", action="store_true")
    daily = subparsers.add_parser("daily-market-refresh")
    daily.add_argument("--extended-size", type=int, default=100)
    daily.add_argument("--top-movers", type=int, default=80)
    daily.add_argument("--research-limit", type=int, default=30)
    daily.add_argument("--video-limit", type=int, default=5)
    daily.add_argument("--min-event-score", type=int, default=40)
    daily.add_argument("--min-video-score", type=int, default=70)
    daily.add_argument("--force-research", action="store_true")
    daily.add_argument("--refresh-stale-research-after-hours", type=int, default=24)
    daily.add_argument(
        "--force-if-no-tier1-or-tier2-sources",
        action="store_true",
        default=True,
    )
    daily.add_argument("--skip-local-digests", action="store_true")
    daily.add_argument(
        "--gemini-digest-video-ready",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    daily.add_argument(
        "--gemini-digest-research-ready-batch",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    daily.add_argument("--digest-batch-size", type=int, default=DEFAULT_DIGEST_BATCH_SIZE)
    autopilot = subparsers.add_parser("weekday-autopilot")
    autopilot.add_argument("--video-limit", type=int, default=3)
    autopilot.add_argument("--renderer", choices=["python", "remotion"], default="remotion")
    autopilot.add_argument("--today", default=None)
    autopilot.add_argument("--extended-size", type=int, default=100)
    autopilot.add_argument("--top-movers", type=int, default=80)
    autopilot.add_argument("--research-limit", type=int, default=30)
    autopilot.add_argument("--min-event-score", type=int, default=40)
    autopilot.add_argument("--min-video-score", type=int, default=70)
    autopilot.add_argument("--force-research", action="store_true")
    autopilot.add_argument("--refresh-stale-research-after-hours", type=int, default=24)
    autopilot.add_argument(
        "--force-if-no-tier1-or-tier2-sources",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    autopilot.add_argument("--force-script", action="store_true")
    autopilot.add_argument("--force-render", action="store_true")
    autopilot.add_argument("--skip-tts", action="store_true")
    autopilot.add_argument("--skip-render", action="store_true")
    autopilot.add_argument("--gemini-digests", action="store_true")
    autopilot.add_argument("--digest-batch-size", type=int, default=DEFAULT_DIGEST_BATCH_SIZE)
    movers = subparsers.add_parser("movers-report")
    movers.add_argument("--date", default="latest")
    movers.add_argument("--limit", type=int, default=25)
    agent = subparsers.add_parser("run-agent-pipeline")
    agent.add_argument("--demo", action="store_true")
    agent.add_argument("--thread-id", default=None)
    agent.add_argument("--skip-render", action=argparse.BooleanOptionalAction, default=True)
    agent.add_argument("--force-script", action="store_true")
    agent.add_argument("--event-id", type=int, action="append", default=None)
    agent.add_argument("--interrupt-before-script", action="store_true")
    agent.add_argument("--interrupt-before-render", action="store_true")
    agent.add_argument("--resume", action="store_true")
    agent.add_argument("--checkpoint-path", default=None)
    inspect_agent = subparsers.add_parser("inspect-agent-run")
    inspect_agent.add_argument("--thread-id", required=True)
    list_agent = subparsers.add_parser("list-agent-runs")
    list_agent.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
        print("Initialized SQLite database.")
    elif args.command == "status":
        print(as_json(status_report()))
    elif args.command == "clean-generated-content":
        print(as_json(clean_generated_content()))
    elif args.command == "seed-companies":
        print(f"Seeded {seed_companies()} companies.")
    elif args.command == "load-demo-data":
        print(as_json(load_synthetic_demo_data()))
    elif args.command == "collect-prices":
        print(f"Stored {collect_prices(company_tickers(args.limit))} price rows.")
    elif args.command == "collect-history":
        print(f"Stored {collect_history(company_tickers(args.limit), args.period)} historical price rows.")
    elif args.command == "collect-news":
        print(f"Stored {collect_news(company_tickers(args.limit))} news rows.")
    elif args.command == "collect-research":
        providers = _providers_arg(args.providers)
        if args.event_id:
            event = _event_by_id(args.event_id)
            print(
                as_json(
                    {
                        "sources": collect_event_research(
                            event, force=args.force, providers=providers
                        )
                    }
                )
            )
        else:
            print(
                as_json(
                    collect_research_for_events(
                        args.limit or 5, force=args.force, providers=providers
                    )
                )
            )
    elif args.command == "collect-filings":
        print(f"Stored {collect_filings(company_tickers(args.limit))} SEC filing rows.")
    elif args.command == "collect-earnings":
        print(f"Stored {collect_earnings(company_tickers(args.limit))} earnings rows.")
    elif args.command == "detect-events":
        print(f"Created {detect_events()} events.")
    elif args.command == "generate-content":
        print(as_json(generate_for_events(args.limit or 10, skip_video=args.skip_video)))
    elif args.command == "prepare-video-story":
        if args.event_id:
            print(
                as_json(
                    prepare_event_story(
                        args.event_id,
                        template=args.template,
                        max_duration=args.max_duration,
                    )
                )
            )
        else:
            print(
                as_json(
                    prepare_top_events(
                        args.limit or 5,
                        template=args.template,
                        max_duration=args.max_duration,
                    )
                )
            )
    elif args.command == "prepare-script-manifest":
        if args.event_id:
            print(as_json(prepare_script_manifest(args.event_id)))
        else:
            print(as_json(prepare_script_manifests(args.limit or 5)))
    elif args.command == "normalize-script-manifests":
        print(as_json(normalize_script_manifests(args.root)))
    elif args.command == "generate-script-from-manifest":
        print(as_json(_generate_script_from_manifest_command(args)))
    elif args.command == "build-research-digests":
        if args.bundle_path:
            print(
                as_json(
                    build_research_digest(
                        args.bundle_path,
                        use_gemini=args.gemini,
                        force=args.force,
                    )
                )
            )
        else:
            print(
                as_json(
                    build_research_digests(
                        limit=args.limit or 10,
                        use_gemini=args.gemini,
                        batch_size=args.batch_size,
                        force=args.force,
                    )
                )
            )
    elif args.command == "render-videos":
        print(
            as_json(
                render_existing_videos(
                    args.limit,
                    approved_only=args.approved_only,
                    template=args.template,
                    max_duration=args.max_duration,
                    captions=args.captions,
                    renderer=args.renderer,
                )
            )
        )
    elif args.command == "run-pipeline":
        print(as_json(run_pipeline(args.limit, skip_video=args.skip_video)))
    elif args.command == "daily-market-refresh":
        print(
            as_json(
                daily_market_refresh(
                    extended_size=args.extended_size,
                    top_movers=args.top_movers,
                    research_limit=args.research_limit,
                    video_limit=args.video_limit,
                    min_event_score=args.min_event_score,
                    min_video_score=args.min_video_score,
                    force_research=args.force_research,
                    refresh_stale_research_after_hours=args.refresh_stale_research_after_hours,
                    force_if_no_tier1_or_tier2_sources=args.force_if_no_tier1_or_tier2_sources,
                    create_local_digests=not args.skip_local_digests,
                    gemini_digest_video_ready=args.gemini_digest_video_ready,
                    gemini_digest_research_ready_batch=args.gemini_digest_research_ready_batch,
                    digest_batch_size=args.digest_batch_size,
                )
            )
        )
    elif args.command == "weekday-autopilot":
        print(
            as_json(
                run_weekday_autopilot(
                    video_limit=args.video_limit,
                    renderer=args.renderer,
                    today=args.today,
                    extended_size=args.extended_size,
                    top_movers=args.top_movers,
                    research_limit=args.research_limit,
                    min_event_score=args.min_event_score,
                    min_video_score=args.min_video_score,
                    force_research=args.force_research,
                    refresh_stale_research_after_hours=args.refresh_stale_research_after_hours,
                    force_if_no_tier1_or_tier2_sources=args.force_if_no_tier1_or_tier2_sources,
                    force_script=args.force_script,
                    force_render=args.force_render,
                    skip_tts=args.skip_tts,
                    skip_render=args.skip_render,
                    gemini_digests=args.gemini_digests,
                    digest_batch_size=args.digest_batch_size,
                )
            )
        )
    elif args.command == "movers-report":
        print(as_json(movers_report(date_value=args.date, limit=args.limit)))
    elif args.command == "run-agent-pipeline":
        interrupts = []
        if args.interrupt_before_script:
            interrupts.append("generate_scripts")
        if args.interrupt_before_render:
            interrupts.append("render_or_skip_videos")
        print(
            as_json(
                run_agent_pipeline(
                    demo=args.demo,
                    thread_id=args.thread_id,
                    skip_render=args.skip_render,
                    force_script=args.force_script,
                    event_ids=args.event_id,
                    interrupt_before=interrupts,
                    resume=args.resume,
                    checkpoint_path=args.checkpoint_path,
                )
            )
        )
    elif args.command == "inspect-agent-run":
        print(as_json(inspect_agent_run(args.thread_id)))
    elif args.command == "list-agent-runs":
        print(as_json({"runs": list_agent_runs(args.limit)}))


def _limit_parser(subparsers, name: str):
    parser = subparsers.add_parser(name)
    parser.add_argument("--limit", type=int, default=None)
    return parser


def _event_by_id(event_id: int) -> dict:
    from models.database import query

    rows = query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not rows:
        raise SystemExit(f"No event found for id {event_id}")
    return rows[0]


def _generate_script_from_manifest_command(args) -> dict:
    if args.manifest_path:
        result = generate_script_from_manifest_path(args.manifest_path)
        script_id = insert_script(result["event_id"], result["db_fields"])
        return {**result, "script_id": script_id}
    if args.event_id:
        manifest = prepare_script_manifest(args.event_id)
        result = generate_script_from_manifest_path(manifest["manifest_path"])
        script_id = insert_script(result["event_id"], result["db_fields"])
        return {**result, "script_id": script_id}

    from models.database import query

    rows = query(
        """
        SELECT e.*
        FROM events e
        LEFT JOIN scripts s ON s.event_id = e.id
        WHERE s.id IS NULL
        ORDER BY e.score DESC, e.created_at DESC
        LIMIT ?
        """,
        (args.limit or 5,),
    )
    counts = {"eligible": len(rows), "scripts": 0, "not_ready": 0, "errors": 0}
    for event in rows:
        try:
            manifest = prepare_script_manifest(int(event["id"]))
            if not manifest["manifest"]["ready_for_gemini_script"]:
                counts["not_ready"] += 1
                continue
            result = generate_script_from_manifest_path(manifest["manifest_path"])
            insert_script(result["event_id"], result["db_fields"])
            counts["scripts"] += 1
        except Exception:
            counts["errors"] += 1
    return counts


def _providers_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
