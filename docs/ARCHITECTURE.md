# Architecture

Market Brief Agents is organized around a research-to-media pipeline with explicit review and
quality boundaries.

## Runtime Flow

1. **Inputs**: market prices, news, filings, earnings, or synthetic public fixtures.
2. **Event selection**: events are ranked by price movement, volume, freshness, and source quality.
3. **Research review**: sources are accepted or rejected, tiered, and written to auditable bundles.
4. **Script manifest**: approved evidence becomes a deterministic structured prompt payload.
5. **RAG retrieval**: approved source rows are exposed as retrievable documents with source metadata.
6. **Agent orchestration**: the agent graph coordinates state transitions and records resumable run state.
7. **Generation**: script packages are validated for source ids, scene structure, word count, and no-advice language.
8. **Media production**: audio, visual assets, captions, Remotion payloads, and videos are generated locally.
9. **Quality gate**: sync and quality reports decide whether an output is ready for review/posting.

## Agent Graph

The graph lives in `agentic/` and has these nodes:

- `initialize_run`
- `load_or_seed_demo_data`
- `collect_market_context`
- `detect_and_rank_events`
- `research_events`
- `prepare_script_manifests`
- `retrieve_evidence_context`
- `generate_scripts`
- `render_or_skip_videos`
- `quality_gate`
- `finalize_run`

If LangGraph is installed with the `agent` extra, the same node functions are compiled through
`StateGraph` with SQLite checkpointing. Without that extra, a local sequential runner executes the
same graph contract so the public demo works with no additional dependencies.

Checkpoint and pause controls:

- Checkpoints are stored in `data/langgraph_checkpoints.sqlite`.
- Run artifacts are stored in `outputs/agent_runs/{thread_id}.json`.
- `--interrupt-before-script` pauses before script generation.
- `--interrupt-before-render` pauses before video rendering.
- `--resume --thread-id ...` resumes from the saved checkpoint/run state.
- Each node records timing, compact input/output summaries, and error counts.

## Data Boundaries

Durable source code and public fixtures are tracked. Runtime artifacts are ignored:

- `data/*.db`
- `outputs/*`
- `storage/*`
- `videos/*`
- `logs/*`
- provider responses and generated review bundles

The public synthetic demo creates local artifacts under those ignored paths only.

## Safety Model

- Tier 1 and Tier 2 sources may support factual claims.
- Tier 3 sources are context only.
- Tier 4 or rejected sources are discovery only and cannot be cited by generated scripts.
- Generated scripts are validated against known source ids.
- Buy, sell, hold, guaranteed-return, and hype language is rejected or repaired.
- Final content includes an educational-only disclaimer.
