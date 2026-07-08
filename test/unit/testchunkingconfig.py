#!/usr/bin/env python3
"""
Unit tests for ChunkingConfig class.
Tests configuration loading, validation, and environment variable overrides.
"""

import os
import pytest
from unittest.mock import patch
from manta_trading.market.config import ChunkingConfig


class TestChunkingConfig:
    """Test ChunkingConfig configuration class."""
    
    def setup_method(self):
        """Clear environment variables before each test."""
        self.config_vars = [
            'CHUNK_SIZE_DAYS', 'MIN_DAYS_THRESHOLD', 'MAX_ERROR_COUNT', 
            'RECENT_DAYS_THRESHOLD', 'MAX_LOOKBACK_DAYS', 'CHUNK_DELAY_SECONDS', 'BATCH_SIZE'
        ]
        
        # Store original values and clear
        self.original_env = {}
        for var in self.config_vars:
            if var in os.environ:
                self.original_env[var] = os.environ[var]
                del os.environ[var]
    
    def teardown_method(self):
        """Restore original environment variables after each test."""
        # Clear any test values
        for var in self.config_vars:
            if var in os.environ:
                del os.environ[var]
                
        # Restore original values
        for var, value in self.original_env.items():
            os.environ[var] = value
    
    def test_default_values(self):
        """Test that default configuration values are correct."""
        config = ChunkingConfig()
        
        assert config.CHUNK_SIZE_DAYS == 100
        assert config.MIN_DAYS_THRESHOLD == 2
        assert config.MAX_ERROR_COUNT == 3
        assert config.RECENT_DAYS_THRESHOLD == 100
        assert config.MAX_LOOKBACK_DAYS == 200
        assert config.CHUNK_DELAY_SECONDS == 0.1
        assert config.BATCH_SIZE == 500
    
    def test_environment_variable_override(self):
        """Test that environment variables override default values."""
        os.environ['CHUNK_SIZE_DAYS'] = '50'
        os.environ['MIN_DAYS_THRESHOLD'] = '1'
        os.environ['MAX_ERROR_COUNT'] = '5'
        os.environ['RECENT_DAYS_THRESHOLD'] = '75'
        os.environ['MAX_LOOKBACK_DAYS'] = '300'
        os.environ['CHUNK_DELAY_SECONDS'] = '0.2'
        os.environ['BATCH_SIZE'] = '1000'
        
        config = ChunkingConfig()
        
        assert config.CHUNK_SIZE_DAYS == 50
        assert config.MIN_DAYS_THRESHOLD == 1
        assert config.MAX_ERROR_COUNT == 5
        assert config.RECENT_DAYS_THRESHOLD == 75
        assert config.MAX_LOOKBACK_DAYS == 300
        assert config.CHUNK_DELAY_SECONDS == 0.2
        assert config.BATCH_SIZE == 1000
    
    def test_validation_chunk_size_days_positive(self):
        """Test that CHUNK_SIZE_DAYS must be positive."""
        os.environ['CHUNK_SIZE_DAYS'] = '0'
        
        with pytest.raises(ValueError, match="CHUNK_SIZE_DAYS must be positive"):
            ChunkingConfig()
            
        os.environ['CHUNK_SIZE_DAYS'] = '-1'
        
        with pytest.raises(ValueError, match="CHUNK_SIZE_DAYS must be positive"):
            ChunkingConfig()
    
    def test_validation_min_days_threshold_non_negative(self):
        """Test that MIN_DAYS_THRESHOLD must be non-negative."""
        os.environ['MIN_DAYS_THRESHOLD'] = '-1'
        
        with pytest.raises(ValueError, match="MIN_DAYS_THRESHOLD must be non-negative"):
            ChunkingConfig()
    
    def test_validation_max_error_count_positive(self):
        """Test that MAX_ERROR_COUNT must be positive."""
        os.environ['MAX_ERROR_COUNT'] = '0'
        
        with pytest.raises(ValueError, match="MAX_ERROR_COUNT must be positive"):
            ChunkingConfig()
    
    def test_validation_recent_days_threshold_positive(self):
        """Test that RECENT_DAYS_THRESHOLD must be positive."""
        os.environ['RECENT_DAYS_THRESHOLD'] = '0'
        
        with pytest.raises(ValueError, match="RECENT_DAYS_THRESHOLD must be positive"):
            ChunkingConfig()
    
    def test_validation_max_lookback_days_positive(self):
        """Test that MAX_LOOKBACK_DAYS must be positive."""
        os.environ['MAX_LOOKBACK_DAYS'] = '0'
        
        with pytest.raises(ValueError, match="MAX_LOOKBACK_DAYS must be positive"):
            ChunkingConfig()
    
    def test_validation_chunk_delay_seconds_non_negative(self):
        """Test that CHUNK_DELAY_SECONDS must be non-negative."""
        os.environ['CHUNK_DELAY_SECONDS'] = '-0.1'
        
        with pytest.raises(ValueError, match="CHUNK_DELAY_SECONDS must be non-negative"):
            ChunkingConfig()
    
    def test_validation_batch_size_positive(self):
        """Test that BATCH_SIZE must be positive."""
        os.environ['BATCH_SIZE'] = '0'
        
        with pytest.raises(ValueError, match="BATCH_SIZE must be positive"):
            ChunkingConfig()
    
    def test_partial_environment_override(self):
        """Test that only specified environment variables are overridden."""
        os.environ['CHUNK_SIZE_DAYS'] = '75'
        os.environ['MIN_DAYS_THRESHOLD'] = '3'
        # Leave other variables as defaults
        
        config = ChunkingConfig()
        
        assert config.CHUNK_SIZE_DAYS == 75  # Overridden
        assert config.MIN_DAYS_THRESHOLD == 3  # Overridden
        assert config.MAX_ERROR_COUNT == 3  # Default
        assert config.RECENT_DAYS_THRESHOLD == 100  # Default
        assert config.MAX_LOOKBACK_DAYS == 200  # Default
        assert config.CHUNK_DELAY_SECONDS == 0.1  # Default
        assert config.BATCH_SIZE == 500  # Default
    
    def test_string_to_int_conversion(self):
        """Test that string environment values are properly converted to integers."""
        os.environ['CHUNK_SIZE_DAYS'] = '150'
        os.environ['BATCH_SIZE'] = '750'
        
        config = ChunkingConfig()
        
        assert isinstance(config.CHUNK_SIZE_DAYS, int)
        assert isinstance(config.BATCH_SIZE, int)
        assert config.CHUNK_SIZE_DAYS == 150
        assert config.BATCH_SIZE == 750
    
    def test_string_to_float_conversion(self):
        """Test that string environment values are properly converted to floats."""
        os.environ['CHUNK_DELAY_SECONDS'] = '0.25'
        
        config = ChunkingConfig()
        
        assert isinstance(config.CHUNK_DELAY_SECONDS, float)
        assert config.CHUNK_DELAY_SECONDS == 0.25
    
    def test_invalid_int_conversion(self):
        """Test that invalid integer values raise appropriate errors."""
        os.environ['CHUNK_SIZE_DAYS'] = 'invalid'
        
        with pytest.raises(ValueError):
            ChunkingConfig()
    
    def test_invalid_float_conversion(self):
        """Test that invalid float values raise appropriate errors."""
        os.environ['CHUNK_DELAY_SECONDS'] = 'invalid'
        
        with pytest.raises(ValueError):
            ChunkingConfig()