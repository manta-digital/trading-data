#!/usr/bin/env bash
# backup_prod.sh — verified base backup of a live PostgreSQL cluster (slice 915).
#
# pg_basebackup -Ft -z -Xs (D1): the WAL stream taken during the copy is what
# makes a backup of a *running* server consistent. A file copy of PGDATA has
# no such guarantee — the 2026-08-10 `cp` this replaces is torn by
# construction.
#
# The result is verified with pg_verifybackup before being presented as a
# backup (D6): an unverified backup is a hypothesis. Verification is not
# skippable.
#
# Usage:
#   backup_prod.sh --db-url <url> --dest <dir> [--remote <rclone-remote-path>]
#
# --db-url and --dest are required (D5); the target is never read from the
# environment. On prod, replication connections are admitted from localhost
# only — the URL must point at 127.0.0.1, and the role needs the REPLICATION
# attribute (provision_roles.sql).
#
# With --remote, the verified backup is pushed offsite and re-verified by
# checksum (offsite_sync.sh); any failing stage fails the whole run.
set -euo pipefail

OFFSITE_SYNC="$(dirname "$0")/offsite_sync.sh"

# No /usr/bin wrapper exists for pg_verifybackup on this host (measured
# 2026-08-16) — the versioned path is the only invocation that works. One
# constant so a major-version bump is a one-line edit.
PG_VERIFYBACKUP="/usr/lib/postgresql/17/bin/pg_verifybackup"

# Explicit rather than default (task 3.1). `spread` paces the initial
# checkpoint instead of spiking I/O on the live server; the backup takes
# correspondingly longer to start.
CHECKPOINT_MODE="spread"

usage() {
  echo "usage: $0 --db-url <postgresql-url> --dest <directory> [--remote <rclone-path>]" >&2
  echo "  --db-url   cluster to back up (required; never read from environment)" >&2
  echo "  --dest     directory to create for this backup (required; must not exist)" >&2
  echo "  --remote   rclone remote to push the verified backup to (optional)" >&2
}

DB_URL=""
DEST=""
REMOTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db-url) DB_URL="${2:-}"; shift 2 ;;
    --dest)   DEST="${2:-}"; shift 2 ;;
    --remote) REMOTE="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$DB_URL" ]; then
  echo "error: --db-url is required" >&2; usage; exit 2
fi
if [ -z "$DEST" ]; then
  echo "error: --dest is required" >&2; usage; exit 2
fi

# Fail before the multi-hour copy, not after it: an unrunnable verification
# must never degrade into an unverified backup.
if [ ! -x "$PG_VERIFYBACKUP" ]; then
  echo "error: $PG_VERIFYBACKUP not found or not executable — refusing to take an unverifiable backup" >&2
  exit 1
fi
if [ -n "$REMOTE" ] && [ ! -x "$OFFSITE_SYNC" ]; then
  echo "error: $OFFSITE_SYNC not found or not executable" >&2
  exit 1
fi

if [ -e "$DEST" ]; then
  echo "error: destination already exists: $DEST" >&2
  exit 1
fi

# The backup lands in <dest>.inprogress and is renamed only after
# verification passes, so nothing at <dest> is ever a partial or unverified
# backup. On failure the partial is removed — a weekly cron that left partials
# behind would silently fill the disk.
TMP_DEST="$DEST.inprogress"
if [ -e "$TMP_DEST" ]; then
  echo "error: stale in-progress destination exists: $TMP_DEST (previous run crashed?) — inspect and remove it first" >&2
  exit 1
fi
trap 'rm -rf "$TMP_DEST"' EXIT

echo "base backup starting: $(date -Is)"
pg_basebackup -d "$DB_URL" -D "$TMP_DEST" -Ft -z -Xs \
  --checkpoint="$CHECKPOINT_MODE" --no-password

echo "backup complete, verifying: $(date -Is)"
# PG17 pg_verifybackup verifies tar-format backups natively (-Ft needs no
# extraction on this version; confirmed empirically against prod, task 3.4).
"$PG_VERIFYBACKUP" "$TMP_DEST"

mv "$TMP_DEST" "$DEST"
trap - EXIT

echo "verified base backup: $(du -sh "$DEST" | cut -f1) $DEST ($(date -Is))"

# Offsite last: the local backup is already complete and verified, so an
# offsite failure loses nothing locally — but it still fails the run, because
# "backed up" means the offsite copy checksum-matched (D6).
if [ -n "$REMOTE" ]; then
  "$OFFSITE_SYNC" --source "$DEST" --remote "$REMOTE"
fi
