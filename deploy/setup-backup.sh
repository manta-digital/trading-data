#!/usr/bin/env bash
# setup-backup.sh — provision the backup tier on a host (slice 920 D1, 227).
#
# One root-run script, explicit arguments, check-then-act, idempotent. Every
# item is reported as OK / DRIFT <expected> <actual> / MISSING; in apply mode
# a step acts only when its item is not OK and prints APPLIED <item>, so a
# run that changes nothing prints zero APPLIED lines. `--check` changes
# nothing and exits 1 if any item is not OK: it is the acceptance instrument
# and the periodic drift audit. NEVER restarts PostgreSQL (reports PENDING
# RESTART); never creates a reconcile arm file (a person or a cutover does,
# runbook 200); never edits the user
# crontab; never sets a B2 lifecycle rule. Recovery for any failure: fix the
# cause, re-run.
#
# Clusters come from deploy/backup-clusters.conf (slice 227, TD2), parsed
# before any step so a malformed row changes nothing. Per-cluster items are
# prefixed with the cluster (`17/tick dir-wal`); host items are not.
#
# Usage:
#   sudo deploy/setup-backup.sh --checkout <dir> --env-file <path> \
#       --backup-root <dir> [--check] [--rehearse <dir>] [--restic-prefix <name>]
#
# --backup-root is the HOST root: the system/ directory and the restic stamp,
# log and lock. Each cluster's own root is the table's backup_root.
#
# --restic-prefix <name>: use a scratch restic repository prefix instead of
# the production one — for the bootstrap acceptance run on a second host
# (runbook 210), so its snapshots never land in the real repository.
#
# --rehearse <dir>: cron.d renders to <dir>/cron.d (naming the table's real
# roots: it is compared, never installed), the timeshift file
# read/written is <dir>/timeshift.json, every cluster root moves under
# <dir>/clusters (e.g. <dir>/clusters/data/backup/17-tick), PostgreSQL
# settings are skipped, the restic prefix becomes system-rehearse; everything
# else runs for real. For proving apply-mode idempotence on a throwaway root.
#
# Steps: 1 restic package; 2 host directories; 3 per cluster: directories,
# WAL ACL, PostgreSQL settings (deploy/lib/pg_settings.sh), arm file, B2
# lifecycle (deploy/lib/b2_lifecycle.sh); 4 cron.d (deploy/lib/render_cron.sh);
# 5 timeshift (deploy/lib/timeshift_merge.sh); 6 restic repository
# (deploy/lib/restic_repo.sh); 7 leftover user-crontab lines.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
# shellcheck source=lib/env_value.sh
. "$LIB_DIR/env_value.sh"
# shellcheck source=lib/backup_clusters.sh
. "$LIB_DIR/backup_clusters.sh"

