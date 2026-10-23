#!/usr/bin/env bash
# env_add_keys.sh — edit an env file's KEY=value lines, atomically
# (slice 226 TD1; slice 924 D4).
#
# Source it, then:
#   env_add_keys <file> <owner> KEY=value [KEY=value ...]
#     A key already present (anchored `^KEY=`) is left exactly as it is; only
#     absent keys are appended. Prints the number of keys added.
#   env_set_key <file> <owner> KEY=value
#     Replaces every `^KEY=` line with KEY=value in place, or appends it if
#     absent. Prints `unchanged` when the key already held exactly that value
#     (the rewrite may still add a missing final newline), else `set`.
#   backup_roots_env_set <env-file> <owner> <group> <clusters.conf>
#     Sets MT_BACKUP_ROOTS to the cluster table's backup roots, then leaves
#     the file <owner>:<group> mode 0640. Prints `set` or `unchanged`.
#     Returns 2 when the table is unreadable or malformed (its error on
#     stderr, env file not opened), 1 when the write fails.
#
# Both editors share one write: the new content goes to a mode-0600 temporary
# file in the same directory, is chowned to <owner>, then `mv`s over <file>,
# so a failure part-way leaves the old file whole. The mode and owner are
# enforced even when nothing changes. Values are never printed.

# Joins the roots in MT_BACKUP_ROOTS. Must match BACKUP_ROOTS_SEPARATOR in
# src/manta_trading/constants.py; a test parses this output with the Python
# reader to hold the two together.
ENV_BACKUP_ROOTS_SEPARATOR=,
ENV_BACKUP_ROOTS_KEY=MT_BACKUP_ROOTS

# _env_commit <file> <owner> <editor> [args...]: run `<editor> <file> <tmp>
# [args...]`, which writes the whole new content to <tmp> and prints a status
# word, then move <tmp> over <file> and print that word.
_env_commit() {
  local file="$1" owner="$2" editor="$3"; shift 3
  local dir tmp status
  dir="$(dirname "$file")"
  tmp="$(umask 077 && mktemp "$dir/.env.XXXXXX")" || return 1
  # Removes the temporary file on any failure; a no-op after the mv.
  # shellcheck disable=SC2064  # expand $tmp now: it is local to this call
  trap "rm -f '$tmp'; trap - RETURN" RETURN
  status="$("$editor" "$file" "$tmp" "$@")" || return 1
  chmod 600 "$tmp" && chown "$owner:" "$tmp" && mv "$tmp" "$file" || return 1
  echo "$status"
}

# _env_copy <file> <tmp>: the current content, ending in a newline. A file not
# ending in one would glue the first new key onto its last line.
_env_copy() {
  [ -f "$1" ] || return 0
  cat "$1" > "$2" || return 1
  if [ -s "$2" ] && [ -n "$(tail -c1 "$2")" ]; then echo >> "$2"; fi
}

_env_append_absent() {
  local file="$1" tmp="$2" added=0 pair key; shift 2
  _env_copy "$file" "$tmp" || return 1
  for pair in "$@"; do
    key="${pair%%=*}"
    if ! grep -q "^${key}=" "$tmp"; then
      printf '%s\n' "$pair" >> "$tmp" || return 1
      added=$((added + 1))
    fi
  done
  echo "$added"
}

_env_replace_key() {
  local file="$1" tmp="$2" pair="$3" key line found=0 differs=0
  key="${pair%%=*}"
  # Rebuilt line by line, never with sed: the value is data and may hold
  # `&`, `\`, `/` or `|`. Input `A=1\nK=old\n` with K=new → `A=1\nK=new\n`.
  if [ -f "$file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      if [[ $line == "$key="* ]]; then
        found=1
        [ "$line" = "$pair" ] || differs=1
        line="$pair"
      fi
      printf '%s\n' "$line" >> "$tmp" || return 1
    done < "$file"
  fi
  if [ "$found" -eq 0 ]; then
    printf '%s\n' "$pair" >> "$tmp" || return 1
    differs=1
  fi
  if [ "$differs" -eq 0 ]; then echo unchanged; else echo set; fi
}

env_add_keys() {
  local file="$1" owner="$2"; shift 2
  _env_commit "$file" "$owner" _env_append_absent "$@"
}

env_set_key() {
  _env_commit "$1" "$2" _env_replace_key "$3"
}

backup_roots_env_set() {
  local env_file="$1" owner="$2" group="$3" conf="$4" joined status
  # shellcheck source=SCRIPTDIR/backup_clusters.sh
  . "$(dirname "${BASH_SOURCE[0]}")/backup_clusters.sh" || return 1
  # The table is validated before the env file is opened for writing.
  backup_clusters_load "$conf" || return 2
  joined="$(IFS="$ENV_BACKUP_ROOTS_SEPARATOR"; echo "${BC_ROOT[*]}")"
  status="$(env_set_key "$env_file" "$owner" "$ENV_BACKUP_ROOTS_KEY=$joined")" || return 1
  chown "$owner:$group" "$env_file" && chmod 0640 "$env_file" || return 1
  echo "$status"
}
