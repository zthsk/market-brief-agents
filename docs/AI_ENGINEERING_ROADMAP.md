# AI Engineering Roadmap

This project is already more than a prompt wrapper: it has source review, manifests, schemas,
fallbacks, and media quality gates. The next work should make the agentic and retrieval pieces more
visible and measurable.

## Milestone 1: Agent Orchestration

- Compile the existing graph through LangGraph when `uv sync --extra agent` is used.
- Add SQLite checkpointing for true resume/replay by thread id.
- Surface per-node timing, inputs, outputs, and error summaries in the dashboard.
- Add human-in-the-loop interrupts before script generation and before video rendering.

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
