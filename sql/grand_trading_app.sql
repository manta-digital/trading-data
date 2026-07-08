-- Grant database-level permissions
  GRANT ALL PRIVILEGES ON DATABASE trading_test TO trading_app;

  -- Grant schema permissions
  GRANT USAGE ON SCHEMA public TO trading_app;
  GRANT CREATE ON SCHEMA public TO trading_app;

  -- Grant permissions on all existing tables and views
  GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO
  trading_app;
  GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO
  trading_app;

  -- Grant permissions on materialized views specifically
  GRANT SELECT ON minute_5min_ohlcv TO trading_app;
  GRANT SELECT ON minute_15min_ohlcv TO trading_app;
  GRANT SELECT ON minute_hourly_ohlcv TO trading_app;
  GRANT SELECT ON minute_4hour_ohlcv TO trading_app;
  GRANT SELECT ON minute_daily_ohlcv TO trading_app;
  GRANT SELECT ON minute_weekly_ohlcv TO trading_app;
  GRANT SELECT ON minute_monthly_ohlcv TO trading_app;

  -- Set default permissions for future objects
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO
   trading_app;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES
   TO trading_app;
