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

# No /usr/bin wrapper on this host (same as pg_verifybackup).
PG_ARCHIVECLEANUP="/usr/lib/postgresql/17/bin/pg_archivecleanup"

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
  fi
done

OLDEST_RETAINED="${RETAINED[0]}"
MANIFEST="$BASE_DIR/$OLDEST_RETAINED/backup_manifest"
[ -r "$MANIFEST" ] || { echo "error: cannot read $MANIFEST" >&2; exit 1; }

# The manifest's WAL-Ranges start is the earliest WAL this backup needs.
# Anything older in the archive serves no retained backup.
OLDEST_SEGMENT=$(python3 - "$MANIFEST" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    manifest = json.load(f)
r = manifest["WAL-Ranges"][0]
tli, start = r["Timeline"], r["Start-LSN"]
high, low = (int(p, 16) for p in start.split("/"))
seg_size = 16 * 1024 * 1024
print(f"{tli:08X}{high:08X}{low // seg_size:08X}")
EOF
)

echo "oldest retained backup: $OLDEST_RETAINED (needs WAL from $OLDEST_SEGMENT)"
"$PG_ARCHIVECLEANUP" -d "$WAL_DIR" "$OLDEST_SEGMENT" 2>&1 | tail -3
echo "prune done: $(ls "$BASE_DIR" | wc -l) backups retained, $(ls "$WAL_DIR" | wc -l) archive files remain"
