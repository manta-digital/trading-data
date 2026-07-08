#!/usr/bin/env python3
"""Quick script to check the database schema."""

import os
from dotenv import load_dotenv
load_dotenv()

import sys
from pathlib import Path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from manta_trading.market.marketdb import MarketDB

# Connect to test database
dbname = os.getenv('MARKET_PSQL_DB_TEST')
user = os.getenv('MARKET_PSQL_USER')
password = os.getenv('MARKET_PSQL_PASSWORD')
host = os.getenv('MARKET_PSQL_HOST')
port = int(os.getenv('MARKET_PSQL_PORT', '5432'))

db = MarketDB(dbname, user, password, host, _port=port)
db.connect()

print("📋 symbol_list table schema:")
db.cur.execute("""
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns 
    WHERE table_name = 'symbol_list'
    ORDER BY ordinal_position
""")

for column_name, data_type, is_nullable, column_default in db.cur.fetchall():
    nullable = "NULL" if is_nullable == "YES" else "NOT NULL"
    default = f"(default: {column_default})" if column_default else ""
    print(f"  {column_name:20} | {data_type:15} | {nullable:8} {default}")

db.close()