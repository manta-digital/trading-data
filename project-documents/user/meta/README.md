# user/meta/

Operational and strategic documents that exist outside the slice system.

## Contents

- **FOCUS.md** - Current constraints. What must be solved before anything else matters. Living document, updated as constraints resolve or emerge.

- **YYYYMMDD-{type}-{subject}.md** - Point-in-time operational work:
  - `audit-*` - Analysis of project state
  - `env-*` - Environment/infrastructure setup
  - `tasks-*` - Tracked action items from analysis
  - `guide-*` - Methodology/process updates

## Naming Convention

```
YYYYMMDD-{type}-{subject}.md
```

- Date first (ISO format, no hyphens) for chronological sorting
- Type second for grouping similar work (`ls` shows all audits together)
- Subject last

## Philosophy

No sprints. No quarters. No arbitrary deadlines.

Do everything as fast as you can do it well, but no faster.

FOCUS.md defines the walls. Break through them. When done, update FOCUS.md.
