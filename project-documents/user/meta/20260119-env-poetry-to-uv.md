# Migrating Trading Project from Poetry to uv

## Why

- uv is faster (10-100x)
- Simpler mental model
- Becoming standard
- Poetry is heavy for what we need

## Current State

- `pyproject.toml` uses poetry format
- `.venv` created by poetry
- Dev dependencies in `[tool.poetry.group.dev.dependencies]`

## Migration Steps (Local Mac)

### 1. Backup and Remove Poetry Artifacts

```bash
cd ~/trading  # or wherever

# Backup poetry.lock if you want to preserve exact versions
cp poetry.lock poetry.lock.backup

# Remove poetry-specific files
rm poetry.lock
rm -rf .venv
```

### 2. Update pyproject.toml

Convert from poetry format to standard PEP 621 format:

**Before (poetry):**
```toml
[tool.poetry]
name = "manta_trading"
packages = [{include = "manta_trading"}]
version = "0.2.1"
description = "..."
authors = ["Erik Corkran <erik.corkran@manta.digital>"]

[tool.poetry.dependencies]
python = ">=3.10, <3.13"
aiofiles = "^24.1.0"
# ... etc

[tool.poetry.group.dev.dependencies]
pytest = "^8.1.1"
# ... etc

[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"
```

**After (standard/uv):**
```toml
[project]
name = "manta_trading"
version = "0.2.1"
description = "manta.digital news and market data management utilities"
authors = [{name = "Erik Corkran", email = "erik.corkran@manta.digital"}]
readme = "README.md"
requires-python = ">=3.10,<3.13"

dependencies = [
    "aiofiles>=24.1.0",
    "aiohttp>=3.9.5",
    "backoff>=2.2.1",
    "loguru>=0.7.2",
    "pandas>=2.3.2",
    "python-dotenv>=1.0.1",
    "psycopg2-binary>=2.9.9",
    "pymongo>=4.9.2",
    "pytz>=2024.1",
    "chromadb>=0.5.0",
    "sqlalchemy>=2.0.43",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.1.1",
    "pytest-asyncio>=0.23.5",
    "pytest-cov>=4.1.0",
    "pytest-mock>=3.12.0",
    "motor>=3.6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["manta_trading"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["test"]
python_files = ["test_*.py", "*_test.py", "test*.py"]
```

### 3. Create New venv with uv

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh
# or: brew install uv

# Create venv
uv venv

# Install with dev deps
uv pip install -e ".[dev]"
```

### 4. Verify

```bash
source .venv/bin/activate
pytest test/unit/test_*.py -v
```

### 5. Update .gitignore

Ensure these are present:
```
.venv/
__pycache__/
*.egg-info/
```

### 6. Remove Poetry (Optional)

If no other projects use poetry:
```bash
pipx uninstall poetry
# or: brew uninstall poetry
```

## Lockfile

uv can generate `uv.lock` for reproducible builds:
```bash
uv lock
```

This replaces `poetry.lock`.

## Notes

- uv reads poetry-format pyproject.toml but can't write to it
- Converting to standard format is cleaner long-term
- `^` version specs become `>=` (close enough for our purposes)
- `hatchling` is a simple, fast build backend
