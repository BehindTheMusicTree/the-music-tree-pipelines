#!/usr/bin/env bash
# (Re)creates one DuckDB view per Parquet file in the bronze output dir, in a persistent
# bronze.duckdb alongside it. Safe to re-run: auto-discovers files, uses CREATE OR REPLACE VIEW.
set -euo pipefail

PIPELINE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Same .env as musicbrainz_to_the_music_tree_api.bronze_musicbrainz (common.env.load_pipeline_env) —
# BRONZE_OUTPUT_DIR is the pipeline's actual output dir, not necessarily "bronze/".
if [ -f "$PIPELINE_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PIPELINE_ROOT/.env"
  set +a
fi
: "${BRONZE_OUTPUT_DIR:?BRONZE_OUTPUT_DIR is required — copy .env.example to .env and set it}"

BRONZE_DIR="$BRONZE_OUTPUT_DIR"
DB_FILE="$BRONZE_DIR/bronze.duckdb"

cd "$BRONZE_DIR"
for f in *.parquet; do
  table="${f%.parquet}"
  # Absolute path: a relative one would only resolve when duckdb is later invoked from
  # inside bronze/, breaking "query bronze.duckdb from anywhere" for anyone else.
  duckdb "$DB_FILE" -c "CREATE OR REPLACE VIEW ${table} AS SELECT * FROM '${BRONZE_DIR}/${f}'"
done

echo "views ready in $DB_FILE:"
duckdb "$DB_FILE" -c "SHOW TABLES"
