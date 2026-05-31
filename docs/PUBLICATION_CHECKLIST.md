# Publication Checklist

Use this checklist before pushing the public repo.

## Must Pass

- `git status --short` shows only intentional public changes.
- `uv run ruff check .`
- `uv run pytest`
- `npm run typecheck --prefix remotion`
- `npm run build --prefix frontend`
- `uv run python scripts/assert_public_clean.py`

## Must Not Be Tracked

- `.env`
- provider API keys
- local SQLite databases
- generated videos, audio, charts, review bundles, logs, and render scratch files
- real provider response payloads
- private brand binaries

## Public Demo Policy

- Ship synthetic fixtures only.
- Let users generate local artifacts, but keep all outputs ignored.
- Default agent demo command should use `--skip-render`.
- Document that real market/API usage requires user-provided credentials.

## Release Steps

1. Run the validation commands above.
2. Export the sanitized branch into the public repo directory.
3. Initialize a fresh Git history in the public repo.
4. Add remote `git@github.com:zthsk/market-brief-agents.git`.
5. Push the initial public commit.
