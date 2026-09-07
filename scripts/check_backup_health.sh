#!/usr/bin/env bash
# check_backup_health.sh — the backup tier's health check (slice 920, D5/D6).
#
# Wraps check_archive_health.sh (slice 915, unchanged) for the four database
# checks and adds six of its own. Every failure is named on its own FAIL line;
# the last line is always `FLAGS archive=<n> stale=<m>`, the per-class counts
# the cron glue turns into the two flag files. Read-only apart from a canary
# file it creates and deletes in --wal-dir.
#
# The ten checks, their class, and the flag the class drives:
#   archive_mode_off      archive  ARCHIVE-BROKEN  archiving not enabled (inner)
#   archiver_failing      archive  ARCHIVE-BROKEN  last archive attempt failed (inner)
#   unarchived_backlog    archive  ARCHIVE-BROKEN  unarchived WAL over threshold (inner)
#   wal_disk_low          archive  ARCHIVE-BROKEN  pg_wal filesystem below floor (inner)
#   prune_permission      archive  ARCHIVE-BROKEN  cannot create+delete a file in --wal-dir
#   archive_wedged        archive  ARCHIVE-BROKEN  next-to-archive segment present, short, no .zst
#   archive_tmp_leftover  archive  ARCHIVE-BROKEN  a *.tmp in --wal-dir older than the limit
#   offsite_wal_stale     stale    BACKUP-STALE    --stamp missing or older than --stale-after
#   system_backup_stale   stale    BACKUP-STALE    --system-stamp missing or older than the limit
#   weekly_base_stale     stale    BACKUP-STALE    newest base/<YYYYMMDD> older than the limit
#
# When anything failed, the line before FLAGS is `FAILED archive=<names,csv>
# stale=<names,csv>` so the glue's journal lines name each flag's own failures.
#
# Usage:
#   check_backup_health.sh --db-url <url> --pgdata <dir> --wal-dir <dir> \
#       --stamp <file> --stale-after <minutes> --system-stamp <file> --base-dir <dir>
#
# All arguments are required and explicit (915 D5): a check aimed by ambient
# configuration can silently watch the wrong cluster or directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Sibling tools resolve by name, with this script's own directory as the last
# place searched, so a stub earlier on PATH stands in for them under test.
PATH="$PATH:$SCRIPT_DIR"

WAL_SEGMENT_BYTES=16777216
TMP_LEFTOVER_MAX_AGE_MIN=10
SYSTEM_BACKUP_STALE_DAYS=2
WEEKLY_BASE_STALE_DAYS=9
PRUNE_CANARY=.prune-canary.tmp

# The one place a check's class is defined. Counted into the FLAGS line by
# name; a FAIL whose name is not listed here is counted as UNLISTED_CLASS
# (an unknown failure is a suspect chain until proven otherwise).
declare -A CHECK_CLASS=(
  [archive_mode_off]=archive
  [archiver_failing]=archive
  [unarchived_backlog]=archive
  [wal_disk_low]=archive
  [prune_permission]=archive
  [archive_wedged]=archive
  [archive_tmp_leftover]=archive
  [offsite_wal_stale]=stale
  [system_backup_stale]=stale
  [weekly_base_stale]=stale
)
UNLISTED_CLASS=archive

usage() {
  echo "usage: $0 --db-url <url> --pgdata <dir> --wal-dir <dir> --stamp <file> --stale-after <minutes> --system-stamp <file> --base-dir <dir>" >&2
}

DB_URL=""; PGDATA_DIR=""; WAL_DIR=""; STAMP=""; STALE_AFTER_MIN=""; SYSTEM_STAMP=""; BASE_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db-url)       DB_URL="${2:-}"; shift 2 ;;
    --pgdata)       PGDATA_DIR="${2:-}"; shift 2 ;;
    --wal-dir)      WAL_DIR="${2:-}"; shift 2 ;;
    --stamp)        STAMP="${2:-}"; shift 2 ;;
    --stale-after)  STALE_AFTER_MIN="${2:-}"; shift 2 ;;
    --system-stamp) SYSTEM_STAMP="${2:-}"; shift 2 ;;
    --base-dir)     BASE_DIR="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$DB_URL" ] || { echo "error: --db-url is required" >&2; usage; exit 2; }
