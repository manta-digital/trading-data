---
docType: review
layer: project
reviewType: code
slice: databento-adapter-and-cost-preflight
targetKind: slice
rulesSource: project
project: trading-data
verdict: CONCERNS
verdictSource: stated
sourceDocument: project-documents/user/slices/220-slice.databento-adapter-and-cost-preflight.md
aiModel: z-ai/glm-5.3-flash
status: complete
dateCreated: 20260927
dateUpdated: 20260927
reviewedSha: 9b75fd0bcd2f272c1c7c6e2eb01735410f5b513a
toolsGiven: [read_file, list_files, grep]
toolCallsMade: 30
findings:
  - id: F001
    severity: concern
    category: dry
    summary: "HTTP status→ProviderError mapping is defined twice (DRY / one-value-one-source)"
    location: "src/manta_trading/data/tick/databento/adapter.py#client_error"
  - id: F002
    severity: concern
    category: performance
    summary: "Eager numpy import in provider.py loads NumPy on every `mt` startup"
    location: "src/manta_trading/data/tick/provider.py:19"
  - id: F003
    severity: note
    category: testing
    summary: "No CI-gated load-tier test yet for the new network download/decode path"
    location: "src/manta_trading/data/tick/databento/_download.py"
  - id: F004
    severity: note
    category: api-design
    summary: "CeilingVerdict.NO_CEILING embeds operator prose into a machine-readable JSON value"
    location: "src/manta_trading/data/tick/estimate.py#CeilingVerdict"
  - id: F005
    severity: note
    category: error-handling
    summary: "as_count raises TypeError for a negative integer"
    location: "src/manta_trading/data/tick/databento/_parse.py#as_count"
  - id: F006
    severity: note
    category: robustness
    summary: "record_databento_fixtures.py uses a CWD-relative fixture directory"
    location: "scripts/record_databento_fixtures.py#FIXTURE_DIR"
  - id: F007
    severity: note
    category: correctness
    summary: "Minor robustness nits in the decode benchmark script"
    location: "scripts/bench_dbn_decode.py#build_input"
  - id: F008
    severity: pass
    category: design
    summary: "Spend-safety is enforced by construction, not by convention"
    location: "src/manta_trading/data/tick/estimate.py#build_estimate"
  - id: F009
    severity: pass
    category: error-handling
    summary: "Paid-call and download semantics are precise and exhaustively tested"
    location: "src/manta_trading/data/tick/databento/_download.py#download_file"
  - id: F010
    severity: pass
    category: testing
    summary: "Tests run against recorded real-provider fixtures and real DBN binaries, test-with the implementation"
    location: "test/unit/data/tick/test_adapter_metadata.py"
---

# Review: code — slice 220

**Verdict:** CONCERNS
**Model:** z-ai/glm-5.3-flash

## Findings

### [CONCERN] HTTP status→ProviderError mapping is defined twice (DRY / one-value-one-source)

