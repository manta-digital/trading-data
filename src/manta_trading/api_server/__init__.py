"""Inbound HTTP service (FastAPI) for the Manta Trading Data Serving API.

Not to be confused with :mod:`manta_trading.api`, which holds outbound
provider HTTP utilities (EODHD sync, retry policy, Finnhub client).
"""

from manta_trading.api_server.app import create_app as create_app
