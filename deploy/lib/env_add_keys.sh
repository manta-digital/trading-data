#!/usr/bin/env bash
# env_add_keys.sh — add absent KEY=value lines to an env file, atomically
# (slice 226, TD1).
#
# Source it, then: env_add_keys <file> <owner> KEY=value [KEY=value ...]
# A key already present (anchored `^KEY=`) is left exactly as it is; only
# absent keys are appended. The new content goes to a mode-0600 temporary
# file in the same directory, is chowned to <owner>, then `mv`s over <file>,
# so a failure part-way leaves the old file whole. The mode and owner are
# enforced even when no key is added. Prints the number of keys added on
# stdout; values are never printed.
env_add_keys() {
  local file="$1" owner="$2"; shift 2
  local dir tmp added=0 pair key
  dir="$(dirname "$file")"
  tmp="$(umask 077 && mktemp "$dir/.env.XXXXXX")" || return 1
  # Removes the temporary file on any failure; a no-op after the mv.
  # shellcheck disable=SC2064  # expand $tmp now: it is local to this call
  trap "rm -f '$tmp'; trap - RETURN" RETURN
  if [ -f "$file" ]; then
    cat "$file" > "$tmp" || return 1
    # A file not ending in a newline would glue the first new key onto its
    # last line.
    if [ -s "$tmp" ] && [ -n "$(tail -c1 "$tmp")" ]; then echo >> "$tmp"; fi
  fi
  for pair in "$@"; do
    key="${pair%%=*}"
    if ! grep -q "^${key}=" "$tmp"; then
      printf '%s\n' "$pair" >> "$tmp" || return 1
      added=$((added + 1))
    fi
  done
  chmod 600 "$tmp" && chown "$owner:" "$tmp" && mv "$tmp" "$file" || return 1
  echo "$added"
}