[ -n "$PGDATA_DIR" ] || { echo "error: --pgdata is required" >&2; usage; exit 2; }
[ -n "$WAL_DIR" ] || { echo "error: --wal-dir is required" >&2; usage; exit 2; }
[ -n "$STAMP" ] || { echo "error: --stamp is required" >&2; usage; exit 2; }
[ -n "$STALE_AFTER_MIN" ] || { echo "error: --stale-after is required" >&2; usage; exit 2; }
[ -n "$SYSTEM_STAMP" ] || { echo "error: --system-stamp is required" >&2; usage; exit 2; }
[ -n "$BASE_DIR" ] || { echo "error: --base-dir is required" >&2; usage; exit 2; }
case "$STALE_AFTER_MIN" in
  ''|*[!0-9]*) echo "error: --stale-after must be a whole number of minutes: $STALE_AFTER_MIN" >&2; exit 2 ;;
esac

CANARY_PATH="$WAL_DIR/$PRUNE_CANARY"
# Whatever path this script exits by, the canary must not outlive it (an
# orphaned one would trip archive_tmp_leftover on a later run).
trap 'rm -f "$CANARY_PATH" 2>/dev/null || true' EXIT

FAILURES=()

# --- The four database checks, delegated. The inner exit code is captured
# rather than allowed to abort us: an unhealthy archive is exactly when the
# remaining checks must still run.
INNER_RC=0
INNER_OUTPUT=$(check_archive_health.sh --db-url "$DB_URL" --pgdata "$PGDATA_DIR" 2>&1) || INNER_RC=$?
[ -z "$INNER_OUTPUT" ] || echo "$INNER_OUTPUT"
while IFS= read -r line; do
  case "$line" in
    "FAIL "*) FAILURES+=("${line#FAIL }") ;;
  esac
done <<< "$INNER_OUTPUT"
if [ "$INNER_RC" -ne 0 ] && ! grep -q '^FAIL ' <<< "$INNER_OUTPUT"; then
  FAILURES+=("archive_uncheckable: check_archive_health.sh exited $INNER_RC without naming a failure")
fi

# --- prune_permission: the identity this runs as (the cron user) must be able
# to create and delete files in the archive, or the prune silently cannot.
if ! (: > "$CANARY_PATH") 2>/dev/null; then
  FAILURES+=("prune_permission: $(id -un) cannot create $CANARY_PATH — the prune cannot delete from $WAL_DIR")
elif ! rm -f "$CANARY_PATH" 2>/dev/null; then
  FAILURES+=("prune_permission: $(id -un) cannot delete $CANARY_PATH — the prune cannot delete from $WAL_DIR")
fi

