"""Route handler for ``GET /api/v1/kalshi/search`` (#63)."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.kalshi_search import SearchResponse
from manta_trading.api_server.models.responses import GATEWAY_TIMEOUT_RESPONSE
from manta_trading.data.kalshi import serve_search
from manta_trading.data.kalshi.constants import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
)

router = APIRouter(prefix="/api/v1/kalshi")


@router.get("/search", responses=GATEWAY_TIMEOUT_RESPONSE)
async def search(
    q: Annotated[str, Query(min_length=1, pattern=r"\S")],
    limit: Annotated[int, Query(ge=1, le=SEARCH_MAX_LIMIT)] = SEARCH_DEFAULT_LIMIT,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> SearchResponse:
    """Search series, events and open markets by plain-language title.

    Returns the best ``limit`` hits at each level, grouped series, then events,
    then markets; every hit carries its parent tickers. Words are stemmed
    ("rates" finds "rate"), all words must appear, and a close misspelling
    still matches. A series is also found by the start of its ticker. Only open
    markets are searched. Synonyms are not understood: "rate cut" does not find
    "funds rate". Unlike the list routes this is a top-N, not a complete
    listing, and has no row ceiling.
    """
    loop = asyncio.get_running_loop()
    hits = await loop.run_in_executor(
        None, lambda: serve_search.search(db, q, limit=limit)
    )
    return SearchResponse.from_hits(q, hits)
