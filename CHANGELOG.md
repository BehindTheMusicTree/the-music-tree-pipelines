# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Table of Contents

- [Changelog Best Practices](#changelog-best-practices)
- [Unreleased](#unreleased)

## Changelog Best Practices

- Changelogs are for humans, not machines.
- Group changes under: Added, Changed, Improved, Deprecated, Removed, Fixed, Documentation, Performance.
- **"Test" is not a standalone category** — mention tests within the related feature or fix entry.
- Use an `[Unreleased]` section for upcoming changes.
- Use ISO 8601 date format: YYYY-MM-DD.

## [Unreleased]

### Added

- Initial project scaffolding: package structure, `pyproject.toml`, CI workflow (lint + test), and testing documentation.
- Automated MusicBrainz sample dataset setup for dev and CI: `musicbrainz-docker` vendored as a pinned git submodule, loaded via `scripts/setup-sample-db.sh`, wired into a new `integration` CI job.

### Changed

- Reorganized the test suite into execution-tier directories (`tests/unit/`, `tests/integration/`) and restructured `TESTING.md` to distinguish execution tiers from cross-cutting risk categories.
