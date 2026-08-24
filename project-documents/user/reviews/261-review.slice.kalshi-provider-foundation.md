---
docType: review
layer: project
reviewType: slice
slice: kalshi-provider-foundation
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/261-slice.kalshi-provider-foundation.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260824
dateUpdated: 20260824
reviewedSha: 020f447e598d277b50ac572707c05cb38b42c0a4
findings:
  - id: F001
    severity: pass
    category: alignment
    summary: "Discovery findings satisfy the architecture's discovery mandate"
    location: "project-documents/user/slices/261-slice.kalshi-provider-foundation.md:28-71"
  - id: F002
    severity: pass
    category: nfr-restatement
    summary: "Shared rate budget across the pass correctly restates the architecture's NFR"
    location: "project-documents/user/slices/261-slice.kalshi-provider-foundation.md:127-130"
  - id: F003
    severity: pass
    category: scope
    summary: "Scope boundaries match the slice plan and avoid creep into downstream slices"
    location: "project-documents/user/slices/261-slice.kalshi-provider-foundation.md:79-89"
  - id: F004
    severity: concern
    category: error-handling
    summary: "Connection-level I/O failures are not enumerated in the error taxonomy"
    location: "project-documents/user/slices/261-slice.kalshi-provider-foundation.md:131-134"
---

# Review: slice — slice 261

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [PASS] Discovery findings satisfy the architecture's discovery mandate

The architecture requires that "exact endpoint behavior and the current cutoff must be verified against Kalshi's published documentation during slice design — not assumed" and that the cutoff be "treated as data (discovered, not hardcoded)." The slice's Discovery Findings section verifies base URLs, auth requirements, the full endpoint surface, historical cutoff mechanics, and rate limits against docs.kalshi.com with a dated check, and explicitly resolves the 266 gate decision. This is a faithful, well-sourced discharge of that architectural obligation.

### [PASS] Shared rate budget across the pass correctly restates the architecture's NFR

The architecture's Technical Considerations require the client be "budgeted so catalog sync, candles, and trades share one budget across the pass." The slice's Data Flow section states the RateLimiter is "one instance per client — the shared budget across all surfaces the architecture requires; 262–266 all call through one client," and Technical Decision 4 gives concrete, single-source-of-truth numeric targets (300 req/min public, 1000 req/min authenticated) rather than leaving the NFR implicit.

### [PASS] Scope boundaries match the slice plan and avoid creep into downstream slices

"No collection logic runs in this slice," candle/trade data tables, `/historical/*` fetch methods beyond cutoff, the pass command, systemd wiring, and `mt data kalshi status` are all explicitly deferred to 262–266, matching the parent slice plan's (260-slices.kalshi-event-contract-data.md) division of work. The optional authenticated-signing addition to this slice is not scope creep — it is an explicit architecture/PM decision (260-arch.kalshi-event-contract-data.md, Technical Considerations §Rate-limit budget, and Future Work item 2 marked "Superseded... folded into slice 261").

### [CONCERN] Connection-level I/O failures are not enumerated in the error taxonomy

The Data Flow section maps failures as "429/5xx/timeouts → ProviderTransientError (after retries exhausted); other 4xx → ProviderPermanentError." This only classifies HTTP status-code outcomes and (loosely) "timeouts." It does not explicitly enumerate handling for connection-level failures on this new I/O path — DNS resolution failure, connection refused, TLS handshake failure, or a peer disconnecting mid-response (e.g., httpx `ConnectError`/`ReadError`/`RemoteProtocolError`) — which are exactly the failure classes the review checklist calls out (hang, timeout, peer-disconnect-mid-send). Success Criterion 2 (line 221) repeats the same status-code-only taxonomy in its test list, so the gap propagates into what gets tested. Since this is a new outbound HTTP client relied on by five downstream slices (262–266), an unclassified `httpx.TransportError` subtype either crashes uncaught or is silently swallowed by a broad except — both anti-patterns this project's CLAUDE.md explicitly forbids. Recommend the client contract state explicitly that all `httpx.TransportError` subclasses (connect/read/write/pool errors, protocol errors) are treated as transient and mapped to `ProviderTransientError` after retry, alongside a stated request-timeout value (connect/read/write/pool), so the classification is complete rather than implicit.
