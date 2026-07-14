#!/usr/bin/env bash
# (Re)creates one DuckDB view per Parquet file in bronze/, in a persistent bronze.duckdb.
# Safe to re-run: auto-discovers files, uses CREATE OR REPLACE VIEW.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRONZE_DIR="$REPO_ROOT/bronze"
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
