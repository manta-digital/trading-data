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
# backup. A partial from a failed *copy* is removed (a weekly cron that left
# partials behind would silently fill the disk); a completed copy that fails
# *verification* is preserved as <dest>.failed for diagnosis — measured
# 2026-08-16, the copy phase alone is ~2 hours, too expensive to discard as a
# side effect of a verification problem.
TMP_DEST="$DEST.inprogress"
VERIFY_DIR="$DEST.verify"
FAILED_DEST="$DEST.failed"
for leftover in "$TMP_DEST" "$VERIFY_DIR" "$FAILED_DEST"; do
  if [ -e "$leftover" ]; then
    echo "error: leftover from a previous run exists: $leftover — inspect and remove it first" >&2
    exit 1
  fi
done
trap 'rm -rf "$TMP_DEST" "$VERIFY_DIR"' EXIT

echo "base backup starting: $(date -Is)"
pg_basebackup -d "$DB_URL" -D "$TMP_DEST" -Ft -z -Xs \
  --checkpoint="$CHECKPOINT_MODE" --no-password

# Copy phase done — from here on, failures preserve the archives.
trap 'rm -rf "$VERIFY_DIR"; mv "$TMP_DEST" "$FAILED_DEST"; echo "error: verification failed — backup preserved at $FAILED_DEST for diagnosis, NOT restorable-verified" >&2' EXIT

echo "backup complete, verifying (extract + manifest checksums): $(date -Is)"
# Determined empirically against prod 2026-08-16 (task 3.4): PG17's
# pg_verifybackup verifies PLAIN-format backups only — pointed at a -Ft
# directory it reports every manifest entry as missing (tar support arrives
# in PG18). So: extract to a sibling scratch dir on the same filesystem,
# verify the extracted tree against the manifest, then discard the
# extraction. Costs one decompression pass per backup; it is the only true
# checksum-against-manifest verification available on this version.
for archive in "$TMP_DEST"/*.tar.gz; do
  case "$(basename "$archive")" in
    base.tar.gz|pg_wal.tar.gz) ;;
    *)
      # A third archive means a tablespace tarball; this cluster has none and
      # the extraction layout below would silently mis-verify one.
      echo "error: unexpected archive $archive — tablespace backups are not supported by this wrapper" >&2
      exit 1
      ;;
  esac
done
mkdir "$VERIFY_DIR" "$VERIFY_DIR/pg_wal"
tar -xzf "$TMP_DEST/base.tar.gz" -C "$VERIFY_DIR"
tar -xzf "$TMP_DEST/pg_wal.tar.gz" -C "$VERIFY_DIR/pg_wal"
"$PG_VERIFYBACKUP" -m "$TMP_DEST/backup_manifest" "$VERIFY_DIR"
rm -rf "$VERIFY_DIR"

mv "$TMP_DEST" "$DEST"
trap - EXIT

echo "verified base backup: $(du -sh "$DEST" | cut -f1) $DEST ($(date -Is))"

# Offsite last: the local backup is already complete and verified, so an
# offsite failure loses nothing locally — but it still fails the run, because
# "backed up" means the offsite copy checksum-matched (D6).
if [ -n "$REMOTE" ]; then
  "$OFFSITE_SYNC" --source "$DEST" --remote "$REMOTE"
fi
