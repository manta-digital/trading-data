#!/usr/bin/env bash
# install-production.sh — install the supervised production stack (slice 916).
#
# Creates the manta-trading service account, clones/updates the pinned
# checkout at /opt/manta-trading, builds its venv, installs the environment
# file skeleton, the systemd units, and the journald drop-in, then reloads
# systemd. ENABLES NOTHING — cutover is a separate, explicit
# `systemctl enable --now` (Group F of the slice tasks).
#
# Idempotent: every step is check-then-act. Recovery for any failure is
# "fix the cause, re-run the whole script."
set -euo pipefail

# Single source of truth for every name/path the script touches.
SERVICE_USER="manta-trading"
INSTALL_DIR="/opt/manta-trading"
REPO_URL="https://github.com/manta-digital/trading-data.git"
ENV_FILE="/etc/manta-trading.env"
UNIT_DIR="/etc/systemd/system"
JOURNALD_DROPIN_DIR="/etc/systemd/journald.conf.d"
UV_CACHE_DIR="/var/cache/manta-trading/uv"
# uv's managed-python dir follows HOME (~/.local/share/uv/python), which would
# land INSIDE the checkout and trip the dirty-tree guard on the next run —
# keep all uv state out of the working tree.
UV_PYTHON_INSTALL_DIR="/var/cache/manta-trading/python"
NOLOGIN_SHELL="/usr/sbin/nologin"
# Unit files installed from the pinned checkout (not the checkout this script
# runs from) so what lands in /etc matches the ref that was chosen.
UNIT_SRC_DIR="${INSTALL_DIR}/deploy/systemd"
ENV_EXAMPLE="${INSTALL_DIR}/deploy/manta-trading.env.example"
UNITS=(
  manta-acquisition.slice
  mt-daily-pass.service
  mt-daily-pass.timer
  mt-minute-pass.service
  mt-minute-pass.timer
  mt-kalshi-pass.service
  mt-kalshi-pass.timer
  mt-health.service
  mt-health.timer
  mt-serve.service
)

usage() {
  cat <<EOF
Usage: sudo $0 --ref <tag-or-sha>

Installs the supervised production stack at ${INSTALL_DIR}, pinned to the
given git ref. There is deliberately no default ref: /opt must track
something somebody chose.

Enables nothing. Cutover is a separate, explicit step.
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

# Run a command as the service account with a sane environment.
as_service_user() {
  runuser -u "${SERVICE_USER}" -- env HOME="${INSTALL_DIR}" UV_CACHE_DIR="${UV_CACHE_DIR}" UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR}" "$@"
}

