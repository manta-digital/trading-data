"""The busiest real tick session the load tier ingests (slices 231, 308).

The archived 2024-12-18 ``tbbo`` day (889,373 records). By that day the ES
volume had rolled to ESH5 (672,277 records); ESZ4 holds 183,748. The
``busiest`` fixture in ``conftest.py`` ingests it through the real pass.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

ARCHIVE = Path("/data/tick-archive")
BUSIEST_DAY = date(2024, 12, 18)
BUSIEST_TIER = ARCHIVE / "GLBX-20250123-XT4GD5UM6C" / "glbx-mdp3-20241218.tbbo.dbn.zst"
BUSIEST_DEFINITIONS = (
    ARCHIVE / "GLBX-20260930-HVGRLYKHRN" / "glbx-mdp3-20241218.definition.dbn.zst"
)
BUSIEST_RECORDS = 889_373
