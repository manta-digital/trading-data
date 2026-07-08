#!/usr/bin/env python3
"""
Simple test to verify data exists in trading_test database
"""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

# Connect to database
conn = psycopg2.connect(
    host=os.getenv('TRADING_PSQL_HOST'),
    database=os.getenv('TRADING_PSQL_DB'),
    user=os.getenv('TRADING_PSQL_USER'),
    password=os.getenv('TRADING_PSQL_PASSWORD'),
    port=int(os.getenv('TRADING_PSQL_PORT', '5432'))
)

print(f"Connected to {os.getenv('TRADING_PSQL_DB')} on {os.getenv('TRADING_PSQL_HOST')}")

with conn.cursor() as cur:
    # Check if data exists
    cur.execute("SELECT COUNT(*) FROM minute_ohlcv")
    count = cur.fetchone()[0]
    print(f"Total rows in minute_ohlcv: {count}")
    
    if count > 0:
        # Show sample data
        cur.execute("SELECT * FROM minute_ohlcv ORDER BY time LIMIT 3")
        rows = cur.fetchall()
        print("\nSample data:")
        for row in rows:
            print(f"  {row[0]} {row[1]} O:{row[2]} H:{row[3]} L:{row[4]} C:{row[5]} V:{row[6]}")
        
        # Check date range
        cur.execute("SELECT MIN(time), MAX(time) FROM minute_ohlcv")
        min_time, max_time = cur.fetchone()
        print(f"\nData range: {min_time} to {max_time}")
        
        # Check symbols
        cur.execute("SELECT DISTINCT symbol FROM minute_ohlcv")
        symbols = [row[0] for row in cur.fetchall()]
        print(f"Symbols: {symbols}")

conn.close()