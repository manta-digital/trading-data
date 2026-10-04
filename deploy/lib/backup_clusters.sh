#!/usr/bin/env bash
# backup_clusters.sh — read deploy/backup-clusters.conf (slice 227, TD2).
#
# Source it, then: backup_clusters_load <file>. On success it fills parallel
# arrays, one element per row in file order:
#   BC_CLUSTER BC_URL_KEY BC_ROOT BC_REMOTE BC_REPL_HOST BC_METADATA_CRON
#   BC_WEEKLY_CRON
# `-` is kept as `-` (callers test for it). On any malformed row it prints an
# error naming the line to stderr and returns 1; the arrays are then partial
# and must not be used. scripts/backup_clusters.py applies the same rules.
#
# backup_clusters_print <file> loads and prints one tab-separated row per
# cluster (the parity tests read it). Sourcing defines functions only.

# 2 single fields + 2 paths + host + 2 x 5 cron fields.
BC_TOKENS_PER_ROW=15
BC_URL_KEY_RE='^MT_[A-Z0-9_]+$'
BC_ROOT_PREFIX=/data/

backup_clusters_load() {
  local file=$1 line n=0 i
  local -a f
  BC_CLUSTER=(); BC_URL_KEY=(); BC_ROOT=(); BC_REMOTE=(); BC_REPL_HOST=()
  BC_METADATA_CRON=(); BC_WEEKLY_CRON=()
  [ -r "$file" ] || { echo "backup-clusters: cannot read $file" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%%#*}
    read -r -a f <<< "$line"
    [ "${#f[@]}" -gt 0 ] || continue
    if [ "${#f[@]}" -ne "$BC_TOKENS_PER_ROW" ]; then
      echo "backup-clusters: $file line $n: expected $BC_TOKENS_PER_ROW fields, got ${#f[@]}" >&2; return 1
    fi
    for i in "${!BC_CLUSTER[@]}"; do
      if [ "${BC_CLUSTER[$i]}" = "${f[0]}" ]; then
        echo "backup-clusters: $file line $n: duplicate cluster ${f[0]}" >&2; return 1
      fi
    done
    if ! [[ ${f[1]} =~ $BC_URL_KEY_RE ]]; then
      echo "backup-clusters: $file line $n: url_key ${f[1]} does not match $BC_URL_KEY_RE" >&2; return 1
    fi
    case "${f[2]}" in
      "$BC_ROOT_PREFIX"?*) ;;
      *) echo "backup-clusters: $file line $n: backup_root ${f[2]} is not an absolute path under $BC_ROOT_PREFIX" >&2; return 1 ;;
    esac
    BC_CLUSTER+=("${f[0]}"); BC_URL_KEY+=("${f[1]}"); BC_ROOT+=("${f[2]}")
    BC_REMOTE+=("${f[3]}"); BC_REPL_HOST+=("${f[4]}")
    BC_METADATA_CRON+=("${f[*]:5:5}"); BC_WEEKLY_CRON+=("${f[*]:10:5}")
  done < "$file"
  [ "${#BC_CLUSTER[@]}" -gt 0 ] || { echo "backup-clusters: $file holds no rows" >&2; return 1; }
}

backup_clusters_print() {
  local i
  backup_clusters_load "$1" || return 1
  for i in "${!BC_CLUSTER[@]}"; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${BC_CLUSTER[$i]}" "${BC_URL_KEY[$i]}" \
      "${BC_ROOT[$i]}" "${BC_REMOTE[$i]}" "${BC_REPL_HOST[$i]}" \
      "${BC_METADATA_CRON[$i]}" "${BC_WEEKLY_CRON[$i]}"
  done
}