# --- Constants: the one definition of every number, name, and path -----------
WAL_OFFSITE_INTERVAL_MIN=60   # renders the push schedule, --stale-after (3x), --timeout (-1)
KEEP_DAYS=7                   # D4a: local == offsite retention
CRON_USER=manta
ARCHIVE_MODE=on
WAL_COMPRESSION=zstd
# D2 + D3 (go, 1.88x measured): atomic, compressed. @WAL_DIR@ is substituted
# with <cluster root>/wal; the runbook prints the rendered form.
ARCHIVE_COMMAND_TEMPLATE='test ! -f @WAL_DIR@/%f.zst && zstd -q -T2 %p -o @WAL_DIR@/%f.zst.tmp && mv @WAL_DIR@/%f.zst.tmp @WAL_DIR@/%f.zst && chmod 644 @WAL_DIR@/%f.zst'
TIMESHIFT_COUNT_WEEKLY=2
TIMESHIFT_EXCLUDES=('/home/manta/**' '/var/lib/postgresql/**' '/var/lib/libvirt/**' '/root/**')
TIMESHIFT_CONFIG=/etc/timeshift/timeshift.json
CRON_TEMPLATE="$SCRIPT_DIR/cron.d/manta-trading-backup"
CRON_CLUSTER_TEMPLATE="$SCRIPT_DIR/cron.d/manta-trading-backup.cluster"
CLUSTER_TABLE="$SCRIPT_DIR/backup-clusters.conf"
CRON_TARGET=/etc/cron.d/manta-trading-backup
RESTIC_PACKAGE=restic
RESTIC_PREFIX=system
RESTIC_PREFIX_REHEARSE=system-rehearse
PG_CONF_ROOT=/etc/postgresql
PG_OWNER=postgres:postgres
# 0775, not 0755: the group bits double as the ACL mask, so a 755 chmod would
# cap the cron user's named-ACL entry at r-x and silently break the prune.
WAL_DIR_MODE=775
# A cluster root, base/ and metadata/ are written by the cron user's jobs
# (logs, stamps, flags, base backups, dumps): production's shape since 915.
CRON_OWNER="$CRON_USER:$CRON_USER"
CRON_DIR_MODE=775
CLUSTER_SUBDIRS=(base metadata)   # plus wal/, which postgres owns
HOST_SUBDIRS=(system)
ARM_FILE=RECONCILE-ARMED
RCLONE_REMOTE=b2            # the rclone remote name; wal/ and base/ prefixes hang off <remote>:<bucket>
# TD10: the rule production's offsite prefixes carry (read 2026-10-04).
LIFECYCLE_PREFIXES=(base/ wal/)
LIFECYCLE_DAYS_FROM_HIDING_TO_DELETING=30
LEGACY_CRON_SCRIPTS=(archive_health_cron.sh cron_nightly_metadata.sh cron_weekly_base.sh)

usage() {
  echo "usage: sudo $0 --checkout <dir> --env-file <path> --backup-root <dir> [--check] [--rehearse <dir>] [--restic-prefix <name>]" >&2
}
die() { echo "ERROR: $*" >&2; exit "${2:-1}"; }
step() { echo; echo "==> $*"; }

# --- Arguments: all required, no defaults ------------------------------------
CHECKOUT=""; ENV_FILE=""; BACKUP_ROOT=""; CHECK=0; REHEARSE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --checkout)    CHECKOUT="${2:-}"; shift 2 ;;
    --env-file)    ENV_FILE="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --check)       CHECK=1; shift ;;
    --rehearse)    REHEARSE="${2:-}"; [ -n "$REHEARSE" ] || { usage; die "--rehearse needs a directory" 2; }; shift 2 ;;
    --restic-prefix) RESTIC_PREFIX="${2:-}"; [ -n "$RESTIC_PREFIX" ] || { usage; die "--restic-prefix needs a name" 2; }; shift 2 ;;
    --help|-h)     usage; exit 0 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
for pair in "--checkout:$CHECKOUT" "--env-file:$ENV_FILE" "--backup-root:$BACKUP_ROOT"; do
  [ -n "${pair#*:}" ] || { usage; die "missing required argument: ${pair%%:*}" 2; }
