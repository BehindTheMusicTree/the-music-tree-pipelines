# Contributing

This project is in early active development by a solo developer. Contributions, suggestions, and feedback are welcome.

## Table of Contents

- [Development Workflow](#development-workflow)
  - [Setup](#setup)
  - [Branching](#branching)
  - [Committing](#committing)
  - [Pull Requests](#pull-requests)
- [Testing](#testing)
- [Changelog](#changelog)
- [Code Style](#code-style)
- [License](#license)

## Development Workflow

### Setup

**Prerequisites:** Python 3.12+, Git. SSH access to the BTMT `infrastructure` VPS is needed for real pipeline dev/runs (see [README.md#data-source](README.md#data-source) — no local MusicBrainz dump for now, disk space workaround), but not for running the test suite — see [TESTING.md](TESTING.md).

```bash
git clone https://github.com/BehindTheMusicTree/root-the-music-tree.git
cd root-the-music-tree
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

Then bring up the SSH tunnel to MusicBrainz staging before running any bronze ingestion — see [README.md#data-source](README.md#data-source).

### Branching

We follow Git Flow:

| Branch | Purpose |
|---|---|
| `main` | Production-ready, stable |
| `develop` | Integration branch — all features merge here |
| `feature/<name>` | New features, branch from `develop` |
| `fix/<name>` | Bug fixes, branch from `develop` |
| `chore/<name>` | Maintenance, CI, dependencies |

No direct commits to `main` or `develop`.

```bash
git checkout develop
git pull origin develop
git checkout -b feature/my-feature
```

### Committing

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>
```

| Type | Use for |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `chore` | Maintenance / dependencies |
| `refactor` | Code restructuring |
| `style` | Formatting only |
| `perf` | Performance |
| `ci` | CI/CD changes |

**Examples:**
- `feat(bronze): ingest MusicBrainz recording_tag and genre tables`
- `feat(silver): build genre hierarchy from Wikidata subclass links`
- `fix(hierarchy): dedupe fuzzy-matched genre aliases`
- `docs(readme): document local MusicBrainz dump setup`

Rules: imperative mood, under 70 characters, lowercase type and scope.

### Pull Requests

1. Ensure your branch is up to date with `develop`
2. Run `ruff check . && ruff format --check .` and `pytest -m "not integration"` — both must pass (same checks CI runs; see [Testing](#testing))
3. Update `CHANGELOG.md` under `[Unreleased]`
4. Open a PR targeting `develop`
5. Use the same `type(scope): summary` format for the PR title

**Pre-PR checklist:**
- [ ] No `print()` or debug code
- [ ] No accidental commits (`.env`, MusicBrainz dumps, large binaries)
- [ ] `CHANGELOG.md` updated
- [ ] Branch targets `develop`

## Testing

See [TESTING.md](TESTING.md) for test tiers (unit, e2e/pipeline, integration) and fixture conventions.

## Changelog

Update `CHANGELOG.md` with every PR. Add entries to the `[Unreleased]` section under the appropriate category. Be descriptive and user-focused — avoid dumping raw git logs.

See [CHANGELOG.md](CHANGELOG.md) for format examples.

## Code Style

- **Python** — Ruff (lint + format, line-length 120), `strict` typing where practical
- **Polars over pandas** — no `pandas` dependency
- **Fail fast** — raise immediately on missing config or invalid state, no silent fallbacks
- **No comments** unless the *why* is non-obvious
- **No dead code** — remove unused variables, imports, and functions
- **Dependency pinning** — exact-pin (`==`) runtime and dev dependencies for reproducibility; use a minimum constraint (`>=`) for the `[build-system]` backend, since it's only invoked transiently during the build and exact-pinning it risks breakage if that version is yanked

## License

All contributions are made under the project's [Apache 2.0 license](LICENSE). You retain authorship of your code.
