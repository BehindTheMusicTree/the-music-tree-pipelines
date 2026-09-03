---
name: code-review
description: Review pull requests in this repo (BTMT bronze/silver data pipelines) against its Ruff/Polars/fail-fast conventions and layering rules. Use for any PR touching pipelines/*.
---

# Code review

Check changed code against these repo-specific rules before general feedback.

## Style (see CONTRIBUTING.md, .github/copilot-instructions.md)

- Polars only — flag any `import pandas` or pandas API usage.
- Fail fast: flag silent fallbacks/defaults that mask missing config (e.g. `os.getenv("X", "default")` instead of `require_env("X")`), and any `try/except` that swallows an error instead of raising.
- No comments unless the *why* is non-obvious — flag comments that restate what the code does.
- No dead code, no unused imports/vars.
- Exact-pin (`==`) runtime/dev dependencies in `pyproject.toml`; `>=` is only acceptable in `[build-system]`.
- Commit messages / PR title must be Conventional Commits (`type(scope): summary`, imperative, <70 chars, lowercase).

## Architecture

- Silver steps must never fetch new external data directly — new data needed by Silver belongs in Bronze first (see the repo's Bronze/Silver split in CLAUDE.md). Flag any HTTP/SPARQL/DB call added inside `pipelines/*/src/*/silver/`.
- `common.env.load_pipeline_env(__file__)` / `require_env(...)` is the only sanctioned way to read pipeline config — flag ad hoc `os.environ` access.
- Silver steps are sequential and numbered (`1_...` → `N_...`); each reads only the prior step's Parquet output. Flag a new step that skips this ordering or reaches back further than the immediate predecessor without justification.
- musicbrainz Silver stays tidy/long (one row per link / per recording-genre match) — collapsing to one row per recording with list-valued columns is explicitly Gold-layer, not Silver. Flag any Silver step doing that collapse.

## Testing

- New Bronze/Silver logic should have unit tests (`pytest -m "not integration"`); anything touching the live Postgres mirror or SPARQL endpoint needs an `integration`-marked test instead of being tested against a live network call.
- Coverage gate is 90% combined (unit + integration) — flag substantial new logic with no accompanying test.

## What to skip

Don't flag formatting Ruff itself would catch (line length, import order) — CI runs `ruff check`/`ruff format --check` separately.
