#!/usr/bin/env bash
# setup-backup.sh — provision the backup tier on a host (slice 920, D1).
#
# One root-run script, explicit arguments, check-then-act, idempotent. Every
# item is reported as OK / DRIFT <expected> <actual> / MISSING; in apply mode
# a step acts only when its item is not OK and prints APPLIED <item>, so a
# run that changes nothing prints zero APPLIED lines. `--check` changes
# nothing and exits 1 if any item is not OK: it is the acceptance instrument
# and the periodic drift audit. NEVER restarts PostgreSQL (reports PENDING
# RESTART); never creates the reconcile arm file; never edits the user
# crontab. Recovery for any failure: fix the cause, re-run.
#
# Usage:
#   sudo deploy/setup-backup.sh --checkout <dir> --env-file <path> \
#       --backup-root <dir> --cluster <ver/name> [--check] [--rehearse <dir>] \
#       [--restic-prefix <name>]
#
# --restic-prefix <name>: use a scratch restic repository prefix instead of
# the production one — for the bootstrap acceptance run on a second host
# (runbook 210), so its snapshots never land in the real repository.
#
# --rehearse <dir>: cron.d renders to <dir>/cron.d, the timeshift file
# read/written is <dir>/timeshift.json, step 4 is skipped, the restic prefix
# becomes system-rehearse; everything else runs for real against the given
# --backup-root. For proving apply-mode idempotence on a throwaway root.
#
# Steps: 1 restic package; 2 directories; 3 WAL ACL; 4 PostgreSQL settings
# (deploy/lib/pg_settings.sh); 5 cron.d (deploy/lib/render_cron.sh);
# 6 timeshift (deploy/lib/timeshift_merge.sh); 7 restic repository
# (deploy/lib/restic_repo.sh); 7a reconcile arm file; 8 leftover user-crontab
# lines.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# --- Constants: the one definition of every number, name, and path -----------
WAL_OFFSITE_INTERVAL_MIN=60   # renders the push schedule, --stale-after (3x), --timeout (-1)
KEEP_DAYS=7                   # D4a: local == offsite retention
CRON_USER=manta
ARCHIVE_MODE=on
WAL_COMPRESSION=zstd
# D2 + D3 (go, 1.88x measured): atomic, compressed. @WAL_DIR@ is substituted
# with <backup-root>/wal; the runbook prints the rendered form.
ARCHIVE_COMMAND_TEMPLATE='test ! -f @WAL_DIR@/%f.zst && zstd -q -T2 %p -o @WAL_DIR@/%f.zst.tmp && mv @WAL_DIR@/%f.zst.tmp @WAL_DIR@/%f.zst && chmod 644 @WAL_DIR@/%f.zst'
TIMESHIFT_COUNT_WEEKLY=2
TIMESHIFT_EXCLUDES=('/home/manta/**' '/var/lib/postgresql/**' '/var/lib/libvirt/**' '/root/**')
TIMESHIFT_CONFIG=/etc/timeshift/timeshift.json
CRON_TEMPLATE="$SCRIPT_DIR/cron.d/manta-trading-backup"
CRON_TARGET=/etc/cron.d/manta-trading-backup
RESTIC_PACKAGE=restic
RESTIC_PREFIX=system
RESTIC_PREFIX_REHEARSE=system-rehearse
PG_CONF_ROOT=/etc/postgresql
PG_DATA_ROOT=/var/lib/postgresql
PG_OWNER=postgres:postgres
# 0775, not 0755: the group bits double as the ACL mask, so a 755 chmod would
# cap the cron user's named-ACL entry at r-x and silently break the prune.
WAL_DIR_MODE=775
BACKUP_SUBDIRS=(base wal metadata system)
ARM_FILE=RECONCILE-ARMED
RCLONE_REMOTE=b2            # the rclone remote name; wal/ and base/ prefixes hang off <remote>:<bucket>
LEGACY_CRON_SCRIPTS=(archive_health_cron.sh cron_nightly_metadata.sh cron_weekly_base.sh)

usage() {
  echo "usage: sudo $0 --checkout <dir> --env-file <path> --backup-root <dir> --cluster <ver/name> [--check] [--rehearse <dir>] [--restic-prefix <name>]" >&2
}
die() { echo "ERROR: $*" >&2; exit "${2:-1}"; }
step() { echo; echo "==> $*"; }

# --- Arguments: all required, no defaults ------------------------------------
CHECKOUT=""; ENV_FILE=""; BACKUP_ROOT=""; CLUSTER=""; CHECK=0; REHEARSE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --checkout)    CHECKOUT="${2:-}"; shift 2 ;;
    --env-file)    ENV_FILE="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --cluster)     CLUSTER="${2:-}"; shift 2 ;;
    --check)       CHECK=1; shift ;;
    --rehearse)    REHEARSE="${2:-}"; [ -n "$REHEARSE" ] || { usage; die "--rehearse needs a directory" 2; }; shift 2 ;;
    --restic-prefix) RESTIC_PREFIX="${2:-}"; [ -n "$RESTIC_PREFIX" ] || { usage; die "--restic-prefix needs a name" 2; }; shift 2 ;;
    --help|-h)     usage; exit 0 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
