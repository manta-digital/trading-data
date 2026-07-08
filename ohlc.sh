#!/bin/bash

# Usage: ohlc.sh [stock_symbol]
source .venv/bin/activate
python -m manta_trading.market.ohlc "$@"