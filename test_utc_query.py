#!/usr/bin/env python3
"""
Direct UTC timezone test
"""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import pandas as pd
from sqlalchemy import create_engine, text

load_dotenv()

# Create SQLAlchemy engine
connection_url = f"postgresql://{os.getenv('TRADING_PSQL_USER')}:{os.getenv('TRADING_PSQL_PASSWORD')}@{os.getenv('TRADING_PSQL_HOST')}:5432/{os.getenv('TRADING_PSQL_DB')}"
engine = create_engine(connection_url)

print("🔍 Testing direct UTC query...")

# Test timezone-aware query
start_time = datetime(2024, 1, 1, 14, 30, tzinfo=timezone.utc)
end_time = datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

print(f"Querying: {start_time} to {end_time}")

query = text("""
    SELECT time, symbol, open, high, low, close, volume
    FROM minute_ohlcv
    WHERE symbol = :symbol 
    AND time >= :start_time 
    AND time <= :end_time
    ORDER BY time
""")

# Test direct connection execution first
with engine.connect() as conn:
    result = conn.execute(query, {'symbol': 'TSLA', 'start_time': start_time, 'end_time': end_time})
    rows = result.fetchall()
    
    print(f"✅ Retrieved {len(rows)} rows")
    if rows:
        print("Sample data:")
        for row in rows[:3]:
            print(f"  {row[0]} {row[1]} O:{row[2]} C:{row[5]} V:{row[6]}")
    else:
        print("No data found - checking all data...")
        
        # Check what data actually exists
        all_query = text("SELECT time, symbol FROM minute_ohlcv ORDER BY time")
        all_result = conn.execute(all_query)
        all_rows = all_result.fetchall()
        print(f"Total rows in table: {len(all_rows)}")
        if all_rows:
            print("Available data:")
            for row in all_rows:
                print(f"  {row[0]} {row[1]}")

# Test pandas compatibility with SQLAlchemy 2.0
print("\n🐼 Testing pandas + SQLAlchemy 2.0 compatibility...")
try:
    df = pd.read_sql_query(
        query, 
        engine, 
        params={'symbol': 'TSLA', 'start_time': start_time, 'end_time': end_time}
    )
    print(f"✅ pandas query successful! Retrieved {len(df)} rows")
    if not df.empty:
        print("Sample pandas DataFrame:")
        print(df.head(2))
except Exception as e:
    print(f"❌ pandas query failed: {e}")

engine.dispose()