done
[ -r "$ENV_FILE" ] || die "env file not readable: $ENV_FILE"
[ -d "$CHECKOUT/scripts" ] || die "not a checkout (no scripts/ directory): $CHECKOUT"
if [ "$CHECK" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "apply mode must run as root (sudo $0 ...); --check may run as any user"
fi

# --- The cluster table and each cluster's data directory, before any step ----
backup_clusters_load "$CLUSTER_TABLE" || die "cluster table rejected: $CLUSTER_TABLE (nothing changed)"
# TD4: the data directory is what pg_lsclusters says, never a derived path.
# Columns: Ver Cluster Port Status Owner Data-directory Log-file.
declare -A PGDATA_OF=()
LSCLUSTERS=$(pg_lsclusters --no-header) || die "pg_lsclusters failed (nothing changed)"
while read -r ver name _port _status _owner datadir _rest; do
  [ -n "${ver:-}" ] && PGDATA_OF["$ver/$name"]=$datadir
done <<< "$LSCLUSTERS"
for c in "${BC_CLUSTER[@]}"; do
  [ -n "${PGDATA_OF[$c]:-}" ] || die "cluster $c is in $CLUSTER_TABLE but not in pg_lsclusters (nothing changed)"
done

BUCKET=$(env_value "$ENV_FILE" MT_BACKUP_S3_BUCKET)
[ -n "$BUCKET" ] || die "MT_BACKUP_S3_BUCKET not in $ENV_FILE (the offsite remote cannot be rendered)"
REMOTE_PREFIX="$RCLONE_REMOTE:$BUCKET"
ROOT_OF=("${BC_ROOT[@]}")
if [ -n "$REHEARSE" ]; then
  mkdir -p "$REHEARSE"
  CRON_TARGET="$REHEARSE/cron.d"; TIMESHIFT_CONFIG="$REHEARSE/timeshift.json"; RESTIC_PREFIX="$RESTIC_PREFIX_REHEARSE"
  for i in "${!ROOT_OF[@]}"; do ROOT_OF[i]="$REHEARSE/clusters${ROOT_OF[$i]}"; done
fi
CHECK_FLAG=(); [ "$CHECK" -eq 0 ] || CHECK_FLAG=(--check)

# --- Reporting ---------------------------------------------------------------
NOT_OK=0; APPLIED=0
report() { # report "<STATUS> <item> [detail]"; tallied by the status word
  echo "$*"
  case "${1%% *}" in
    OK|INFO|SKIPPED) ;;
    APPLIED) APPLIED=$((APPLIED + 1)) ;;
    *) NOT_OK=$((NOT_OK + 1)) ;;
  esac
}
# Run a lib, echo its lines (prefixed with $2 when non-empty), tally them.
run_lib() { # run_lib <prefix> <lib> [args...]
  local prefix=$1 out rc=0; shift
  out=$("$@" 2>&1) || rc=$?
  [ -z "$prefix" ] || out=$(sed -E "s#^(OK|DRIFT|MISSING|APPLIED|PENDING RESTART|INFO) #\1 $prefix #" <<< "$out")
  [ -z "$out" ] || echo "$out"
  NOT_OK=$((NOT_OK + $(grep -cE '^(DRIFT|MISSING|PENDING RESTART) ' <<< "$out" || true)))
  APPLIED=$((APPLIED + $(grep -c '^APPLIED ' <<< "$out" || true)))
  if [ "$rc" -ne 0 ] && ! grep -qE '^(DRIFT|MISSING|PENDING RESTART) ' <<< "$out"; then
    report "MISSING ${prefix:+$prefix }$(basename "$1") exited $rc"
  fi
}
# Act only when the item is not OK; report both the action and the re-check.
apply() { # apply <item> <check-fn> <not-ok-word> <detail> <act-fn>
  if "$2"; then report "OK $1"; return; fi
  if [ "$CHECK" -eq 1 ]; then report "${3:-MISSING} $1${4:+ $4}"; return; fi
  "$5"; report "APPLIED $1"
  if "$2"; then report "OK $1"; else report "${3:-MISSING} $1${4:+ $4} (still, after apply)"; fi
}
# A directory with an owner and mode: exists, then shape.
SHAPE_ACTUAL=""
ensure_dir() { # ensure_dir <item> <dir> <owner> <mode>
  local item=$1 dir=$2 owner=$3 mode=$4
  dir_ok() { [ -d "$dir" ]; }
  dir_make() { mkdir -p "$dir"; }
  apply "$item" dir_ok MISSING "$dir" dir_make
  shape_ok() { SHAPE_ACTUAL=$(stat -c '%U:%G %a' "$dir" 2>/dev/null || echo absent); [ "$SHAPE_ACTUAL" = "$owner $mode" ]; }
  shape_fix() { chown "$owner" "$dir"; chmod "$mode" "$dir"; }
  shape_ok || true
  apply "$item-owner-mode" shape_ok DRIFT "'$owner $mode' '$SHAPE_ACTUAL'" shape_fix
}

echo "setup-backup: checkout=$CHECKOUT env=$ENV_FILE host-root=$BACKUP_ROOT clusters=${BC_CLUSTER[*]} mode=$([ "$CHECK" -eq 1 ] && echo check || echo apply)${REHEARSE:+ rehearse=$REHEARSE}"
[ "$CHECK" -eq 0 ] || [ "$(id -u)" -eq 0 ] || echo "note: --check as $(id -un): PostgreSQL and crontab items need root to be readable" >&2

