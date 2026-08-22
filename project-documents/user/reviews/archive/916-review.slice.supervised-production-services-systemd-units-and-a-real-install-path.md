---
docType: review
layer: project
reviewType: slice
slice: supervised-production-services-systemd-units-and-a-real-install-path
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md
aiModel: z-ai/glm-5.2
status: complete
dateCreated: 20260822
dateUpdated: 20260822
reviewedSha: 24ce883bcaeebbcffa8aa35b4099cc4a47b32ae9
findings:
  - id: F001
    severity: pass
    category: configuration
    summary: "Configuration approach aligns with architecture's pydantic-settings pattern"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#architecture"
  - id: F002
    severity: pass
    category: error-handling
    summary: "Runtime failure modes for supervised paths are well-enumerated"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#architecture"
  - id: F003
    severity: concern
    category: error-handling
    summary: "Install script failure recovery is implicit, not per-step enumerated"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#implementation-details"
  - id: F004
    severity: pass
    category: scope
    summary: "Scope boundaries respect maintenance-band constraints"
    location: "916-slice.supervised-production-services-systemd-units-and-a-real-install-path.md#technical-scope"
  - id: F005
    severity: note
    category: nfr
    summary: "No NFRs in parent architecture require restatement"
    location: "900-arch.foundation-cleanup.md"
---

# Review: slice — slice 916

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.2

## Findings

### [PASS] Configuration approach aligns with architecture's pydantic-settings pattern

The slice's environment-file design correctly implements the architecture's centralized configuration principle. Services receive `MT_*` variables via systemd's `EnvironmentFile=/etc/manta-trading.env`, which populates the process environment that pydantic-settings reads. No `.env` file is placed in the working directory, ensuring a single source of truth. Missing variables fail explicitly per the architecture's "Explicit failure" principle and the project rule against silent fallbacks. The deliberate exclusion of `MT_TIMESCALE_MAINTENANCE_URL` from the service environment honors the credential separation established in slice 913, keeping DDL credentials out of service-runtime scope.

### [PASS] Runtime failure modes for supervised paths are well-enumerated

The slice enumerates and addresses failure modes for each new supervision path:
- **Crash of `mt-serve`**: `Restart=on-failure` with `RestartSec=10s`, `StartLimitBurst=5`/`StartLimitIntervalSec=300s`.
- **Reboot**: `Persistent=true` on timers fires missed schedules; `mt-serve.service` has `WantedBy=multi-user.target`.
- **Overlong pass**: systemd won't start a service whose previous activation is still running, so the next timer firing is absorbed.
- **Failed oneshot pass**: no `Restart=` — recovery is the next timer firing, which is safe because pass semantics make re-runs cheap (no provider calls on a drained scope).
- **Two installs coexisting**: journal `_SYSTEMD_UNIT` identity distinguishes supervised from manual runs; the install script pins a ref so `/opt` never tracks a moving branch.

These are concrete handling strategies, not "TBD" or implicit.

### [CONCERN] Install script failure recovery is implicit, not per-step enumerated

The `deploy/install-production.sh` script is a new I/O path that performs network operations (`git clone`, `uv sync`) and privileged filesystem changes (account creation, unit file installation, `daemon-reload`). The doc states the script is "idempotent (safe to re-run) and refuses to proceed if `/opt/manta-trading` exists with local modifications," but does not enumerate the failure-recovery behavior for each step:

- What state is the system in if `git clone` succeeds but `uv sync` fails mid-way? Is a partial `.venv` cleaned up on re-run, or does the idempotency check's "local modifications" guard prevent a clean retry?
- If unit files are installed but `daemon-reload` hasn't run, does the script detect and re-run that step?
- If the `manta-trading` account already exists (from a prior partial run), does account creation fail or skip gracefully?

The "enables nothing" design mitigates the blast radius (the system stays inert until the explicit cutover step), but the per-step failure handling strategy is implicit rather than explicitly enumerated. For a production install script that is PM-executed and is the sole deployment artifact, each failure point should state its recovery strategy concretely.

### [PASS] Scope boundaries respect maintenance-band constraints

The architecture's maintenance-band extension allows 900-999 slices to touch any layer but constrains them to "corrective, not additive" work. This slice fills three explicitly-documented gaps ("Not yet documented" items in the runbook, a "Future target" section) — making real what was already specified as needed but unbuilt. The only code change is correcting `serve.py` help text that references a deprecated slice (155). The slice explicitly excludes: backup cron migration, rclone mount, automatic migrations, `mt update` changes, test/production separation, and CI. The scope is well-bounded and does not smuggle in feature work under the maintenance label.

### [NOTE] No NFRs in parent architecture require restatement

The architecture document does not state any non-functional requirements (latency, throughput, availability targets) that this slice's paths would need to restate. The journald caps (2 GiB / 200 MiB) are operational disk-management choices, not NFRs derived from the architecture. No action required.
