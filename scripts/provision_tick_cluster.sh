#!/usr/bin/env bash
# provision_tick_cluster.sh — the tick PostgreSQL cluster (slice 226, TD1).
#
# Creates and configures `17/tick` (port 5433, data on /data), the databases
# trading_tick (the keeper) and trading_tick_proof (disposable, for the proof
# harness), the roles tick_app and tick_migrate, and the four tick URLs in the
# checkout's .env. Never touches the production cluster `17/main` beyond
# reading its TimescaleDB version and its port.
#
# Host-script convention: check-then-act, expected against seen. Every item
# prints OK / DRIFT <expected> <seen> / MISSING / APPLIED / WOULD; a re-run
# that changes nothing prints no APPLIED line and `0 keys added`. The log goes
# to /var/log/manta-tick-provision-<timestamp>.log and, after a credential
# scan, a copy under project-documents/user/notes/.
#
# Usage (from anywhere; the checkout is this script's parent directory):
#   sudo scripts/provision_tick_cluster.sh        apply
#   scripts/provision_tick_cluster.sh --check     no root, changes nothing
#
# --check exits 0 when every pre-check passes and every copied log is clean;
# it prints WOULD for each action an apply run would take. Items it cannot
# read without root are reported as SKIP.
#
# Recovery for any failure: fix the cause, re-run. Passwords are generated
# once per role and never printed; a role whose URL is already in .env keeps
# that password (the role is re-synced to it if they differ).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECKOUT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$CHECKOUT/deploy/lib"
# shellcheck source=SCRIPTDIR/../deploy/lib/env_value.sh
. "$LIB_DIR/env_value.sh"
# shellcheck source=SCRIPTDIR/../deploy/lib/env_add_keys.sh
. "$LIB_DIR/env_add_keys.sh"
# shellcheck source=SCRIPTDIR/../deploy/lib/log_scrub.sh
. "$LIB_DIR/log_scrub.sh"

# --- Memory: TD1's starting values, in pg_settings base units ---------------
SHARED_BUFFERS_8KB=$((4 * 1024 * 1024 / 8))         # 4 GB
EFFECTIVE_CACHE_SIZE_8KB=$((16 * 1024 * 1024 / 8))  # 16 GB, planner hint only
WORK_MEM_KB=$((64 * 1024))                          # 64 MB
MAINTENANCE_WORK_MEM_KB=$((1024 * 1024))            # 1 GB
MAX_CONNECTIONS=30
TS_MAX_BACKGROUND_WORKERS=4
MAX_WAL_SIZE_MB=$((8 * 1024))                       # 8 GB

# --- Host facts and names: the one definition of each -----------------------
PG_VERSION=17
CLUSTER=tick
PORT=5433
DATA_ROOT=/data/postgresql
DATA_DIR="$DATA_ROOT/$PG_VERSION/$CLUSTER"
DATA_VOLUME=/data
MIN_FREE_BYTES=$((100 * 1000 ** 3))                 # 100 GB before creating
LISTEN_ADDRESS=127.0.1.1                            # what `manta9000` resolves to
HOST_NAME=manta9000
PG_OWNER=postgres
PG_DIR_MODE=700
CONF_DIR="/etc/postgresql/$PG_VERSION/$CLUSTER"
UNIT="postgresql@$PG_VERSION-$CLUSTER"
PROD_PORT=5432
PROD_CONF_DIR="/etc/postgresql/$PG_VERSION/main"
PROD_AUTO_CONF="/var/lib/postgresql/$PG_VERSION/main/postgresql.auto.conf"
PROD_URL_KEY=MT_TIMESCALE_DB_URL
# Every .env database URL is read with this: 2 user, 3 password, 4 host, 5 port, 6 db.
PG_URL_RE='^postgres(ql)?://([^:]+):([^@]+)@([^:/]+):([0-9]+)/([^?]+)'
TS_CONTROL="/usr/share/postgresql/$PG_VERSION/extension/timescaledb.control"
APP_ROLE=tick_app
MIGRATE_ROLE=tick_migrate
DATABASES=(trading_tick trading_tick_proof)
ROLES_SQL="$SCRIPT_DIR/provision_tick_roles.sql"
ENV_FILE="$CHECKOUT/.env"
ENV_OWNER=manta
NOTES_DIR="$CHECKOUT/project-documents/user/notes"
LOG_GLOB="*-226-tick-provision-*.log"
PASSWORD_HEX_BYTES=24
# key → role|database. The proof URLs are MT_PROOF_226_*, never MT_TICK_*:
# the tick preflight refuses an MT_TICK_* key no setting reads.
declare -A URL_KEYS=(
  [MT_TICK_DB_URL]="$APP_ROLE|trading_tick"
  [MT_TICK_MAINTENANCE_URL]="$MIGRATE_ROLE|trading_tick"
  [MT_PROOF_226_DB_URL]="$APP_ROLE|trading_tick_proof"
  [MT_PROOF_226_MAINTENANCE_URL]="$MIGRATE_ROLE|trading_tick_proof"
)