# --- Step 1: package -----------------------------------------------------------
step "Step 1/7: package $RESTIC_PACKAGE"
pkg_ok() { dpkg -s "$RESTIC_PACKAGE" >/dev/null 2>&1; }
pkg_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$RESTIC_PACKAGE" >/dev/null; }
apply "package-$RESTIC_PACKAGE" pkg_ok MISSING "" pkg_install

# --- Step 2: host directories --------------------------------------------------
step "Step 2/7: host directories under $BACKUP_ROOT"
for sub in "${HOST_SUBDIRS[@]}"; do
  host_dir_ok() { [ -d "$BACKUP_ROOT/$sub" ]; }
  host_dir_make() { mkdir -p "$BACKUP_ROOT/$sub"; }
  apply "dir-$sub" host_dir_ok MISSING "$BACKUP_ROOT/$sub" host_dir_make
done

# --- Step 3: per cluster -------------------------------------------------------
for i in "${!BC_CLUSTER[@]}"; do
  C=${BC_CLUSTER[$i]}; ROOT=${ROOT_OF[$i]}; WAL_DIR="$ROOT/wal"
  step "Step 3/7: cluster $C (root $ROOT, data ${PGDATA_OF[$C]})"
  ensure_dir "$C dir-root" "$ROOT" "$CRON_OWNER" "$CRON_DIR_MODE"
  for sub in "${CLUSTER_SUBDIRS[@]}"; do
    ensure_dir "$C dir-$sub" "$ROOT/$sub" "$CRON_OWNER" "$CRON_DIR_MODE"
  done
  ensure_dir "$C dir-wal" "$WAL_DIR" "$PG_OWNER" "$WAL_DIR_MODE"

  acl_ok() {
    local acl; acl=$(getfacl -p --omit-header "$WAL_DIR" 2>/dev/null) || return 1
    grep -qx "user:$CRON_USER:rwx" <<< "$acl" && grep -qx "default:user:$CRON_USER:rwx" <<< "$acl"
  }
  acl_set() { setfacl -m "u:$CRON_USER:rwx" -m "d:u:$CRON_USER:rwx" "$WAL_DIR"; }
  apply "$C wal-acl-$CRON_USER" acl_ok MISSING "" acl_set

  if [ -n "$REHEARSE" ]; then
    report "SKIPPED $C pg-settings (rehearse)"
  else
    # psql runs as postgres when root (peer auth, superuser for ALTER SYSTEM);
    # the library itself stays the caller's process — postgres cannot read a
    # checkout under an operator's home. Non-root (only --check permits it):
    # psql as the invoking user.
    PG_AS_USER=(); [ "$(id -u)" -ne 0 ] || PG_AS_USER=(--as-user postgres)
    ARCHIVE_COMMAND=${ARCHIVE_COMMAND_TEMPLATE//@WAL_DIR@/"$WAL_DIR"}
    run_lib "$C" env PGCLUSTER="$C" "$LIB_DIR/pg_settings.sh" "${CHECK_FLAG[@]}" "${PG_AS_USER[@]}" \
      --conf "$PG_CONF_ROOT/$C/postgresql.conf" --set "archive_mode=$ARCHIVE_MODE" \
      --set "archive_command=$ARCHIVE_COMMAND" --set "wal_compression=$WAL_COMPRESSION" \
      --forbid-conf-line archive_command
  fi

  # Reported, never created: armed by hand after a watched reconcile (TD7).
  if [ -e "$ROOT/$ARM_FILE" ]; then
    report "OK $C arm-file $ROOT/$ARM_FILE"
  else
    report "MISSING $C arm-file $ROOT/$ARM_FILE (production: created by hand after a watched reconcile; 17/tick: created by the 227 cutover after its first weekly run; see runbook 200)"
  fi

  # TD10: a missing rule is printed but never tallied (it costs storage, not
  # data); only an unreadable rule set is a failure.
  LIFECYCLE_OUT=$("$LIB_DIR/b2_lifecycle.sh" --env-file "$ENV_FILE" --subpath "${BC_REMOTE[$i]}" \
    --days "$LIFECYCLE_DAYS_FROM_HIDING_TO_DELETING" "${LIFECYCLE_PREFIXES[@]}" 2>&1) || true
  while IFS= read -r line; do
    case "$line" in
      "OK "*|"MISSING lifecycle "*) echo "${line%% *} $C ${line#* }" ;;
      *) report "MISSING $C lifecycle-read ${line#UNREADABLE lifecycle }" ;;
    esac
  done <<< "$LIFECYCLE_OUT"
