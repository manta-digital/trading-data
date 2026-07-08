@echo off
call .venv\Scripts\activate.bat
python -m manta_trading.market.ohlc %*