# --- Argument handling: --ref is required, no default -----------------------
REF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      [[ $# -ge 2 ]] || die "--ref requires a value (tag or commit SHA)"
      REF="$2"; shift 2 ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      usage >&2; die "unknown argument: $1" ;;
  esac
done
[[ -n "${REF}" ]] || { usage >&2; die "missing required argument: --ref <tag-or-sha>"; }

[[ "$(id -u)" -eq 0 ]] || die "this script must run as root (sudo $0 --ref ${REF})"

# --- Step 1: service account ------------------------------------------------
step "Step 1/6: service account '${SERVICE_USER}'"
if entry="$(getent passwd "${SERVICE_USER}")"; then
  actual_home="$(cut -d: -f6 <<<"${entry}")"
  actual_shell="$(cut -d: -f7 <<<"${entry}")"
  if [[ "${actual_home}" == "${INSTALL_DIR}" && "${actual_shell}" == "${NOLOGIN_SHELL}" ]]; then
    echo "exists with expected home and shell — skipping creation"
  else
    die "account '${SERVICE_USER}' exists with unexpected shape \
(home='${actual_home}' shell='${actual_shell}', expected home='${INSTALL_DIR}' \
shell='${NOLOGIN_SHELL}'). Adopting a colliding or hand-made account is a PM \
decision — resolve it, then re-run."
  fi
else
  # --no-create-home: the checkout step owns that directory's lifecycle.
  useradd --system --shell "${NOLOGIN_SHELL}" --home-dir "${INSTALL_DIR}" \
    --no-create-home --user-group "${SERVICE_USER}"
  echo "created system account '${SERVICE_USER}' (${NOLOGIN_SHELL}, home ${INSTALL_DIR})"
fi

# uv cache lives outside the checkout so it never dirties the working tree.
mkdir -p "${UV_CACHE_DIR}" "${UV_PYTHON_INSTALL_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$(dirname "${UV_CACHE_DIR}")"

# --- Step 2: checkout at the pinned ref -------------------------------------
step "Step 2/6: checkout ${INSTALL_DIR} at ref '${REF}'"
if [[ ! -e "${INSTALL_DIR}/.git" ]]; then
  if [[ -e "${INSTALL_DIR}" ]]; then
    # A died-mid-way clone leaves either nothing or a partial tree with no
    # usable .git — safe to discard; guarded by the .git-absence check above.
    echo "removing leftover ${INSTALL_DIR} (no .git — unusable partial tree)"
    rm -rf "${INSTALL_DIR}"
  fi
  mkdir -p "${INSTALL_DIR}"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
  echo "fresh clone from ${REPO_URL}"
  as_service_user git clone "${REPO_URL}" "${INSTALL_DIR}"
else
  actual_origin="$(as_service_user git -C "${INSTALL_DIR}" remote get-url origin)"
  [[ "${actual_origin}" == "${REPO_URL}" ]] || die "existing checkout's origin \
is '${actual_origin}', expected '${REPO_URL}' — aborting untouched"
  dirty="$(as_service_user git -C "${INSTALL_DIR}" status --porcelain)"
  [[ -z "${dirty}" ]] || die "existing checkout has local modifications — \
aborting untouched. Production must track a pinned ref; resolve by hand:
${dirty}"
  echo "existing clean checkout — fetching"
  as_service_user git -C "${INSTALL_DIR}" fetch --tags origin
fi

target_commit="$(as_service_user git -C "${INSTALL_DIR}" rev-parse --verify --quiet "${REF}^{commit}")" \
  || die "ref '${REF}' not found in the repository"
current_commit="$(as_service_user git -C "${INSTALL_DIR}" rev-parse HEAD)"
if [[ "${current_commit}" == "${target_commit}" ]]; then
  echo "already at ${REF} (${target_commit}) — nothing to check out"
else
  echo "checking out ${REF} (${target_commit}; was ${current_commit})"
  as_service_user git -C "${INSTALL_DIR}" checkout --detach "${target_commit}"
fi

# --- Step 3: virtualenv -----------------------------------------------------
step "Step 3/6: venv via uv sync --frozen"
# uv is deploy-time only; resolve it from root's PATH and hand the absolute
# path to the service account. Do NOT delete .venv on failure — uv sync is
# resumable and reconciles a partial venv on the next run.
UV_BIN="$(command -v uv)" || die "uv not found on PATH — install uv, then re-run"
(cd "${INSTALL_DIR}" && as_service_user "${UV_BIN}" sync --frozen)
# Every ExecStart= depends on this exact path — gate the rest of the script.
"${INSTALL_DIR}/.venv/bin/mt" --version >/dev/null \
  || die "${INSTALL_DIR}/.venv/bin/mt --version failed — the venv is not usable. \
If re-running does not fix it, escalate: rm -rf ${INSTALL_DIR}/.venv and re-run (runbook)."
echo "venv OK: $("${INSTALL_DIR}/.venv/bin/mt" --version)"

# --- Step 4: environment file -----------------------------------------------
step "Step 4/6: environment file ${ENV_FILE}"
if [[ -e "${ENV_FILE}" ]]; then
  # Never overwrite, never merge — the PM's filled-in credentials must
  # survive every re-run.
  echo "exists — leaving contents untouched (sha256: $(sha256sum "${ENV_FILE}" | cut -d' ' -f1))"
else
  install -m 0640 -o root -g "${SERVICE_USER}" "${ENV_EXAMPLE}" "${ENV_FILE}"
  echo "installed skeleton from ${ENV_EXAMPLE} — fill it before running a pass"
fi
# Re-assert ownership and mode every run, in case they drifted.
chown "root:${SERVICE_USER}" "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

# --- Step 5: unit files and journald drop-in --------------------------------
step "Step 5/6: systemd units and journald drop-in"
# Copied unconditionally: the repo is the source of truth and these files
# hold no host-local state. A partial failure leaves units present but
# unreferenced (inert — nothing is enabled); the re-run completes the set.
for unit in "${UNITS[@]}"; do
  install -m 0644 -o root -g root "${UNIT_SRC_DIR}/${unit}" "${UNIT_DIR}/${unit}"
  echo "installed ${UNIT_DIR}/${unit}"
done
mkdir -p "${JOURNALD_DROPIN_DIR}"
install -m 0644 -o root -g root "${UNIT_SRC_DIR}/journald-manta-trading.conf" \
  "${JOURNALD_DROPIN_DIR}/manta-trading.conf"
echo "installed ${JOURNALD_DROPIN_DIR}/manta-trading.conf"
# Operator front-end: one command to start a pass with live output, or check
# on a running one (mt-run status / mt-run follow). Copied unconditionally,
# same reasoning as the units.
install -m 0755 -o root -g root "${INSTALL_DIR}/deploy/mt-run" /usr/local/bin/mt-run
echo "installed /usr/local/bin/mt-run"

# --- Step 6: reload ---------------------------------------------------------
step "Step 6/6: systemctl daemon-reload"
# Unconditional and LAST, so a half-copied unit set is never loaded and
# "files installed but reload skipped" is unreachable.
systemctl daemon-reload
systemctl restart systemd-journald   # journald caps take effect
echo "reloaded systemd; restarted journald"

# --- Closing message ---------------------------------------------------------
echo
if systemctl is-enabled --quiet mt-daily-pass.timer 2>/dev/null; then
  echo "Install complete. Production was ALREADY CUT OVER and remains supervised —"
  echo "this run only updated files. Enabled units stay enabled."
else
  echo "Install complete. NOTHING HAS BEEN ENABLED — no timer or service will"
  echo "start on its own. Production is unchanged."
fi
echo
echo "Unit state (pass services show 'static' by design — no [Install]; the"
echo "timers are what get enabled, later):"
systemctl list-unit-files 'mt-*' 'manta-acquisition.*' || true
if ! systemctl is-enabled --quiet mt-daily-pass.timer 2>/dev/null; then
  echo
  echo "Next steps:"
  echo "  1. Fill the environment file:  sudoedit ${ENV_FILE}"
  echo "  2. Run one pass by hand:       sudo mt-run daily   (live output; Ctrl-C detaches)"
  echo
  echo "Cutover (later, explicit): sudo systemctl enable --now mt-daily-pass.timer mt-minute-pass.timer mt-kalshi-pass.timer mt-health.timer mt-serve.service"
fi
