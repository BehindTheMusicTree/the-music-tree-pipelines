#!/usr/bin/env bash
# Loads the official MusicBrainz sample dataset into a disposable local
# Postgres via the vendored musicbrainz-docker submodule. Safe to re-run:
# skips the (slow) reload if the sample data is already present.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MB_DOCKER_DIR="$REPO_ROOT/vendor/musicbrainz-docker"

if [ ! -e "$MB_DOCKER_DIR/.git" ]; then
  echo "error: vendor/musicbrainz-docker submodule is not initialized." >&2
  echo "run: git submodule update --init" >&2
  exit 1
fi

cd "$MB_DOCKER_DIR"

COMPOSE=(docker compose -f docker-compose.yml -f compose/musicbrainz-standalone.yml -f compose/publishing-db-port.yml)
IMAGES=(musicbrainz-docker_db:18 musicbrainz-docker-musicbrainz:latest)

if ! docker image inspect "${IMAGES[@]}" >/dev/null 2>&1; then
  "${COMPOSE[@]}" build db musicbrainz
fi
"${COMPOSE[@]}" up -d db

echo "waiting for postgres to be ready..."
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T db pg_isready -U musicbrainz -d musicbrainz_db >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! "${COMPOSE[@]}" exec -T db pg_isready -U musicbrainz -d musicbrainz_db >/dev/null 2>&1; then
  echo "error: postgres did not become ready within 60s" >&2
  exit 1
fi

if "${COMPOSE[@]}" exec -T db psql -U musicbrainz -d musicbrainz_db -c 'select 1 from artist limit 1' >/dev/null 2>&1; then
  echo "sample dataset already loaded, skipping import"
else
  "${COMPOSE[@]}" run --rm --no-deps musicbrainz createdb.sh -sample -fetch
fi

echo
echo "connection string: postgresql://musicbrainz@127.0.0.1:5432/musicbrainz_db"
echo "(export PGPASSWORD=musicbrainz before connecting with psql)"
