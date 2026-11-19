---
docType: slice-design
slice: standing-fixes
project: trading-data
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: []
dateCreated: 20261119
dateUpdated: 20261119
status: in_progress
---

# Slice Design: standing-fixes

## Overview

A permanent container for fixes that need more than one step. It is never complete. Each fix is a task group in [990-tasks.standing-fixes.md](../tasks/990-tasks.standing-fixes.md), titled with its issue number and committed at the group's `**Commit**:` markers on the target branch. Unrelated fixes belong here together, and a closed group may be reopened. Small fixes do not come here: they are committed directly, following the project's fix rules.

## Value

Multi-step fixes get a checklist and commit points without becoming slices.

## Technical Scope

Whatever the open task groups say. Each group's issue is its spec.

## Dependencies

None of its own. Each group names what it needs.

## Architecture

None. Each fix works within the existing architecture.

## Technical Decisions

Recorded per group in the task file, next to the work.

## Integration Points

Per group.

## Success Criteria

Each group closes its issue.

### Verification Walkthrough

Each group ends with its own production check.

## Implementation Notes

Work groups on the target branch. No branch, no review unless the Project Manager asks.