CHECK=0
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  *) echo "usage: sudo $0 | $0 --check" >&2; exit 2 ;;
esac

NOT_OK=0
item() { echo "$*"; case "${1%% *}" in OK|APPLIED|WOULD|SKIP|INFO) ;; *) NOT_OK=$((NOT_OK + 1)) ;; esac; }
die() { echo "FAIL: $*"; exit 1; }
step() { echo; echo "==> $*"; }
as_postgres() { runuser -u "$PG_OWNER" -- "$@"; }
tick_psql() { as_postgres psql -X -q -At -v ON_ERROR_STOP=1 -p "$PORT" "$@"; }

if [ "$CHECK" -eq 0 ]; then
  [ "$(id -u)" -eq 0 ] || die "apply mode needs root: sudo $0 (or --check)"
  LOG="/var/log/manta-tick-provision-$(date -u +%Y%m%dT%H%M%SZ).log"
  exec 3>&1 4>&2 > >(tee -a "$LOG") 2>&1
  TEE_PID=$!
  echo "log: $LOG"
fi
ROOT=0; [ "$(id -u)" -eq 0 ] && ROOT=1

cluster_exists() { pg_lsclusters -h "$PG_VERSION" "$CLUSTER" >/dev/null 2>&1; }

# --- Pre-checks: refuse before anything changes -----------------------------
prechecks() {
  step "pre-checks"
  local holder free binary prod
  holder="$(pg_lsclusters -h | awk -v p="$PORT" '$3 == p {print $1 "/" $2}')"
  if [ -z "$holder" ] && ss -Hltn "sport = :$PORT" | grep -q .; then holder="a non-cluster process"; fi
  case "$holder" in
    "") item "OK port $PORT free" ;;
    "$PG_VERSION/$CLUSTER") item "OK port $PORT held by $holder" ;;
    *) die "port $PORT is taken by $holder" ;;
  esac
  free="$(df -B1 --output=avail "$DATA_VOLUME" | tail -1 | tr -d ' ')"
  if cluster_exists; then
    item "INFO $DATA_VOLUME free $((free / 1000 ** 3)) GB (cluster exists; floor applies only before creating it)"
  elif [ "$free" -ge "$MIN_FREE_BYTES" ]; then
    item "OK $DATA_VOLUME free $((free / 1000 ** 3)) GB >= $((MIN_FREE_BYTES / 1000 ** 3)) GB"
  else
    die "$DATA_VOLUME has $((free / 1000 ** 3)) GB free, below $((MIN_FREE_BYTES / 1000 ** 3)) GB"
  fi
  binary="$(sed -n "s/^default_version = '\(.*\)'/\1/p" "$TS_CONTROL")"
  prod="$(production_ts_version)" || die "cannot read production's TimescaleDB version"
  [ -n "$binary" ] && [ "$binary" = "$prod" ] \
    || die "TimescaleDB binaries '$binary' differ from production's '$prod'"
  item "OK timescaledb binaries $binary = production (port $PROD_PORT) $prod"
}

