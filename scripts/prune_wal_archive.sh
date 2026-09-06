#!/usr/bin/env bash
# prune_wal_archive.sh — retention for base backups and the WAL archive (D2, 4.5).
#
# Retention is keyed to the oldest RETAINED base backup, never to a bare age:
# WAL older than that backup's start position is useless, WAL newer than the
# newest backup is mandatory. Deleting by age alone can sever the newest
# backup from its WAL — the exact failure the LLD's recovery table warns of.
#
# Usage:
#   prune_wal_archive.sh --base-dir <dir> --wal-dir <dir> --keep-days <n>
#
# All arguments explicit (D5). Destructive: only touches dated (YYYYMMDD)
# directories under --base-dir and, via pg_archivecleanup, segments under
# --wal-dir older than the oldest retained backup's start segment. The newest
# backup is always kept regardless of age.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# No /usr/bin wrapper on this host (same as pg_verifybackup).
PG_ARCHIVECLEANUP="/usr/lib/postgresql/17/bin/pg_archivecleanup"
# Archived segments are compressed since slice 920 (D3); the extension is
# stripped before the age comparison, a no-op on raw names, so a mixed
# archive (raw before the cutover, .zst after) prunes correctly.
ARCHIVE_EXT=.zst

usage() {
  echo "usage: $0 --base-dir <dir> --wal-dir <dir> --keep-days <n>" >&2
}

BASE_DIR=""; WAL_DIR=""; KEEP_DAYS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base-dir)  BASE_DIR="${2:-}"; shift 2 ;;
    --wal-dir)   WAL_DIR="${2:-}"; shift 2 ;;
    --keep-days) KEEP_DAYS="${2:-}"; shift 2 ;;
    *) echo "error: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$BASE_DIR" ] || { echo "error: --base-dir is required" >&2; usage; exit 2; }
[ -n "$WAL_DIR" ] || { echo "error: --wal-dir is required" >&2; usage; exit 2; }
[ -n "$KEEP_DAYS" ] || { echo "error: --keep-days is required" >&2; usage; exit 2; }
[ -x "$PG_ARCHIVECLEANUP" ] || { echo "error: $PG_ARCHIVECLEANUP not found" >&2; exit 1; }

mapfile -t BACKUPS < <(find "$BASE_DIR" -maxdepth 1 -mindepth 1 -type d -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' -printf '%f\n' | sort)
if [ "${#BACKUPS[@]}" -eq 0 ]; then
  echo "error: no dated base backups under $BASE_DIR — refusing to prune anything" >&2
  exit 1
fi

count_archive_files() { find "$WAL_DIR" -maxdepth 1 -type f | wc -l; }
WAL_BEFORE=$(count_archive_files)
BASE_PRUNED=0

CUTOFF=$(date -d "-${KEEP_DAYS} days" +%Y%m%d)
NEWEST="${BACKUPS[-1]}"

RETAINED=()
for b in "${BACKUPS[@]}"; do
  # The newest backup is sacred whatever its age: pruning must never leave
  # zero restorable backups.
  if [ "$b" = "$NEWEST" ] || [ "$b" -ge "$CUTOFF" ]; then
    RETAINED+=("$b")
  else
    echo "pruning base backup $BASE_DIR/$b (older than $KEEP_DAYS days)"
    rm -rf "${BASE_DIR:?}/$b"
    BASE_PRUNED=$((BASE_PRUNED + 1))
  fi
done

OLDEST_RETAINED="${RETAINED[0]}"
MANIFEST="$BASE_DIR/$OLDEST_RETAINED/backup_manifest"
[ -r "$MANIFEST" ] || { echo "error: cannot read $MANIFEST" >&2; exit 1; }

# The manifest's WAL-Ranges start is the earliest WAL this backup needs.
# Anything older in the archive serves no retained backup. The segment-name
# arithmetic lives in wal_segment_name.py (shared with the health check).
read -r OLDEST_TLI OLDEST_LSN < <(jq -r '.["WAL-Ranges"][0] | "\(.Timeline) \(.["Start-LSN"])"' "$MANIFEST")
OLDEST_SEGMENT=$("$SCRIPT_DIR/wal_segment_name.py" from-lsn "$OLDEST_TLI" "$OLDEST_LSN")

echo "oldest retained backup: $OLDEST_RETAINED (needs WAL from $OLDEST_SEGMENT)"
"$PG_ARCHIVECLEANUP" -d -x "$ARCHIVE_EXT" "$WAL_DIR" "$OLDEST_SEGMENT" 2>&1 | tail -3
WAL_AFTER=$(count_archive_files)
echo "prune done: ${#RETAINED[@]} backups retained, $WAL_AFTER archive files remain"
# Machine-readable last line: the weekly reconcile sizes its --max-delete from it.
echo "PRUNED wal=$((WAL_BEFORE - WAL_AFTER)) base=$BASE_PRUNED"
