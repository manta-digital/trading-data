#!/usr/bin/env bash
# pg_settings.sh — PostgreSQL settings via ALTER SYSTEM, check-then-act
# (slice 920, D1 step 4).
#
# Compares pg_settings.setting for each --set name=value against the value,
# applies `ALTER SYSTEM SET` only on drift, reloads once, and then reports
# what still needs a restart (never restarts) and whether each setting is
# now served from postgresql.auto.conf (DRIFT <name> source otherwise). With
# --forbid-conf-line, an uncommented line for that name in postgresql.conf
# or its conf.d is DRIFT: the hand edit the runbook says to remove.
#
# Usage:
#   pg_settings.sh [--check] --conf <postgresql.conf> --set name=value ... \
#       [--forbid-conf-line <name> ...]
#
# Runs `psql` from PATH against the cluster the environment selects (the
# caller sets PGCLUSTER and runs this as postgres). Output lines:
#   OK <name> | DRIFT <name> <expected> <actual> | APPLIED <name> |
#   PENDING RESTART <name> | DRIFT <name> source <expected> <actual> |
#   DRIFT <name> conf-line <file>:<line> | MISSING pg-settings <reason>
# Exit 1 if any line is not OK/APPLIED.
set -euo pipefail

AUTO_CONF=postgresql.auto.conf
CONF_D=conf.d

usage() {
  echo "usage: $0 [--check] --conf <postgresql.conf> --set name=value [--set ...] [--forbid-conf-line <name> ...]" >&2
}
die() { echo "error: $*" >&2; exit "${2:-1}"; }

CHECK=0; CONF=""
declare -A EXPECTED=()
NAMES=(); FORBIDDEN=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --conf)  CONF="${2:-}"; shift 2 ;;
    --set)
      [ -n "${2:-}" ] && [[ "$2" == *=* ]] || { usage; die "--set needs name=value" 2; }
      NAMES+=("${2%%=*}"); EXPECTED["${2%%=*}"]="${2#*=}"; shift 2 ;;
    --forbid-conf-line) [ -n "${2:-}" ] || { usage; die "--forbid-conf-line needs a name" 2; }
      FORBIDDEN+=("$2"); shift 2 ;;
    *) usage; die "unknown argument: $1" 2 ;;
  esac
done
[ -n "$CONF" ] || { usage; die "--conf is required" 2; }
[ "${#NAMES[@]}" -gt 0 ] || { usage; die "at least one --set is required" 2; }

NOT_OK=0
report() { echo "$*"; case "${1%% *}" in OK|APPLIED|INFO) ;; *) NOT_OK=$((NOT_OK + 1)) ;; esac; }

sql_list() { local out=""; for n in "${NAMES[@]}"; do out+="${out:+,}'$n'"; done; echo "$out"; }

# name|setting|sourcefile|pending_restart per line.
declare -A SETTING=() SOURCE=() PENDING=()
query_settings() {
  local rows
  rows=$(psql -X -At -F'|' -v ON_ERROR_STOP=1 -d postgres -c "
SELECT name, setting, COALESCE(sourcefile, ''), pending_restart
  FROM pg_settings WHERE name IN ($(sql_list)) ORDER BY name;" 2>&1) \
    || { report "MISSING pg-settings psql failed: $(paste -sd ' ' <<< "$rows")"; return 1; }
  while IFS='|' read -r name setting source pending; do
    [ -n "$name" ] || continue
    SETTING["$name"]="$setting"; SOURCE["$name"]="$source"; PENDING["$name"]="$pending"
  done <<< "$rows"
}

query_settings || exit 1

DRIFTED=()
compare_settings() {
  DRIFTED=()
  for n in "${NAMES[@]}"; do
    if [ -z "${SETTING[$n]+x}" ]; then
      report "MISSING $n not in pg_settings"
    elif [ "${SETTING[$n]}" = "${EXPECTED[$n]}" ]; then
      report "OK $n"
    else
      report "DRIFT $n '${EXPECTED[$n]}' '${SETTING[$n]}'"
      DRIFTED+=("$n")
    fi
  done
}
compare_settings

if [ "$CHECK" -eq 0 ] && [ "${#DRIFTED[@]}" -gt 0 ]; then
  STATEMENTS=()
  for n in "${DRIFTED[@]}"; do
    v="${EXPECTED[$n]//\'/\'\'}"
    STATEMENTS+=(-c "ALTER SYSTEM SET $n = '$v';")
  done
  psql -X -q -v ON_ERROR_STOP=1 -d postgres "${STATEMENTS[@]}" -c "SELECT pg_reload_conf();" >/dev/null
  for n in "${DRIFTED[@]}"; do echo "APPLIED $n"; done
  # Re-report from a fresh read: the tally restarts with what is true now.
  NOT_OK=0
  query_settings || exit 1
  compare_settings
fi

for n in "${NAMES[@]}"; do
  [ -n "${SETTING[$n]+x}" ] || continue
  [ "${PENDING[$n]}" != "t" ] || report "PENDING RESTART $n"
  case "${SOURCE[$n]}" in
    */$AUTO_CONF) ;;
    *) report "DRIFT $n source $AUTO_CONF '${SOURCE[$n]:-(none)}'" ;;
  esac
done

# postgresql.conf includes conf.d; a hand-set line in either would shadow
# nothing (auto.conf wins) but leaves the file lying about the effective value.
for n in "${FORBIDDEN[@]}"; do
  hits=$(grep -nE "^[[:space:]]*${n}[[:space:]]*=" "$CONF" "$(dirname "$CONF")/$CONF_D"/*.conf 2>/dev/null | cut -d: -f1,2 | paste -sd ' ' || true)
  if [ -n "$hits" ]; then
    report "DRIFT $n conf-line $hits"
  else
    report "OK $n conf-line"
  fi
done

[ "$NOT_OK" -eq 0 ]
