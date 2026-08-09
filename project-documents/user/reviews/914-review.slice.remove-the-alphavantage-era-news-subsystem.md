---
docType: review
layer: project
reviewType: slice
slice: remove-the-alphavantage-era-news-subsystem
project: trading-data
verdict: PASS
sourceDocument: project-documents/user/slices/914-slice.remove-the-alphavantage-era-news-subsystem.md
aiModel: minimax/minimax-m3
status: complete
dateCreated: 20260808
dateUpdated: 20260808
reviewedSha: 2c8ea3b3c2efdcb28d958e9752fbc1cb0e4fdc13
findings:
  - id: F001
    severity: pass
    category: uncategorized
    summary: "Alignment with \"clean codebase\" and deprecated-code-removal goals"
    location: 900-arch.foundation-cleanup.md#design-goals
  - id: F002
    severity: pass
    category: uncategorized
    summary: "Maintenance band authorization and \"corrective, not additive\" compliance"
    location: 900-arch.foundation-cleanup.md#overview
  - id: F003
    severity: pass
    category: uncategorized
    summary: "Dependency hygiene consistent with \"Minimal new dependencies\""
    location: 900-arch.foundation-cleanup.md#architectural-principles
  - id: F004
    severity: pass
    category: uncategorized
    summary: "Verification strategy addresses deletion failure modes"
    location: 914-slice.remove-the-alphavantage-era-news-subsystem.md#verification-walkthrough
  - id: F005
    severity: pass
    category: uncategorized
    summary: "Consumer/dependency analysis is thorough and falsifiable"
    location: 914-slice.remove-the-alphavantage-era-news-subsystem.md#consumers-requiring-updates
  - id: F006
    severity: note
    category: uncategorized
    summary: "Scope of removal exceeds the architecture's explicit enumeration but is consistent with its broader principles"
    location: 914-slice.remove-the-alphavantage-era-news-subsystem.md#technical-scope
  - id: F007
    severity: note
    category: uncategorized
    summary: "Chromadb and stale description deferred as follow-ups"
    location: 914-slice.remove-the-alphavantage-era-news-subsystem.md#technical-scope
  - id: F008
    severity: note
    category: uncategorized
    summary: "No NFR restatement required for this slice type"
    location: 914-slice.remove-the-alphavantage-era-news-subsystem.md
---

# Review: slice — slice 914

**Verdict:** PASS
**Model:** minimax/minimax-m3

## Findings

### [PASS] Alignment with "clean codebase" and deprecated-code-removal goals

The architecture's "Clean codebase" design goal ("Remove deprecated code paths, establish the src layout as canonical, and ensure the package is publishable to PyPI") and the Envisioned State's "Old CLI entry points (ohlc.py, news.py) removed" line both directly support this slice. The news subsystem the slice removes contains the `news.py` entry point named in the architecture and additional dead stub code. The removal is consistent with the architecture's stated outcome.

### [PASS] Maintenance band authorization and "corrective, not additive" compliance

The architecture's 2026-08-03 scope extension explicitly authorizes slices in 900-999 to touch any layer, including acquisition and serving modules, provided the work is "corrective, not additive" and "honors the originating initiative's contracts." The slice removes a non-functional stub (raises unconditionally) and does not introduce new capability. The PM's Value-section rationale — that AV-era schema assumptions are a liability rather than a head start for any future news provider — is the kind of judgment the maintenance band framework invites.

### [PASS] Dependency hygiene consistent with "Minimal new dependencies"

The slice removes `pymongo` and `motor` from `pyproject.toml` and regenerates `uv.lock`. While the architecture's "Minimal new dependencies" principle is stated for new additions, the inverse (pruning dependencies a deletion frees the project from) is consistent with the principle's spirit and reduces the surface area the architecture's provider/client code must reason about.

### [PASS] Verification strategy addresses deletion failure modes

Although the slice is a deletion (no new I/O paths or message types requiring hang/timeout/disconnect handling), the verification walkthrough enumerates the relevant failure modes for a removal: dangling references (`grep -ri news`), unresolved dependencies (`uv sync`), test-suite regression (per-subpackage suite), and CLI surface change (`mt --help`). This is appropriate coverage for the slice type and the existing 30-second pytest timeout hazard from the `pymongo` server-RTT thread is explicitly called out as eliminated.

### [PASS] Consumer/dependency analysis is thorough and falsifiable

The slice documents its cross-reference check (case-insensitive `news` grep across `src/` and `test/`, inspection of `cli/`, `api_server/`, `config/`, `.env_sample`) and identifies the single false-positive (`"NEWSTOCK"` literal in `test_chunking_strategy.py`) as unrelated. The single cross-directory edge (`news.py` → `agents/newsagent.py`) is identified and both sides are deleted together. No hidden dependencies remain.

### [NOTE] Scope of removal exceeds the architecture's explicit enumeration but is consistent with its broader principles

The architecture's Envisioned State names only the `news.py` CLI entry point as slated for removal. This slice removes a much broader surface (7 source modules, 1 agent, 8 test files, 2 dependencies). This is consistent with the broader "Clean codebase" goal and the maintenance band authorization, but the slice doc could more explicitly cite the architectural basis for the broader scope (the Current State's mention of "Deprecated code in `market/deprecated/slice025_2025_01/`" being removed in analogous fashion) to preempt scope-creep questions. Not a finding requiring action.

### [NOTE] Chromadb and stale description deferred as follow-ups

The slice acknowledges `chromadb` (unused, in `pyproject.toml`) and the stale `[project.description]` ("news and market data management utilities") as out-of-scope, citing the slice-plan's effort-1 sizing. Both are minor remaining "clean codebase" gaps, and the slice is disciplined in not absorbing them. Worth noting that the architecture's "Minimal new dependencies" and PyPI-publishability goals are not fully achieved until these are addressed, but this is not a slice-design failure — it is a reasonable follow-up.

### [NOTE] No NFR restatement required for this slice type

The architecture does not state NFRs (latency, throughput, availability) specific to the news subsystem or its MongoDB backend, and this slice is a deletion that adds no new I/O paths. No NFR restatement is required, and the slice correctly does not invent one. (The 30-second pytest timeout hazard is a test-harness concern rather than an architecture-level NFR.)