done

# --- Step 4: cron.d -------------------------------------------------------------
step "Step 4/7: $CRON_TARGET"
RENDERED=$(mktemp); trap 'rm -f "$RENDERED"' EXIT
PGDATA_ARGS=()
for c in "${BC_CLUSTER[@]}"; do PGDATA_ARGS+=(--pgdata "$c=${PGDATA_OF[$c]}"); done
"$LIB_DIR/render_cron.sh" --template "$CRON_TEMPLATE" --cluster-template "$CRON_CLUSTER_TEMPLATE" \
  --clusters "$CLUSTER_TABLE" --interval "$WAL_OFFSITE_INTERVAL_MIN" \
  --checkout "$CHECKOUT" --env-file "$ENV_FILE" --host-root "$BACKUP_ROOT" --cron-user "$CRON_USER" \
  "${PGDATA_ARGS[@]}" --keep-days "$KEEP_DAYS" --remote-prefix "$REMOTE_PREFIX" --restic-prefix "$RESTIC_PREFIX" \
  --out "$RENDERED"
cron_ok() { cmp -s "$RENDERED" "$CRON_TARGET"; }
cron_install() { install -m 0644 -o root -g root "$RENDERED" "$CRON_TARGET"; }
if [ -e "$CRON_TARGET" ]; then CRON_STATE=DRIFT; else CRON_STATE=MISSING; fi
apply "cron.d" cron_ok "$CRON_STATE" "$CRON_TARGET" cron_install

# --- Step 5: timeshift ----------------------------------------------------------
step "Step 5/7: timeshift managed keys ($TIMESHIFT_CONFIG)"
TS_ARGS=(--file "$TIMESHIFT_CONFIG" --count-weekly "$TIMESHIFT_COUNT_WEEKLY")
for x in "${TIMESHIFT_EXCLUDES[@]}"; do TS_ARGS+=(--exclude "$x"); done
run_lib "" "$LIB_DIR/timeshift_merge.sh" "${CHECK_FLAG[@]}" "${TS_ARGS[@]}"

# --- Step 6: restic repository ------------------------------------------------
step "Step 6/7: restic repository (prefix $RESTIC_PREFIX)"
RESTIC_CMD=init; [ "$CHECK" -eq 0 ] || RESTIC_CMD=check
run_lib "" "$LIB_DIR/restic_repo.sh" --env-file "$ENV_FILE" --prefix "$RESTIC_PREFIX" "$RESTIC_CMD"

# --- Step 7: leftover user-crontab lines (reported, never edited) -------------
step "Step 7/7: user crontab of $CRON_USER"
CRON_ERR=""
if USER_CRON=$(crontab -l -u "$CRON_USER" 2>&1) || { CRON_ERR="$USER_CRON"; USER_CRON=""; case "$CRON_ERR" in *"no crontab for"*) true ;; *) false ;; esac; }; then
  # A user with no crontab at all is the clean state, not an unreadable one.
  LEFT=""
  for s in "${LEGACY_CRON_SCRIPTS[@]}"; do grep -q "$s" <<< "$USER_CRON" && LEFT+="${LEFT:+ }$s"; done
  if [ -n "$LEFT" ]; then
    report "DRIFT user-crontab still runs: $LEFT (remove those lines by hand; cron.d runs them now)"
  else
    report "OK user-crontab"
  fi
else
  report "MISSING user-crontab (cannot read crontab of $CRON_USER as $(id -un): ${CRON_ERR:-$USER_CRON})"
fi

# --- Summary -------------------------------------------------------------------
echo
echo "SUMMARY applied=$APPLIED not-ok=$NOT_OK mode=$([ "$CHECK" -eq 1 ] && echo check || echo apply)"
[ "$NOT_OK" -eq 0 ]
