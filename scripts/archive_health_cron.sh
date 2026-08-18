#!/usr/bin/env bash
# archive_health_cron.sh — cron glue that surfaces the archive-health check
# where an operator actually looks (slice 915, 5.2).
#
# On FAIL (or on being unable to check at all — an unreachable cluster is an
# alarm, not a pass): writes the loud flag file the runbook names, and the
# weekly backup glue refuses to run while it exists. On PASS: removes the
# flag. Every run appends one line to the log.
#
# Usage:
#   archive_health_cron.sh --env-file <path> --pgdata <dir> --flag <path> --log <path>
#
# The env file path is explicit; the URL is grep'd from it, never sourced
# (the $-in-password trap).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  echo "usage: $0 --env-file <path> --pgdata <dir> --flag <path> --log <path>" >&2
}

ENV_FILE=""; PGDATA_DIR=""; FLAG=""; LOG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --pgdata)   PGDATA_DIR="${2:-}"; shift 2 ;;
    --flag)     FLAG="${2:-}"; shift 2 ;;
    --log)      LOG="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$ENV_FILE" ] || { echo "error: --env-file is required" >&2; usage; exit 2; }
[ -n "$PGDATA_DIR" ] || { echo "error: --pgdata is required" >&2; usage; exit 2; }
[ -n "$FLAG" ] || { echo "error: --flag is required" >&2; usage; exit 2; }
[ -n "$LOG" ] || { echo "error: --log is required" >&2; usage; exit 2; }

DB_URL=$(grep '^MT_TIMESCALE_MAINTENANCE_URL' "$ENV_FILE" | sed 's/^[^=]*=//' | tr -d '"')
if [ -z "$DB_URL" ]; then
  OUTPUT="FAIL cannot_check: MT_TIMESCALE_MAINTENANCE_URL not found in $ENV_FILE"
  STATUS=1
else
  OUTPUT=$("$SCRIPT_DIR/check_archive_health.sh" --db-url "$DB_URL" --pgdata "$PGDATA_DIR" 2>&1)
  STATUS=$?
fi

STAMP=$(date -Is)
echo "$STAMP $OUTPUT" >> "$LOG"

if [ "$STATUS" -ne 0 ]; then
  {
    echo "WAL ARCHIVING IS BROKEN OR UNCHECKABLE — see the backup-and-restore runbook"
    echo "detected: $STAMP"
    echo "$OUTPUT"
  } > "$FLAG"
  exit 1
fi

rm -f "$FLAG"