# --- archive_wedged: the segment the archiver will write next (or is failing
# on) sits in the archive short and uncompressed. Name arithmetic is delegated
# to wal_segment_name.py; none happens here or in SQL.
ARCHIVER_ROW=$(psql "$DB_URL" -X -At -F'|' -v ON_ERROR_STOP=1 -c "
SELECT COALESCE(last_failed_time > COALESCE(last_archived_time, '-infinity'), false),
       COALESCE(last_archived_wal, ''), COALESCE(last_failed_wal, '')
  FROM pg_stat_archiver;" 2>/dev/null) || ARCHIVER_ROW=""
IFS='|' read -r ARCHIVER_FAILING LAST_ARCHIVED LAST_FAILED <<< "$ARCHIVER_ROW"
NEXT_SEGMENT=""
if [ "${ARCHIVER_FAILING:-}" = "t" ] && [ -n "${LAST_FAILED:-}" ]; then
  NEXT_SEGMENT="$LAST_FAILED"
elif [ -n "${LAST_ARCHIVED:-}" ]; then
  NEXT_SEGMENT=$("$SCRIPT_DIR/wal_segment_name.py" next "$LAST_ARCHIVED")
fi
if [ -n "$NEXT_SEGMENT" ] && [ -f "$WAL_DIR/$NEXT_SEGMENT" ] && [ ! -e "$WAL_DIR/$NEXT_SEGMENT.zst" ]; then
  NEXT_SIZE=$(stat -c %s "$WAL_DIR/$NEXT_SEGMENT")
  if [ "$NEXT_SIZE" -ne "$WAL_SEGMENT_BYTES" ]; then
    FAILURES+=("archive_wedged: $WAL_DIR/$NEXT_SEGMENT is $NEXT_SIZE bytes (a whole segment is $WAL_SEGMENT_BYTES) and has no .zst sibling — the archiver cannot overwrite it (see runbook)")
  fi
fi

# --- archive_tmp_leftover: an atomic write that never finished.
LEFTOVER=$(find "$WAL_DIR" -maxdepth 1 -name '*.tmp' -mmin "+$TMP_LEFTOVER_MAX_AGE_MIN" -printf '%f ' 2>/dev/null || true)
if [ -n "$LEFTOVER" ]; then
  FAILURES+=("archive_tmp_leftover: older than ${TMP_LEFTOVER_MAX_AGE_MIN} min in $WAL_DIR: ${LEFTOVER% }")
fi

# --- Stale checks: a stamp is written only on success, so its age is the time
# since the tier last completed.
NOW=$(date +%s)
stamp_age_min() {
  echo $(( (NOW - $(stat -c %Y "$1")) / 60 ))
}

if [ ! -f "$STAMP" ]; then
  FAILURES+=("offsite_wal_stale: push stamp $STAMP does not exist — the hourly WAL push has never succeeded here")
elif [ "$(stamp_age_min "$STAMP")" -gt "$STALE_AFTER_MIN" ]; then
  FAILURES+=("offsite_wal_stale: push stamp $STAMP is $(stamp_age_min "$STAMP") min old (limit ${STALE_AFTER_MIN} min)")
fi

SYSTEM_STALE_MIN=$((SYSTEM_BACKUP_STALE_DAYS * 1440))
if [ ! -f "$SYSTEM_STAMP" ]; then
  FAILURES+=("system_backup_stale: restic stamp $SYSTEM_STAMP does not exist — the system backup has never succeeded here")
elif [ "$(stamp_age_min "$SYSTEM_STAMP")" -gt "$SYSTEM_STALE_MIN" ]; then
  FAILURES+=("system_backup_stale: restic stamp $SYSTEM_STAMP is $(stamp_age_min "$SYSTEM_STAMP") min old (limit ${SYSTEM_BACKUP_STALE_DAYS} days)")
fi

# The directory name is the date; mtime is not (a prune touches it).
NEWEST_BASE=$(find "$BASE_DIR" -maxdepth 1 -mindepth 1 -type d -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' -printf '%f\n' 2>/dev/null | sort | tail -1 || true)
if [ -z "$NEWEST_BASE" ]; then
  FAILURES+=("weekly_base_stale: no dated base backup directory under $BASE_DIR")
else
  BASE_AGE_DAYS=$(( (NOW - $(date -d "$NEWEST_BASE" +%s)) / 86400 ))
  if [ "$BASE_AGE_DAYS" -gt "$WEEKLY_BASE_STALE_DAYS" ]; then
    FAILURES+=("weekly_base_stale: newest base backup $BASE_DIR/$NEWEST_BASE is ${BASE_AGE_DAYS} days old (limit ${WEEKLY_BASE_STALE_DAYS} days)")
  fi
fi

# --- Report: our own FAIL lines (the inner's were already printed), then the
# per-class counts over every failure by name.
ARCHIVE_COUNT=0; STALE_COUNT=0; ARCHIVE_NAMES=""; STALE_NAMES=""
for f in "${FAILURES[@]}"; do
  name="${f%%:*}"
  class="${CHECK_CLASS[$name]:-$UNLISTED_CLASS}"
  case "$class" in
    archive) ARCHIVE_COUNT=$((ARCHIVE_COUNT + 1)); ARCHIVE_NAMES+="${ARCHIVE_NAMES:+,}$name" ;;
    stale)   STALE_COUNT=$((STALE_COUNT + 1)); STALE_NAMES+="${STALE_NAMES:+,}$name" ;;
  esac
done
INNER_FAIL_COUNT=$(grep -c '^FAIL ' <<< "$INNER_OUTPUT" || true)
for f in "${FAILURES[@]:$INNER_FAIL_COUNT}"; do
  echo "FAIL $f"
done
# Two machine-readable lines for the cron glue: names per class (only when
# something failed), then the counts line, always last.
[ "$((ARCHIVE_COUNT + STALE_COUNT))" -eq 0 ] || echo "FAILED archive=$ARCHIVE_NAMES stale=$STALE_NAMES"
echo "FLAGS archive=$ARCHIVE_COUNT stale=$STALE_COUNT"
[ "$((ARCHIVE_COUNT + STALE_COUNT))" -eq 0 ]
