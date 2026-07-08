#!/bin/bash

source .venv/bin/activate
python -m manta_trading.news.news "$@"