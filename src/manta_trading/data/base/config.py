"""
Configuration for foundation modules.

This module contains configuration settings for instrument registry,
trading calendars, adjustment policies, and session partitioning.
"""

FOUNDATION_CONFIG = {
    'instrument_registry': {
        'cache_ttl_seconds': 3600,  # Cache instrument lookups for 1 hour
        'auto_register_unknown': False,  # Require explicit registration
    },
    'trading_calendar': {
        'cache_ttl_seconds': 86400,  # Cache calendar data for 24 hours
        'preload_years': [2020, 2021, 2022, 2023, 2024, 2025],
        'default_timezone': 'America/New_York',
    },
    'adjustment_policy': {
        'default_policy': 'split_adjusted',
        'validate_ohlcv': True,
        'allow_negative_prices': False,
    },
    'session_partitioning': {
        'enabled': True,
        'default_session': 'RTH',
        'track_extended_hours': True,
    }
}