# Production's installed extension version, read with its own URL from .env
# (the password goes through PGPASSWORD, never the command line).
production_ts_version() {
  local url
  url="$(env_value "$ENV_FILE" "$PROD_URL_KEY")"
  [[ "$url" =~ $PG_URL_RE ]] || { echo "$PROD_URL_KEY in $ENV_FILE is not a user:password URL" >&2; return 1; }
  [ "${BASH_REMATCH[5]}" = "$PROD_PORT" ] || { echo "$PROD_URL_KEY port is not $PROD_PORT" >&2; return 1; }
  PGPASSWORD="${BASH_REMATCH[3]}" psql -X -At -v ON_ERROR_STOP=1 \
    -h "${BASH_REMATCH[4]}" -p "$PROD_PORT" -U "${BASH_REMATCH[2]}" -d "${BASH_REMATCH[6]}" \
    -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
}

# SHA-256 of every production configuration file this user can read; taken
# at start and end of an apply run, which fails if any changed (slice 226
# 3.6 / 14.2). Without root, the postgres-only files are listed as SKIP.
production_conf_sums() {
  local file
  for file in "$PROD_CONF_DIR"/*.conf "$PROD_CONF_DIR"/conf.d/*.conf "$PROD_AUTO_CONF"; do
    [ -e "$file" ] || continue
    if [ -r "$file" ]; then sha256sum "$file"; else echo "SKIP $file (needs root to read)"; fi
  done
}

# --- Cluster ------------------------------------------------------------------
ensure_cluster() {
  step "cluster $PG_VERSION/$CLUSTER"
  local seen
  seen="$(stat -c '%U %a' "$DATA_ROOT" 2>/dev/null || echo absent)"
  if [ "$seen" = "$PG_OWNER $PG_DIR_MODE" ]; then item "OK $DATA_ROOT $seen"
  elif [ "$CHECK" -eq 1 ]; then item "WOULD set $DATA_ROOT to $PG_OWNER $PG_DIR_MODE (seen: $seen)"
  else
    install -d -o "$PG_OWNER" -g "$PG_OWNER" -m "$PG_DIR_MODE" "$DATA_ROOT"
    item "APPLIED $DATA_ROOT $PG_OWNER $PG_DIR_MODE (was: $seen)"
  fi
  if cluster_exists; then item "OK cluster exists"
  elif [ "$CHECK" -eq 1 ]; then item "WOULD pg_createcluster $PG_VERSION $CLUSTER --port $PORT --datadir $DATA_DIR"
  else
    pg_createcluster "$PG_VERSION" "$CLUSTER" --port "$PORT" --datadir "$DATA_DIR"
    systemctl daemon-reload
    item "APPLIED pg_createcluster $PG_VERSION $CLUSTER"
  fi
  if systemctl is-active --quiet "$UNIT"; then item "OK $UNIT active"
  elif [ "$CHECK" -eq 1 ]; then item "WOULD start $UNIT"
  else systemctl start "$UNIT"; item "APPLIED start $UNIT"
  fi
}

# --- Settings: two passes, because timescaledb.* exists only once loaded ----
apply_settings() {
  local out rc=0
  out="$(PGCLUSTER="$PG_VERSION/$CLUSTER" "$LIB_DIR/pg_settings.sh" --as-user "$PG_OWNER" \
    --conf "$CONF_DIR/postgresql.conf" "$@")" || rc=$?
  echo "$out"
  if grep -q '^PENDING RESTART' <<< "$out"; then
    systemctl restart "$UNIT"
    item "APPLIED restart $UNIT (settings pending restart)"
    PGCLUSTER="$PG_VERSION/$CLUSTER" "$LIB_DIR/pg_settings.sh" --check --as-user "$PG_OWNER" \
      --conf "$CONF_DIR/postgresql.conf" "$@" || die "settings still drift after restart"
  elif [ "$rc" -ne 0 ]; then
    die "pg_settings.sh reported drift it could not apply"
  fi
}

ensure_settings() {
  step "settings"
  if [ "$CHECK" -eq 1 ]; then
    item "WOULD reconcile shared_preload_libraries, listen_addresses, memory block (restart $UNIT only if a setting changed)"
    return
  fi
  apply_settings --set shared_preload_libraries=timescaledb \
    --set listen_addresses="$LISTEN_ADDRESS" --set max_connections="$MAX_CONNECTIONS" \
    --set shared_buffers="$SHARED_BUFFERS_8KB" --set effective_cache_size="$EFFECTIVE_CACHE_SIZE_8KB" \
    --set work_mem="$WORK_MEM_KB" --set maintenance_work_mem="$MAINTENANCE_WORK_MEM_KB" \
    --set max_wal_size="$MAX_WAL_SIZE_MB"
  apply_settings --set timescaledb.max_background_workers="$TS_MAX_BACKGROUND_WORKERS"
}

# The address a client connecting to LISTEN_ADDRESS arrives from: the kernel
# gives loopback connections the route's source (127.0.0.1 on this host), not
# the address dialled. Found 2026-10-03: an entry for 127.0.1.1/32 matched no
# connection ("no pg_hba.conf entry for host 127.0.0.1").
client_source() {
  local src
  src="$(ip route get "$LISTEN_ADDRESS" | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
  [ -n "$src" ] || die "no source address for a connection to $LISTEN_ADDRESS"
  echo "$src"
}

hba_content() {
  local dbs roles src
  dbs="$(IFS=,; echo "${DATABASES[*]}")"; roles="$APP_ROLE,$MIGRATE_ROLE"
  src="$(client_source)"
  printf '%s\n' \
    "# Written by scripts/provision_tick_cluster.sh (slice 226 TD1); re-run it, do not edit." \
    "local all $PG_OWNER peer" \
    "host $dbs $roles $src/32 scram-sha-256"
}

ensure_hba() {
  step "pg_hba.conf"
  local file="$CONF_DIR/pg_hba.conf"
  if [ "$ROOT" -eq 0 ]; then item "SKIP $file (needs root to read)"; return; fi
  if [ -f "$file" ] && [ "$(cat "$file")" = "$(hba_content)" ]; then item "OK $file"; return; fi
  if [ "$CHECK" -eq 1 ]; then item "WOULD write $file"; return; fi
  hba_content > "$file.new" && chown "$PG_OWNER:$PG_OWNER" "$file.new" && chmod 640 "$file.new"
  mv "$file.new" "$file"
  tick_psql -d postgres -c "SELECT pg_reload_conf()" >/dev/null
  item "APPLIED $file (reloaded)"
}

# --- Databases, roles, credentials ---------------------------------------------
ensure_databases() {
  step "databases and roles"
  local db
  for db in "${DATABASES[@]}"; do
    if [ "$CHECK" -eq 1 ]; then item "WOULD apply $(basename "$ROLES_SQL") to $db"; continue; fi
    # Root opens the file (postgres cannot read under an operator's home).
    tick_psql -d postgres -v tick_db="$db" -f - < "$ROLES_SQL" >/dev/null
    item "APPLIED $(basename "$ROLES_SQL") to $db (idempotent grants)"
  done
}

# The password .env already holds for a role, or empty.
env_password() {
  local key spec url
  for key in "${!URL_KEYS[@]}"; do
    spec="${URL_KEYS[$key]}"; [ "${spec%%|*}" = "$1" ] || continue
    url="$(env_value "$ENV_FILE" "$key")"
    if [[ "$url" =~ $PG_URL_RE ]]; then echo "${BASH_REMATCH[3]}"; return; fi
  done
}

can_login() {
  PGPASSWORD="$2" psql -X -At -h "$LISTEN_ADDRESS" -p "$PORT" -U "$1" -d "${DATABASES[0]}" \
    -c "SELECT 1" >/dev/null 2>&1
}

declare -A PASSWORDS=()
ensure_passwords() {
  step "role passwords"
  local role pw
  for role in "$APP_ROLE" "$MIGRATE_ROLE"; do
    pw="$(env_password "$role")"
    if [ "$CHECK" -eq 1 ]; then
      if [ -n "$pw" ]; then item "WOULD keep the .env password for $role"
      else item "WOULD generate a password for $role"; fi
      continue
    fi
    [ -n "$pw" ] || pw="$(openssl rand -hex "$PASSWORD_HEX_BYTES")"
    PASSWORDS[$role]="$pw"
    if can_login "$role" "$pw"; then item "OK $role logs in with its .env password"; continue; fi
    # Through stdin, so the password is on no command line.
    printf "ALTER ROLE %s WITH PASSWORD '%s';\n" "$role" "$pw" | tick_psql -d postgres -f - >/dev/null
    can_login "$role" "$pw" || die "$role cannot log in after setting its password"
    item "APPLIED password for $role"
  done
}

ensure_env() {
  step ".env"
  local key spec role pairs=() missing=0 added
  for key in "${!URL_KEYS[@]}"; do
    spec="${URL_KEYS[$key]}"; role="${spec%%|*}"
    if [ -n "$(env_value "$ENV_FILE" "$key")" ]; then item "OK $key present"; continue; fi
    missing=$((missing + 1))
    [ "$CHECK" -eq 1 ] && { item "WOULD add $key"; continue; }
    pairs+=("$key=postgresql://$role:${PASSWORDS[$role]}@$HOST_NAME:$PORT/${spec#*|}")
  done
  if [ "$CHECK" -eq 1 ]; then ENV_ADDED="$missing"; return; fi
  added="$(env_add_keys "$ENV_FILE" "$ENV_OWNER" "${pairs[@]}")" || die "could not write $ENV_FILE"
  ENV_ADDED="$added"
  item "OK $ENV_FILE $(stat -c '%U %a' "$ENV_FILE"), $added keys added"
}

# --- Log: scan, then copy --------------------------------------------------------
scan_copied_logs() {
  step "credential scan of copied logs"
  local log found=0
  for log in "$NOTES_DIR"/$LOG_GLOB; do
    [ -e "$log" ] || continue
    found=1
    log_scrub "$log" || die "$log holds a credential"
    item "OK $log clean"
  done
  [ "$found" -eq 1 ] || item "INFO no copied log yet"
}

copy_log() {
  local copy stamp
  stamp="$(basename "$LOG" .log | sed 's/.*-//')"
  copy="$NOTES_DIR/$(date -u +%Y-%m-%d)-226-tick-provision-$stamp.log"
  # Close the tee so the log is complete before it is scanned and copied.
  exec 1>&3 2>&4 3>&- 4>&-
  wait "$TEE_PID"
  log_scrub "$LOG" "${PASSWORDS[@]}" || die "$LOG holds a credential; not copied"
  install -o "$ENV_OWNER" -g "$ENV_OWNER" -m 644 "$LOG" "$copy"
  echo "log copied to $copy"
}

step "production cluster configuration (before)"
PROD_SUMS_BEFORE="$(production_conf_sums)"
echo "$PROD_SUMS_BEFORE"
prechecks
ensure_cluster
ensure_settings
ensure_hba
ensure_databases
ensure_passwords
ENV_ADDED=0
ensure_env
scan_copied_logs
step "production cluster configuration (after)"
if [ "$(production_conf_sums)" = "$PROD_SUMS_BEFORE" ]; then
  item "OK $PROD_CONF_DIR and postgresql.auto.conf unchanged"
else
  item "DRIFT production configuration changed during this run"
fi
[ "$NOT_OK" -eq 0 ] || die "$NOT_OK item(s) not OK"
if [ "$CHECK" -eq 1 ]; then
  echo "CHECK: pre-checks pass; .env would gain $ENV_ADDED keys"
  exit 0
fi
pg_lsclusters
echo "PASS: tick cluster $PG_VERSION/$CLUSTER on $PORT; ${#DATABASES[@]} databases; .env updated ($ENV_ADDED keys added)"
copy_log
