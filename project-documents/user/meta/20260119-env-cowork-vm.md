# Claude Cowork VM Setup for Trading Project

## Overview

The trading project's `.venv` is created on macOS and won't work in the Cowork VM (Linux). This document describes how to set up a working Python environment in each new Cowork session.

## Procedure

Run these commands at the start of each Cowork session:

```bash
# 1. Install uv (if not already installed)
pip install uv

# 2. Create venv in session directory (not in mounted folder)
cd /sessions/{session-name}
~/.local/bin/uv venv trading-venv

# 3. Install dependencies from trading project
cd /sessions/{session-name}/mnt/trading
source /sessions/{session-name}/trading-venv/bin/activate
~/.local/bin/uv pip install -e .

# 4. Install dev dependencies (poetry group.dev not auto-detected by uv)
~/.local/bin/uv pip install pytest pytest-asyncio pytest-cov pytest-mock motor

# 5. Verify
python -m pytest test/unit/test_*.py -v --tb=short
```

## Why This Works

- **venv location**: Created in `/sessions/` (writable) not in mounted folder (read-only for some operations)
- **uv**: Fast, reads pyproject.toml, doesn't need poetry installed
- **motor**: Listed in poetry dev deps but needs explicit install

## Test Baseline (2026-01-19)

```
New-style tests (test_*.py): 152 passed, 0 failed
All unit tests: 345 passed, 19 failed

Failed tests (expected):
- testmarketdb.py: Database connection (no DB in VM)
- testohlc.py/testohlcoptions.py: Broken import from deprecated code
- testpathutil.py: File permission issues in VM
- testalphavantage.py: API test
- testdatetimehelper.py: Edge case formatting
```

## Notes

- The mounted `.venv` from macOS cannot be deleted or modified
- Each session requires fresh setup (venv doesn't persist)
- Can optionally add `.venv-cowork` to `.gitignore` if creating in project dir
