---
docType: slice-design
slice: 126
parent: user/architecture/120-slices.data-acquisition.md
project: trading
dateCreated: 20260425
dateUpdated: 20260515
status: complete
completedOn: 20260515
completedNote: Deliverables (systemd unit templates, journald drop-in, install runbook) shipped as part of slice 128. Slice 126 was deferred when AV proved unsuitable, then superseded and absorbed into 128 for the EODHD reality.
---

# Slice 126 — Production Deployment (systemd, single-host)

## Status: Complete (absorbed into slice 128)

This slice was deferred on 2026-04-26 when AlphaVantage's 2-year intraday window made
a meaningful production deployment impossible. Slice 128 absorbed its scope entirely:

- `deploy/systemd/mt-daily-daemon.service.tmpl` — shipped in slice 128
- `deploy/systemd/mt-minute-daemon.service.tmpl` — shipped in slice 128
- `deploy/systemd/journald-manta-trading.conf` — shipped in slice 128 (filename differs from original plan: `journald-manta-trading.conf` rather than `manta-trading-journald.conf`)
- `project-documents/user/runbooks/production-deploy.md` — shipped in slice 128, EODHD-aware with two HARD GATES

The tasks file for this slice has been removed — all items were either completed via
slice 128 or made irrelevant by the architectural shift to a single unified daemon
(`mt data daemon run`) rather than separate daily/minute processes.

## Note on unit templates

The `ExecStart` lines in the shipped templates originally referenced `mt data daily daemon`
and `mt data minute daemon` (pre-146 CLI shape). Both were corrected to the actual
unified command (`mt data daemon run`) when the templates were last updated.
