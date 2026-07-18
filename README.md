# CodeLens

## Development

```bash
# Backend API
uv run --project backend codelens-review start .

# Frontend development server
pnpm --dir frontend dev
```

## Verification

```bash
# Backend verification
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src

# Frontend verification
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```
