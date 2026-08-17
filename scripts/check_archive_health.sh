#!/usr/bin/env bash
# check_archive_health.sh — WAL-archiving health check (slice 915, D2).
#
# A silently failing archive_command is the one way the backup work can cause
# an outage: PostgreSQL retains every unarchived segment, pg_wal grows, the
# filesystem fills, and the server halts. This check turns that silent state
# into a loud one. It is read-only.
#
# Checks, each named on failure so the output needs no log-reading:
#   archive_mode_off    archiving is not enabled at all
#   archiver_failing    the most recent archive attempt failed
#   unarchived_backlog  bytes of WAL awaiting archive exceed the threshold
#   wal_disk_low        (only with --pgdata) free space on the pg_wal
#                       filesystem is below the floor
#
# Usage:
#   check_archive_health.sh --db-url <url> [--pgdata <dir>]
#
# The DB URL is a required explicit argument (D5) — monitoring aimed by an
# ambient variable can silently watch the wrong cluster.
set -euo pipefail

# Backlog threshold: max_wal_size on this cluster is 1 GB and steady-state
# pg_wal was measured at 560 MB (2026-08-16), so 4 GiB of unarchived WAL means
# the archiver has been failing or badly behind for a long time — alarm well
# before the disk (978 GB free) is in danger.
MAX_UNARCHIVED_BYTES=$((4 * 1024 * 1024 * 1024))

# Free-space floor for the pg_wal filesystem, in percent.
MIN_FREE_PCT=15

usage() {
  echo "usage: $0 --db-url <postgresql-url> [--pgdata <data-directory>]" >&2
}

DB_URL=""
PGDATA_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db-url) DB_URL="${2:-}"; shift 2 ;;
    --pgdata) PGDATA_DIR="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$DB_URL" ]; then
  echo "error: --db-url is required" >&2; usage; exit 2
fi

# One round trip: archive_mode, current-failure state, and the archiver
# backlog in bytes (current WAL position minus the end of the last archived
# segment, decoded from its filename).
read -r ARCHIVE_MODE FAILING BACKLOG <<EOF
$(psql "$DB_URL" -X -At -F' ' -v ON_ERROR_STOP=1 -c "
WITH seg AS (
  SELECT pg_size_bytes(current_setting('wal_segment_size')) AS seg_bytes
), arch AS (
  SELECT last_archived_wal, last_archived_time, last_failed_wal, last_failed_time
    FROM pg_stat_archiver
)
SELECT current_setting('archive_mode'),
       COALESCE((SELECT last_failed_time > COALESCE(last_archived_time, '-infinity')
                   FROM arch WHERE last_failed_wal IS NOT NULL), false),
       CASE
         WHEN (SELECT last_archived_wal FROM arch) IS NULL
           THEN pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')
         ELSE pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0')
              - (('x' || substr((SELECT last_archived_wal FROM arch), 9, 8))::bit(32)::bigint
                   * (4294967296 / (SELECT seg_bytes FROM seg))
                 + ('x' || substr((SELECT last_archived_wal FROM arch), 17, 8))::bit(32)::bigint
                 + 1) * (SELECT seg_bytes FROM seg)
       END::bigint;")
EOF

FAILURES=()

if [ "$ARCHIVE_MODE" != "on" ] && [ "$ARCHIVE_MODE" != "always" ]; then
  FAILURES+=("archive_mode_off: archive_mode is '$ARCHIVE_MODE' — WAL archiving is not running")
fi

if [ "$FAILING" = "t" ]; then
  FAILURES+=("archiver_failing: the most recent archive attempt failed (see pg_stat_archiver.last_failed_wal)")
fi

# Only meaningful when archiving is on: with it off, everything is "backlog".
if { [ "$ARCHIVE_MODE" = "on" ] || [ "$ARCHIVE_MODE" = "always" ]; } \
   && [ "$BACKLOG" -gt "$MAX_UNARCHIVED_BYTES" ]; then
  FAILURES+=("unarchived_backlog: ${BACKLOG} bytes of WAL awaiting archive (threshold ${MAX_UNARCHIVED_BYTES})")
fi

if [ -n "$PGDATA_DIR" ]; then
  USED_PCT=$(df --output=pcent "$PGDATA_DIR" | tail -1 | tr -d ' %')
  FREE_PCT=$((100 - USED_PCT))
  if [ "$FREE_PCT" -lt "$MIN_FREE_PCT" ]; then
    FAILURES+=("wal_disk_low: ${FREE_PCT}% free on the filesystem holding $PGDATA_DIR (floor ${MIN_FREE_PCT}%)")
  fi
fi

if [ "${#FAILURES[@]}" -gt 0 ]; then
  for f in "${FAILURES[@]}"; do
    echo "FAIL $f"
  done
  exit 1
fi

echo "PASS archive healthy (mode=$ARCHIVE_MODE, unarchived_bytes=$BACKLOG)"
