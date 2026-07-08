---
layer: process
docType: evolution
phase: meta
date: 2026-01-19
purpose: Proposed updates to ai-project-guide for Staff/Principal level coordination
author: Erik + Claude Staff (Cowork)
status: draft
---

# AI Project Guide Evolution - Staff/Principal Level Patterns

## Context

This document captures proposed updates to the ai-project-guide methodology based on learnings from:
1. Multi-project coordination (trading, context-forge, context-forge-pro)
2. Extended AI collaboration at staff/principal engineering level
3. Claude Cowork capabilities (persistent file access, multi-project visibility)

## The Paradigm Shift

### Previous Model (2025)
```
Human (PM/Tech Lead)
    ↓ manages
Claude Code (per-project)
    ↓ creates
Code + Documentation
```

**Limitation:** Human was sole orchestrator across projects. Context management was the bottleneck.

### Emerging Model (2026)
```
PRINCIPAL/DIRECTOR LEVEL
┌─────────────────────────────────────────────────────┐
│  Human + Claude Cowork                               │
│  - Portfolio-wide visibility                         │
│  - Cross-project decisions                           │
│  - Methodology evolution (ai-project-guide)          │
│  - Strategic prioritization                          │
└─────────────────────────────────────────────────────┘
                      │
                      ▼
STAFF LEVEL
┌─────────────────────────────────────────────────────┐
│  Human + Claude Cowork (project-cluster scope)       │
│  - Ecosystem coordination (e.g., all trading)        │
│  - Architecture across services                      │
│  - Quality/progress evaluation                       │
│  - Orchestration of project-level work               │
└─────────────────────────────────────────────────────┘
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
PROJECT LEVEL (Lead/Senior)
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Trading/ │  │ Trading/ │  │ Context  │
│ data     │  │ UI       │  │ Forge    │
│ Human +  │  │ Human +  │  │ Human +  │
│ Claude   │  │ Claude   │  │ Claude   │
│ Code     │  │ Code     │  │ Code     │
└──────────┘  └──────────┘  └──────────┘
```

---

## Proposed Guide Additions

### 1. New Role: Staff Engineer (AI)

Add to `guide.ai-project.000-process.md` Roles section:

```markdown
- Staff Engineer (AI): A meta-level AI (Claude Cowork or equivalent) providing:
  - Multi-project coordination and architectural oversight
  - Cross-cutting decision support
  - Progress tracking across project portfolio
  - Methodology evolution and pattern recognition
  - Context recovery and continuity management

  The Staff Engineer AI operates above individual projects, coordinating
  multiple Project-level AI/Human teams. It maintains awareness of:
  - All active projects and their states
  - Dependencies between projects
  - Shared infrastructure and patterns
  - Strategic priorities and resource allocation
```

### 2. New Document Type: Portfolio State

Add to `directory-structure.md`:

```markdown
###### Portfolio-Level Documents
```markdown
* PORTFOLIO.md: High-level state of all projects in the workspace
  - Active projects and their current slice/phase
  - Cross-project dependencies
  - Strategic priorities
  - Resource allocation notes

* portfolio/: Directory for cross-project artifacts
  - portfolio/decisions/: Architectural decisions affecting multiple projects
  - portfolio/patterns/: Shared patterns and templates
  - portfolio/audits/: Periodic portfolio reviews
```

### 3. New Document Type: DEVLOG.md (Per-Project)

Already added (per 2026-01-18 session). Confirm standard:

```markdown
* DEVLOG.md: Append-only activity log at project root
  - Format: Newest entries first
  - Date headers: ## YYYY-MM-DD
  - Brief notes: 1-3 lines per session
  - Purpose: Enable context recovery after breaks
  - Never delete entries, only append
```

### 4. Context Recovery Protocol

Add new section to process guide or create new guide:

```markdown
### Context Recovery After Hiatus

When returning to a project after extended absence (>2 weeks):

1. **Staff-level scan** (if multi-project):
   - Review DEVLOG.md files across projects
   - Check git log summaries for each project
   - Identify which projects need attention

2. **Project-level recovery** (per project):
   - Read DEVLOG.md for activity history
   - Review slice files for current phase/status
   - Check task files for incomplete items
   - Verify git status (uncommitted changes, branches)
   - Run tests to validate current state

3. **Create Project State summary**:
   - Current slice and phase
   - Incomplete tasks
   - Known blockers or decisions needed
   - Recommended next actions

4. **Update DEVLOG.md** with recovery session notes
```

### 5. Cross-Project Decision Records

Add to decision documentation patterns:

```markdown
### Cross-Project Architectural Decisions

When a decision affects multiple projects:

1. Create decision record in `portfolio/decisions/`
2. Name: `YYYYMMDD-decision.{topic}.md`
3. Reference in affected projects' architecture docs
4. Include:
   - Context: What prompted this decision
   - Decision: What was decided
   - Affected projects: Which projects impacted
   - Consequences: Expected outcomes
   - Alternatives considered: What else was evaluated
```

---

## Proposed Workflow Additions

### Staff Engineer Review Cadence

For large portfolios, establish regular review patterns:

```markdown
### Weekly Portfolio Review

Staff Engineer AI performs:
1. Scan all project DEVLOG.md files
2. Check slice completion rates
3. Identify blocked projects
4. Flag cross-project conflicts
5. Update PORTFOLIO.md

Output: Portfolio status summary and recommended priorities
```

### Project Handoff Protocol

When transitioning a project between AI instances:

```markdown
### Project Handoff

1. **Outgoing AI** creates handoff document:
   - Current state summary
   - In-progress work
   - Known issues
   - Recommended next steps

2. **Handoff document** placed in project root or user/notes/

3. **Incoming AI** reads handoff + DEVLOG.md + current slice
```

---

## Implementation Priority

### Phase 1: Immediate (This Week)
- [ ] Add DEVLOG.md to ai-project-guide/directory-structure.md ✅ (done 2026-01-18)
- [ ] Create context recovery protocol document
- [ ] Add Staff Engineer role to process guide

### Phase 2: Short Term (This Month)
- [ ] Create portfolio documentation structure
- [ ] Establish cross-project decision record pattern
- [ ] Document project handoff protocol

### Phase 3: Medium Term
- [ ] Create portfolio review automation (if tooling allows)
- [ ] Establish metrics for portfolio health
- [ ] Develop patterns for multi-AI coordination

---

## Open Questions

1. **Portfolio vs Workspace**: Is "portfolio" the right term? Alternatives: ecosystem, workspace, domain

2. **Granularity of Staff Role**: When does a project group need Staff-level coordination vs individual project management?

3. **Tooling Integration**: How does this integrate with context-forge? Could context-forge generate portfolio summaries?

4. **Multi-Human Teams**: How does this scale when multiple humans are involved? Do we need coordination patterns for human teams + AI teams?

---

## Related Documents

- `guide.ai-project.000-process.md` - Base process guide
- `directory-structure.md` - File organization conventions
- `2026-01-18-cowork-session-context-recovery.md` - Session that identified these patterns
- `2026-01-19-trading-project-audit.md` - Staff-level audit example

---

## Notes

This document represents the first attempt at formalizing patterns that emerged naturally from using Claude Cowork for multi-project coordination. The patterns are derived from actual practice, not theoretical design.

Key insight: The bottleneck in AI-assisted development shifted from "AI can't handle complexity" to "humans can't orchestrate enough parallel AI work." Cowork mode addresses this by giving AI the ability to maintain cross-project context.
