# Review integration baseline

This directory contains the deterministic cross-layer Review regression baseline. It creates a
temporary Git repository at runtime with a `main` branch and a `fixture-change` branch. The target
branch contains one added file, one deleted file, and one modified file. No submodule, local-only
branch, API key, or network LLM is required.

The fake model runtime sends fixed review intent through the production `comment` collector. This
keeps the test stable despite normal LLM output variation while still exercising line resolution,
Finding validation, persistence, HTTP responses, process-report aggregation, and the React UI.

Run both integration layers from the repository root:

```bash
uv sync --project backend
pnpm install
uv run --project backend pytest integration-tests/test_review_pipeline.py -v
pnpm --dir integration-tests test
```

The Playwright suite starts its own loopback-only backend and frontend services, runs desktop and
mobile projects, and fails on browser page errors. Ports and data locations can be overridden with
`CODELENS_INTEGRATION_BACKEND_PORT`, `CODELENS_INTEGRATION_FRONTEND_PORT`, and
`CODELENS_INTEGRATION_DATA_DIR`.