`adapter.client_error` maps `429 → ProviderTransientError`, `401/403 → ProviderAuthError`, else permanent, using its own `_AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})`. `_download._status_error` (src/manta_trading/data/tick/databento/_download.py#_status_error) independently re-defines the identical `_AUTH_STATUSES` and re-implements the same policy (`429`/`5xx → transient`, `401/403 → auth`, else permanent, plus a `_GONE_STATUSES` refinement). CLAUDE.md is explicit: comparison values used in conditionals must be defined once and referenced everywhere. The two copies can drift silently — e.g., adding `408` or `402` handling to one and not the other changes retry semantics between metadata calls and downloads with no compile-time signal. `_download.py` deliberately avoids importing the adapter (the adapter imports `_download`, so a shared helper cannot live in `adapter.py`), but a status→error mapper in a neutral home (`manta_trading/providers/errors.py` or a small `_status_map` in `_download.py` that the adapter's `client_error` also calls) would give one definition. The test suites cover both mappings today, which limits but does not remove the drift risk.

### [CONCERN] Eager numpy import in provider.py loads NumPy on every `mt` startup

`provider.py` imports `numpy as np` at module level (line 19), but the only use is the `RecordBatch.records: np.ndarray` annotation. Because `tick.py` imports `TickRequest` from this module and `data.py` registers `tick_app`, this import executes on every `mt` invocation. The slice itself demonstrates it cares about this cost — `tick.py` defers the `DatabentoTickProvider` import into the command body with the comment that `databento`/pandas/pyarrow "adds ~0.15 s to every `mt` command's startup otherwise". NumPy is cheaper than that chain (~60–100 ms) but it is the same category of cost, and whether it is already paid transitively elsewhere in `data.py`'s import graph is unverified from what I inspected. With `from __future__ import annotations` already in place, moving the import under `if TYPE_CHECKING:` is a one-line, zero-risk fix that removes the question entirely.

### [NOTE] No CI-gated load-tier test yet for the new network download/decode path

The Python rules require at least one load test (asserting latency/throughput/resource bounds, gated in CI) for code on network/concurrency paths. `_download.py` (verified HTTP download with resume) and the batch decode path are exactly such code, and the only measurement today is the manual `scripts/bench_dbn_decode.py`, which is not a pytest load test and not CI-gated. The slice's own docs explicitly defer throughput targets to slice 226 on purchased data ("this measures decode only… its rate is an upper bound… never pass it"), and no realistic data volume exists to test against yet, so this is recorded as design-acknowledged debt rather than a blocker: when 226 lands, the load-tier gate must land with it.

### [NOTE] CeilingVerdict.NO_CEILING embeds operator prose into a machine-readable JSON value

`NO_CEILING = f"no ceiling configured ({TICK_SPEND_CEILING_ENV} unset)"` — the enum's *value* is a human sentence, and it flows verbatim into the `--json` payload as `ceiling_verdict`. The other verdicts are clean machine tokens (`within`, `over`, `bought with each tier`, `not purchasable in this initiative`), so a JSON consumer keying on verdict strings gets one prose entry with punctuation and a variable name inside it. Rendering the prose at the presentation layer (as `tick_render` already does for the ceiling line) and keeping the enum value a token like `no_ceiling` would make the JSON contract uniform. Currently exercised and pinned by tests, so a change is a deliberate contract change.

### [NOTE] as_count raises TypeError for a negative integer

`as_count` raises `TypeError(f"… expected a non-negative integer, got {raw!r}")` when the value is an `int` but negative — a *value* problem, semantically `ValueError`, not a type problem. All three exception types are mapped to `ProviderPermanentError` by the adapter and tests cover the `-1` case, so behavior is correct; this is a naming/semantics nit only. (`as_usd` has the same shape.)

### [NOTE] record_databento_fixtures.py uses a CWD-relative fixture directory

`FIXTURE_DIR = Path("test/fixtures/databento/metadata")` resolves against the current working directory, while the sibling script `bench_dbn_decode.py` anchors its sample via `Path(__file__).resolve().parents[1]`. Run from anywhere other than the repo root, the recorder happily `mkdir(parents=True)` and writes fixtures into a wrong tree (each path is printed, so it is discoverable, but nothing fails). Anchoring to `__file__` like the bench script does would make the script CWD-proof; the docstring should also state the repo-root requirement if CWD-relative is kept.

### [NOTE] Minor robustness nits in the decode benchmark script

Three small items in `build_input`: (1) `SAMPLE.open("rb")` is never explicitly closed (the zstd `stream_reader` may not close the underlying handle) — the rules prefer context managers for resource management, and `with SAMPLE.open("rb") as fh: …stream_reader(fh)…` is cheap; (2) the TradeMsg record size `48` is a magic number (commented, but it is exactly the kind of comparison value the project wants defined once — the record-size derivation already exists in `dbn_file._record_size`); (3) a sample file with an empty body would make `sample_count == 0` and `divmod(records, sample_count)` raise `ZeroDivisionError` — unreachable with the committed fixture, but an explicit guard would fail with a message instead.

### [PASS] Spend-safety is enforced by construction, not by convention

The preflight accepts only `ITickMetadataProvider` — a protocol with no billable method — so `build_estimate` cannot spend regardless of what implementation is passed, and it imports nothing from `data/tick/databento/` (guarded by `test_import_boundary.py`). The fixture-recording script reinforces this from the other side: it drives a real provider through a recording proxy that raises on any access outside `metadata`/`symbology`, so the script structurally cannot reach `batch`/`timeseries`, and it scans every output file for the API key before writing. The test suite then backstops both: fake `Historical` instances leave `batch`/`timeseries` unsupplied so any billable access raises. This is exactly the "program to interfaces / fail explicitly" posture CLAUDE.md asks for.

### [PASS] Paid-call and download semantics are precise and exhaustively tested

Paid calls distinguish "refused, not charged" (4xx → mapped normally) from "outcome unknown" (`ProviderOutcomeUnknownError`, deliberately not a `ProviderTransientError` subclass), and `submit_batch` wraps even a failed read-back of a *successful* submit as outcome-unknown. `fetch_range` never leaves a partial masquerading as a final file and deletes residue on every failure path. The download module verifies size plus SHA-256 before the final rename, refuses path-like filenames, refuses to touch a mismatching pre-existing final, and handles 206/200/416 resume semantics correctly — and the tests assert on-disk state after every case (`test_download.py`), including resumed-Range behavior and the never-call-`batch.download` invariant. The `PAID_FAILURES`/`FAILURES` parametrized tables with `type(caught.value) is expected` assertions are the right rigor for money-adjacent error mapping.

### [PASS] Tests run against recorded real-provider fixtures and real DBN binaries, test-with the implementation

Metadata tests decode fixtures recorded from the live free endpoints (with the recording script and its key-scan providing provenance), the DBN reader tests use the five committed real `.dbn.zst` binaries including a v2 file, and the batch-job parser is validated against a real (redacted) job record — satisfying CLAUDE.md's "the test fixture must include the actual format the parser will consume in production." Coverage patterns follow the rules: shared fakes in `test/tick_support`, parametrized edge cases, mocking only at the SDK/HTTP boundary, real settings with `_env_file=None`, and no `.env` or production-variable reads anywhere in the new test code (the repo's prod-URL scrub and guard conventions are untouched because this slice touches no database at all).

### Run Digest

- Response length: 10370 chars
- Response is newline-free: no
- Tool calls made: 30
- Tool calls failed: 2
- Stop reason: stop
- Reasoning characters: 71719
- `## Summary` located: yes
- `## Findings` located: yes
- Finding-shaped matches — whole response: 10
- Finding-shaped matches — inside fences: 0
- Finding-shaped matches — in findings section: 10
- Finding-shaped matches — surviving validation: 10
