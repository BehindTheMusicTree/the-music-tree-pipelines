# Security

## Reporting issues

If you believe you have found a security vulnerability, please **do not** open a public GitHub issue. Contact the repository maintainer privately (e.g. via GitHub Security Advisories for this repo, if enabled, or the maintainer's preferred channel).

Include enough detail to reproduce or understand impact (component, configuration, steps). Do not paste live credentials or connection strings.

## Scope

This repository defines a **data pipeline** (Python/Polars, SQL, Postgres) that reads a local MusicBrainz database dump and Wikidata data to build a genre hierarchy. It does not expose a network service.

## Operational hygiene

- Database connection strings and any API tokens are supplied via a local, gitignored `.env` — never commit credentials.
- The MusicBrainz dump and any downloaded Wikidata extracts are treated as local, disposable data — not committed to the repository.