for pair in "--checkout:$CHECKOUT" "--env-file:$ENV_FILE" "--backup-root:$BACKUP_ROOT" "--cluster:$CLUSTER"; do
  [ -n "${pair#*:}" ] || { usage; die "missing required argument: ${pair%%:*}" 2; }
done
[ -r "$ENV_FILE" ] || die "env file not readable: $ENV_FILE"
[ -d "$CHECKOUT/scripts" ] || die "not a checkout (no scripts/ directory): $CHECKOUT"
if [ "$CHECK" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "apply mode must run as root (sudo $0 ...); --check may run as any user"
fi

WAL_DIR="$BACKUP_ROOT/wal"
PGDATA="$PG_DATA_ROOT/$CLUSTER"
PG_CONF="$PG_CONF_ROOT/$CLUSTER/postgresql.conf"
ARCHIVE_COMMAND=${ARCHIVE_COMMAND_TEMPLATE//@WAL_DIR@/$WAL_DIR}
BUCKET=$({ grep '^MT_BACKUP_S3_BUCKET=' "$ENV_FILE" || true; } | head -1 | sed 's/^[^=]*=//' | tr -d '"')
[ -n "$BUCKET" ] || die "MT_BACKUP_S3_BUCKET not in $ENV_FILE (the offsite remote cannot be rendered)"
REMOTE_PREFIX="$RCLONE_REMOTE:$BUCKET"
if [ -n "$REHEARSE" ]; then
  mkdir -p "$REHEARSE"
  CRON_TARGET="$REHEARSE/cron.d"; TIMESHIFT_CONFIG="$REHEARSE/timeshift.json"; RESTIC_PREFIX="$RESTIC_PREFIX_REHEARSE"
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
# Run a lib, echo its lines, and tally them by the same rule.
run_lib() {
  local out rc=0
  out=$("$@" 2>&1) || rc=$?
  [ -z "$out" ] || echo "$out"
  NOT_OK=$((NOT_OK + $(grep -cE '^(DRIFT|MISSING|PENDING RESTART) ' <<< "$out" || true)))
  APPLIED=$((APPLIED + $(grep -c '^APPLIED ' <<< "$out" || true)))
  if [ "$rc" -ne 0 ] && ! grep -qE '^(DRIFT|MISSING|PENDING RESTART) ' <<< "$out"; then
    report "MISSING $(basename "$1") exited $rc"
  fi
}
# Act only when the item is not OK; report both the action and the re-check.
apply() { # apply <item> <check-fn> <act-fn>
  if "$2"; then report "OK $1"; return; fi
  if [ "$CHECK" -eq 1 ]; then report "${3:-MISSING} $1${4:+ $4}"; return; fi
  "$5"; report "APPLIED $1"
  if "$2"; then report "OK $1"; else report "${3:-MISSING} $1${4:+ $4} (still, after apply)"; fi
}

echo "setup-backup: checkout=$CHECKOUT env=$ENV_FILE root=$BACKUP_ROOT cluster=$CLUSTER mode=$([ "$CHECK" -eq 1 ] && echo check || echo apply)${REHEARSE:+ rehearse=$REHEARSE}"
[ "$CHECK" -eq 0 ] || [ "$(id -u)" -eq 0 ] || echo "note: --check as $(id -un): PostgreSQL and crontab items need root to be readable" >&2

# --- Step 1: package -----------------------------------------------------------
step "Step 1/8: package $RESTIC_PACKAGE"
pkg_ok() { dpkg -s "$RESTIC_PACKAGE" >/dev/null 2>&1; }
pkg_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$RESTIC_PACKAGE" >/dev/null; }
apply "package-$RESTIC_PACKAGE" pkg_ok MISSING "" pkg_install

# --- Step 2: directories -------------------------------------------------------
step "Step 2/8: directories under $BACKUP_ROOT"
for sub in "${BACKUP_SUBDIRS[@]}"; do
  dir_ok() { [ -d "$BACKUP_ROOT/$sub" ]; }
  dir_make() { mkdir -p "$BACKUP_ROOT/$sub"; }
  apply "dir-$sub" dir_ok MISSING "$BACKUP_ROOT/$sub" dir_make
done
WAL_SHAPE_ACTUAL=""
wal_shape_ok() { WAL_SHAPE_ACTUAL=$(stat -c '%U:%G %a' "$WAL_DIR" 2>/dev/null || echo absent); [ "$WAL_SHAPE_ACTUAL" = "$PG_OWNER $WAL_DIR_MODE" ]; }
wal_shape_fix() { chown "$PG_OWNER" "$WAL_DIR"; chmod "$WAL_DIR_MODE" "$WAL_DIR"; }
wal_shape_ok || true
apply "wal-dir-owner-mode" wal_shape_ok DRIFT "'$PG_OWNER $WAL_DIR_MODE' '$WAL_SHAPE_ACTUAL'" wal_shape_fix

# --- Step 3: ACL ---------------------------------------------------------------
step "Step 3/8: ACL for $CRON_USER on $WAL_DIR"
acl_ok() {
  local acl; acl=$(getfacl -p --omit-header "$WAL_DIR" 2>/dev/null) || return 1
  grep -qx "user:$CRON_USER:rwx" <<< "$acl" && grep -qx "default:user:$CRON_USER:rwx" <<< "$acl"
}
acl_set() { setfacl -m "u:$CRON_USER:rwx" -m "d:u:$CRON_USER:rwx" "$WAL_DIR"; }
apply "wal-acl-$CRON_USER" acl_ok MISSING "" acl_set

# --- Step 4: PostgreSQL settings ----------------------------------------------
step "Step 4/8: PostgreSQL settings ($CLUSTER)"
if [ -n "$REHEARSE" ]; then
  report "SKIPPED step-4 (rehearse)"
else
  # psql runs as postgres when root (peer auth, superuser for ALTER SYSTEM);
  # the library itself stays the caller's process — postgres cannot read a
  # checkout under an operator's home. Non-root (only --check permits it):
  # psql as the invoking user.
  PG_AS_USER=(); [ "$(id -u)" -ne 0 ] || PG_AS_USER=(--as-user postgres)
  run_lib env PGCLUSTER="$CLUSTER" "$LIB_DIR/pg_settings.sh" "${CHECK_FLAG[@]}" "${PG_AS_USER[@]}" \
    --conf "$PG_CONF" --set "archive_mode=$ARCHIVE_MODE" --set "archive_command=$ARCHIVE_COMMAND" \
    --set "wal_compression=$WAL_COMPRESSION" --forbid-conf-line archive_command
fi

# --- Step 5: cron.d -------------------------------------------------------------
step "Step 5/8: $CRON_TARGET"
RENDERED=$(mktemp); trap 'rm -f "$RENDERED"' EXIT
"$LIB_DIR/render_cron.sh" --template "$CRON_TEMPLATE" --interval "$WAL_OFFSITE_INTERVAL_MIN" \
  --checkout "$CHECKOUT" --env-file "$ENV_FILE" --backup-root "$BACKUP_ROOT" --cron-user "$CRON_USER" \
  --pgdata "$PGDATA" --keep-days "$KEEP_DAYS" --remote-prefix "$REMOTE_PREFIX" --restic-prefix "$RESTIC_PREFIX" \
  --out "$RENDERED"
cron_ok() { cmp -s "$RENDERED" "$CRON_TARGET"; }
cron_install() { install -m 0644 -o root -g root "$RENDERED" "$CRON_TARGET"; }
if [ -e "$CRON_TARGET" ]; then CRON_STATE=DRIFT; else CRON_STATE=MISSING; fi
apply "cron.d" cron_ok "$CRON_STATE" "$CRON_TARGET" cron_install

# --- Step 6: timeshift ----------------------------------------------------------
step "Step 6/8: timeshift managed keys ($TIMESHIFT_CONFIG)"
TS_ARGS=(--file "$TIMESHIFT_CONFIG" --count-weekly "$TIMESHIFT_COUNT_WEEKLY")
for x in "${TIMESHIFT_EXCLUDES[@]}"; do TS_ARGS+=(--exclude "$x"); done
run_lib "$LIB_DIR/timeshift_merge.sh" "${CHECK_FLAG[@]}" "${TS_ARGS[@]}"

# --- Step 7: restic repository ------------------------------------------------
step "Step 7/8: restic repository (prefix $RESTIC_PREFIX)"
RESTIC_CMD=init; [ "$CHECK" -eq 0 ] || RESTIC_CMD=check
run_lib "$LIB_DIR/restic_repo.sh" --env-file "$ENV_FILE" --prefix "$RESTIC_PREFIX" "$RESTIC_CMD"

# --- Step 7a: reconcile arm file (reported, never created) --------------------
step "Step 7a: reconcile arm file"
if [ -e "$BACKUP_ROOT/$ARM_FILE" ]; then
  report "OK arm-file $BACKUP_ROOT/$ARM_FILE"
else
  report "MISSING arm-file $BACKUP_ROOT/$ARM_FILE (created by hand after the watched first reconcile; see runbook)"
fi

# --- Step 8: leftover user-crontab lines (reported, never edited) -------------
step "Step 8/8: user crontab of $CRON_USER"
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
