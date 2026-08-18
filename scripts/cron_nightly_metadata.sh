#!/usr/bin/env bash
# cron_nightly_metadata.sh — cron glue for the nightly metadata tier (9.1).
#
# Dump the metadata tables, then push the metadata directory offsite with
# checksum verification. This glue is the *caller* that supplies the explicit
# arguments the D5 tools demand; credentials are grep'd from the named env
# file, never sourced.
#
# Usage:
#   cron_nightly_metadata.sh --env-file <path> --dest <dir>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  echo "usage: $0 --env-file <path> --dest <dir>" >&2
}

ENV_FILE=""; DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --dest)     DEST="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$ENV_FILE" ] || { echo "error: --env-file is required" >&2; usage; exit 2; }
[ -n "$DEST" ] || { echo "error: --dest is required" >&2; usage; exit 2; }

DB_URL=$(grep '^MT_TIMESCALE_MAINTENANCE_URL' "$ENV_FILE" | sed 's/^[^=]*=//' | tr -d '"')
BUCKET=$(grep '^MT_BACKUP_S3_BUCKET' "$ENV_FILE" | sed 's/^[^=]*=//' | tr -d '"')
[ -n "$DB_URL" ] || { echo "error: MT_TIMESCALE_MAINTENANCE_URL not in $ENV_FILE" >&2; exit 1; }
[ -n "$BUCKET" ] || { echo "error: MT_BACKUP_S3_BUCKET not in $ENV_FILE" >&2; exit 1; }

echo "=== nightly metadata run: $(date -Is) ==="
"$SCRIPT_DIR/backup_metadata.sh" --db-url "$DB_URL" --dest "$DEST"
"$SCRIPT_DIR/offsite_sync.sh" --source "$DEST" --remote "b2:$BUCKET/metadata"
echo "=== nightly metadata done: $(date -Is) ==="
