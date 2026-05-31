# AI Engineering Roadmap

This project is already more than a prompt wrapper: it has source review, manifests, schemas,
fallbacks, and media quality gates. The next work should make the agentic and retrieval pieces more
visible and measurable.

## Milestone 1: Agent Orchestration

Status: complete.

- The graph compiles through LangGraph when `uv sync --extra agent` is used.
- SQLite checkpoints are written to `data/langgraph_checkpoints.sqlite`.
- Runs can pause and resume by `thread_id`.
- Per-node timing, compact input/output summaries, and error counts are written to run artifacts.
- The Streamlit dashboard has an Agent Runs page for traces, status, errors, and resume controls.
- Human-in-the-loop pause gates are available before script generation and video rendering.

## Milestone 2: RAG Pipeline

- Chunk approved filings, research highlights, digests, and script manifests into retrievable documents.
- Add a persistent local vector store for public demos.
- Track retrieved source ids used in each generated scene.
- Evaluate retrieval quality with synthetic questions and expected source ids.

## Milestone 3: Specialist Agents

- Research agent: proposes missing source types and rejects weak evidence.
- Script agent: drafts scene plans from approved manifests and retrieved context.
- Compliance agent: checks unsupported claims, source-tier misuse, and advice language.
- Producer agent: checks captions, timing, visual requirements, and output readiness.

## Milestone 4: Evals And Observability

- Add claim-support tests for generated scripts.
- Add no-advice regression tests.
- Add latency/error metrics by graph node.
- Add optional LangSmith or OpenTelemetry tracing for local runs.
