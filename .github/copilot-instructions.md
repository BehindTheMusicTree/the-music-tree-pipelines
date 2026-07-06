# Copilot Instructions

Follow [CONTRIBUTING.md](../CONTRIBUTING.md) for this repository, in particular:

- **Commits**: every commit message must use a [Conventional Commits](https://www.conventionalcommits.org/) `type(scope): summary` prefix (`feat`, `fix`, `docs`, `chore`, `refactor`, `style`, `perf`, `ci`). Imperative mood, under 70 characters, lowercase type and scope. Never suggest or generate a commit message without this prefix.
- **Code style**: Ruff (lint + format, line-length 120), Polars over pandas, fail fast with no silent fallbacks, no comments unless the *why* is non-obvious, no dead code.
- **Dependencies**: exact-pin (`==`) runtime and dev dependencies; use `>=` only for the `[build-system]` backend.
- **Branching**: Git Flow — `feature/`, `fix/`, `chore/` branches off `develop`, never direct commits to `main`/`develop`.

See [TESTING.md](../TESTING.md) for test tiers and fixture conventions.
