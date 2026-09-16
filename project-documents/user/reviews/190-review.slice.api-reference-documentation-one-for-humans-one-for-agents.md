---
docType: review
layer: project
reviewType: slice
slice: api-reference-documentation-one-for-humans-one-for-agents
project: trading-data
verdict: CONCERNS
sourceDocument: project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md
aiModel: claude-sonnet-5
status: complete
dateCreated: 20260916
dateUpdated: 20260916
reviewedSha: 84f20e9fb063acee1c8837192649c3ce0790bae2
findings:
  - id: F001
    severity: concern
    category: consistency
    summary: "Unmeasured claim breaks the document's own no-invented-facts rule"
    location: "project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md:245-246"
  - id: F002
    severity: note
    category: completeness
    summary: "statement_timeout/504 request-vs-statement nuance not called out in the outline"
    location: "project-documents/user/slices/190-slice.api-reference-documentation-one-for-headings-one-for-agents.md#agentsmd-section-5-failure-modes"
  - id: F003
    severity: pass
    category: nfr-alignment
    summary: "NFR restated with measured, route-specific targets rather than the inherited estimate"
    location: "project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md:231-253"
  - id: F004
    severity: pass
    category: architectural-boundary
    summary: "D3's only code change stays inside the Thin-Wrapper boundary"
    location: "project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md:199-229"
  - id: F005
    severity: pass
    category: dependency-direction
    summary: "Dependencies and integration points match the architecture and its downstream slices"
    location: "project-documents/user/slices/190-slice.api-reference-documentation-one-for-humans-one-for-agents.md:115-140"
---

# Review: slice — slice 190

**Verdict:** CONCERNS
**Model:** claude-sonnet-5

## Findings

### [CONCERN] Unmeasured claim breaks the document's own no-invented-facts rule

D3's msgpack-savings table measures three routes (bars 1d, Kalshi candlesticks, Kalshi trades) and correctly notes none of them match the architecture's stated NFR shape ("minute bars over weeks," 180-arch.data-serving.md:81/212). But line 246 then asserts "Equity bars carry floats and do better" for the untested minute-granularity case, with no measurement backing it. D6 of this same slice states the governing principle explicitly: "An invented example is a lie with syntax highlighting" — the same standard should apply to an invented performance claim. SC12 requires reporting "per-route measured sizes... not the architecture's 40–60% estimate," but the design itself violates that requirement for the one shape (`1m` bars over weeks) that is actually the architecture's stated NFR target. A reader could carry this line into the reference document as if it were established, when the ~40–60% figure for minute bars remains as unverified after this slice as before it.

### [NOTE] statement_timeout/504 request-vs-statement nuance not called out in the outline

The parent architecture (180-arch.data-serving.md:291) documents an important caveat: `statement_timeout` bounds a single statement, not a request, so a route issuing many statements can exceed its time budget many times over without any single statement being cancelled — measured at 186 D12b as a 95s request under a 20s budget. `agents.md` section 5 ("Failure modes... each status gets an explicit retry verdict") is exactly the place an agent consumer needs this nuance to reason correctly about 504 retries, but the outline (190-slice doc lines 417–422) doesn't name it as required content, unlike the credits-504 case which is explicitly called out (D8, SC9's walkthrough step 9). This is a design-level outline, so the content may still land during drafting — flagged as NOTE rather than CONCERN.

### [PASS] NFR restated with measured, route-specific targets rather than the inherited estimate

D3 restates the architecture's msgpack-savings NFR (180-arch.data-serving.md:81, "~40-60% vs JSON... for minute data over weeks") with measured, dated, per-route numbers (SC12), and explicitly reasons about *why* the measured routes fall short of the architecture's figure (`Decimal` fields are pre-stringified by `mode="json"` before either encoder runs) rather than silently contradicting or silently repeating the parent NFR. This is exactly the restatement-with-specific-target behavior the architecture alignment criteria call for.

### [PASS] D3's only code change stays inside the Thin-Wrapper boundary

180-arch.data-serving.md's "Design Principle: Thin Wrapper" (lines 34-43) forbids business logic in the API layer. D3 adds only a `responses={200: ...}` schema declaration to three existing routes — no served byte changes, gated by a mandatory pre/post byte-comparison (SC7) that reverts the change if any difference is found. The design treats this as a hard constraint ("the design does not assume its own preferred outcome," line 223) rather than an assumption, which is the correct posture for a documentation slice touching route code.

### [PASS] Dependencies and integration points match the architecture and its downstream slices

Prerequisites (189 for the last two routes, 186 D7 for the artifact/drift-test pattern, 188 for admission/error constants) are all upstream slices the architecture already documents as having landed or being in progress (180-arch.data-serving.md:173-177, 211, 288-303). "Interfaces provided" (docs/api/reference.md, agents.md) correctly targets trading-ui and agent clients, consistent with the architecture's stated consumer set (180-arch.data-serving.md:21, 318-325). No reversed or hidden dependency is introduced.
