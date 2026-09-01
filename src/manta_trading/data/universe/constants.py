"""Tracked universe definitions for index constituent tracking (slice 161).

Single source of truth: changing which universes are tracked requires editing
exactly this dict. Nothing outside this module embeds the string "sp500".

R2000 and NASDAQ-100 are deferred until a reliable free data source is identified.
"""

from __future__ import annotations

TRACKED_UNIVERSES: tuple[str, ...] = ("sp500",)
"""Universe names for which constituent history is maintained."""

SP500_CSV_URL: str = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)
"""URL for the fja05680/sp500 CSV going back to 2019.

Format: date,tickers — one row per change date, full constituent set per row.
"""

SP500_GITHUB_API_URL: str = "https://api.github.com/repos/fja05680/sp500/contents/"
"""GitHub API URL to list repo contents and find the latest dated historical CSV.

The full history file (back to 1996) has a versioned filename like
'S&P 500 Historical Components & Changes(01-17-2026).csv'.
Use the API to find the most recent one dynamically.
"""
