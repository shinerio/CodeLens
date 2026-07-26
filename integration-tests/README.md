# Review integration baseline

This directory contains the deterministic cross-layer Review regression baseline. It creates a
temporary Git repository at runtime with a `main` branch and a `fixture-change` branch. The target
branch contains one added file, one deleted file, and one modified file. No submodule, local-only
branch, API key, or network LLM is required.

The fake model runtime sends fixed review intent through the production `comment` collector and
intentionally repeats one comment. The validator must deduplicate the four submitted comments into
three Findings without failing the Review. This keeps the test stable while exercising line
resolution, Finding validation, persistence, HTTP responses, process-report aggregation, and the
React UI.

Run both integration layers from the repository root:

```bash
uv sync --project backend
pnpm install
uv run --project backend pytest integration-tests/test_review_pipeline.py -v
pnpm --dir integration-tests test
```

The Playwright suite creates one Review, checks its three Findings at desktop and mobile viewport
sizes, and fails if any additional Review is created. Its launcher starts loopback-only backend and
frontend services in a fresh temporary data directory and removes that directory after success or
failure. Ports and the temporary-directory parent can be overridden with
`CODELENS_INTEGRATION_BACKEND_PORT`, `CODELENS_INTEGRATION_FRONTEND_PORT`, and
`CODELENS_INTEGRATION_DATA_DIR`.
