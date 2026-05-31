# Market Brief Agents Project Structure

This repo is organized around the content pipeline stages. Keep source code, durable inputs, and generated artifacts separate so each stage can be reset without guessing what is safe to delete.

## Source Code

- `backend/`: CLI and Streamlit review dashboard.
- `jobs/`: Pipeline orchestration across collection, research, scripts, assets, audio, and video.
- `models/`: SQLite schema and persistence helpers.
- `services/`: External data/API integrations and stage-specific service logic.
- `media_engine/`: Story, scene, caption, manifest, quality, and render primitives.
- `frontend/`: Public Next.js site.
- `agentic/`: LangGraph-compatible orchestration and RAG retrieval.
- `tests/`: Unit tests for each pipeline layer.

## Durable Local Inputs

- `assets/brand/`: Optional local brand media. Public repo tracks only `.gitkeep`.
- `.env`: Local secrets and provider settings. This is ignored and should not be committed.
- `data/market_brief_agents.db`: Local SQLite database. This is ignored and can be regenerated.

## Generated Artifacts

- `outputs/research/`: Raw provider responses, reviewed source results, and research review manifests.
- `outputs/script_manifests/`: Reviewed, deterministic payloads for script generation.
- `outputs/scripts/`: Script-generation audit bundles with manifest snapshots, prompts, raw responses, and normalized scripts.
- `outputs/review/`: Prepared/rendered story bundles with scenes, captions, thumbnails, video, and quality reports.
- `storage/assets/`: Generated charts and visual cards.
- `storage/audio/`: Generated narration audio.
- `storage/render/`: Temporary ffmpeg render files.
- `videos/`: Final rendered videos.

Generated filenames use `TICKER_YYYY-MM-DD_EVENTID` where practical, for example:

- `storage/audio/ADBE_2026-05-29_1.wav`
- `storage/audio/ADBE_2026-05-29_1_scene.wav`
- `outputs/scripts/ADBE_2026-05-29_1/script.json`
- `videos/ADBE_2026-05-29_1.mp4`
- `outputs/review/ADBE_2026-05-29_1/`

## Reset Boundary

Use this checkpoint before refactoring script generation or TTS:

```bash
uv run marketbrief clean-generated-content
```

That command preserves companies, market data, events, research sources, `outputs/research/`, and `outputs/script_manifests/`. It clears generated scripts, script/video database rows, generated visual assets, narration audio, review bundles, render scratch files, and final videos.

To rebuild the research-manifest stage from an initialized database:

```bash
uv run marketbrief collect-research --limit 5 --providers all
uv run marketbrief prepare-script-manifest --limit 5
```
