#!/usr/bin/env bash
# (Re)creates one DuckDB view per Parquet file in the bronze output dir, in a persistent
# bronze.duckdb alongside it. Safe to re-run: auto-discovers files, uses CREATE OR REPLACE VIEW.
set -euo pipefail

PIPELINE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Same .env as musicbrainz.ingest —
# BRONZE_OUTPUT_DIR is the pipeline's actual output dir, not necessarily "bronze/".
if [ -f "$PIPELINE_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PIPELINE_ROOT/.env"
  set +a
fi
: "${BRONZE_OUTPUT_DIR:?BRONZE_OUTPUT_DIR is required — copy .env.example to .env and set it}"

if [ ! -d "$BRONZE_OUTPUT_DIR" ]; then
  echo "BRONZE_OUTPUT_DIR directory does not exist: $BRONZE_OUTPUT_DIR (run bronze ingestion first)" >&2
  exit 1
fi

BRONZE_DIR="$(cd "$BRONZE_OUTPUT_DIR" && pwd)"
DB_FILE="$BRONZE_DIR/bronze.duckdb"

shopt -s nullglob
cd "$BRONZE_DIR"
for f in *.parquet; do
  table="${f%.parquet}"
  parquet_path="${BRONZE_DIR}/${f}"
  # Escape single quotes for safe embedding in a single-quoted SQL string.
  parquet_path_sql="${parquet_path//$'\047'/$'\047\047'}"
  # Absolute path: a relative one would only resolve when duckdb is later invoked from
  # inside bronze/, breaking "query bronze.duckdb from anywhere" for anyone else.
  duckdb "$DB_FILE" -c "CREATE OR REPLACE VIEW \"${table}\" AS SELECT * FROM '${parquet_path_sql}'"
done

echo "views ready in $DB_FILE:"
duckdb "$DB_FILE" -c "SHOW TABLES"
