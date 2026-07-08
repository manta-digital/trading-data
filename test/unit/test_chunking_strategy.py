"""
Unit tests for ChunkingStrategy implementations

Tests all chunking strategy classes including:
- FetchInstruction data class
- Abstract ChunkingStrategy base class
- DefaultChunkingStrategy (legacy behavior)
- CompactChunkingStrategy (small gaps)
- IntelligentChunkingStrategy (gap-aware chunking)
- ChunkingStrategyFactory
"""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock

from manta_trading.market.chunking_strategy import (
    FetchStrategy,
    FetchInstruction,
    ChunkingStrategy,
    DefaultChunkingStrategy,
    CompactChunkingStrategy,
    IntelligentChunkingStrategy,
    ChunkingStrategyFactory
)
from manta_trading.market.config import ChunkingConfig


class TestFetchInstruction:
    """Test cases for FetchInstruction data class."""
    
    def test_fetch_instruction_creation(self):
        """Test basic FetchInstruction creation with default values."""
        instruction = FetchInstruction(symbol="AAPL", output_size="compact")
        
        assert instruction.symbol == "AAPL"
        assert instruction.output_size == "compact"
        assert instruction.start_date is None
        assert instruction.end_date is None
        assert instruction.chunk_number == 1
        assert instruction.total_chunks == 1
    
    def test_fetch_instruction_full_specification(self):
        """Test FetchInstruction creation with all parameters."""
        start_date = date(2024, 1, 1)
        end_date = date(2024, 3, 31)
        
        instruction = FetchInstruction(
            symbol="TSLA",
            output_size="full",
            start_date=start_date,
            end_date=end_date,
            chunk_number=2,
            total_chunks=5
        )
        
        assert instruction.symbol == "TSLA"
        assert instruction.output_size == "full"
        assert instruction.start_date == start_date
        assert instruction.end_date == end_date
        assert instruction.chunk_number == 2
        assert instruction.total_chunks == 5
    
    def test_fetch_instruction_string_representation(self):
        """Test string representation for single and multi-chunk instructions."""
        single_chunk = FetchInstruction(symbol="AAPL", output_size="compact")
        assert str(single_chunk) == "FetchInstruction[AAPL, compact]"
        
        multi_chunk = FetchInstruction(symbol="TSLA", output_size="compact", chunk_number=2, total_chunks=3)
        assert str(multi_chunk) == "FetchInstruction[TSLA, compact(2/3)]"


class TestDefaultChunkingStrategy:
    """Test cases for DefaultChunkingStrategy (legacy behavior)."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.config = ChunkingConfig()
        self.strategy = DefaultChunkingStrategy(self.config)
        self.today = date.today()
    
    def test_skip_recent_updates(self):
        """Test that recent updates are skipped."""
        recent_date = self.today - timedelta(days=1)  # Within MIN_DAYS_THRESHOLD
        
        plan = self.strategy.determine_fetch_plan("AAPL", recent_date)
        assert plan == []
    
    def test_compact_for_small_gaps(self):
        """Test compact fetch for gaps within RECENT_DAYS_THRESHOLD."""
        gap_date = self.today - timedelta(days=50)  # Within 100-day threshold
        
        plan = self.strategy.determine_fetch_plan("AAPL", gap_date)
        
        assert len(plan) == 1
        assert plan[0].symbol == "AAPL"
        assert plan[0].output_size == "compact"
        assert plan[0].chunk_number == 1
        assert plan[0].total_chunks == 1
    
    def test_full_for_large_gaps(self):
        """Test full fetch for gaps exceeding RECENT_DAYS_THRESHOLD."""
        gap_date = self.today - timedelta(days=150)  # Beyond 100-day threshold
        
        plan = self.strategy.determine_fetch_plan("AAPL", gap_date)
        
        assert len(plan) == 1
        assert plan[0].symbol == "AAPL"
        assert plan[0].output_size == "full"
        assert plan[0].chunk_number == 1
        assert plan[0].total_chunks == 1
    
    def test_new_symbol_handling(self):
        """Test behavior for symbols with no last update date."""
        plan = self.strategy.determine_fetch_plan("NEWSTOCK", None)
        
        assert len(plan) == 1
        assert plan[0].symbol == "NEWSTOCK"
        assert plan[0].output_size == "full"
    
    def test_custom_config_parameters(self):
        """Test strategy behavior with custom configuration."""
        custom_config = ChunkingConfig()
        custom_config.MIN_DAYS_THRESHOLD = 5
        custom_config.RECENT_DAYS_THRESHOLD = 50
        
        strategy = DefaultChunkingStrategy(custom_config)
        
        # Test with gap that should now use full fetch (custom threshold)
        gap_date = self.today - timedelta(days=60)
        plan = strategy.determine_fetch_plan("AAPL", gap_date)
        
        assert len(plan) == 1
        assert plan[0].output_size == "full"


class TestCompactChunkingStrategy:
    """Test cases for CompactChunkingStrategy."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.config = ChunkingConfig()
        self.strategy = CompactChunkingStrategy(self.config)
        self.today = date.today()
    
    def test_skip_recent_updates(self):
        """Test that recent updates are skipped."""
        recent_date = self.today - timedelta(days=1)
        
        plan = self.strategy.determine_fetch_plan("AAPL", recent_date)
        assert plan == []
    
    def test_always_compact_for_valid_gaps(self):
        """Test that strategy always uses compact for any valid gap."""
        test_cases = [
            timedelta(days=10),   # Small gap
            timedelta(days=50),   # Medium gap
            timedelta(days=150),  # Large gap
            timedelta(days=500)   # Very large gap
        ]
        
        for gap in test_cases:
            gap_date = self.today - gap
            plan = self.strategy.determine_fetch_plan("AAPL", gap_date)
            
            assert len(plan) == 1
            assert plan[0].output_size == "compact"
            assert plan[0].chunk_number == 1
            assert plan[0].total_chunks == 1
    
    def test_new_symbol_handling(self):
        """Test compact strategy for new symbols."""
        plan = self.strategy.determine_fetch_plan("NEWSTOCK", None)
        
        assert len(plan) == 1
        assert plan[0].output_size == "compact"


class TestIntelligentChunkingStrategy:
    """Test cases for IntelligentChunkingStrategy (core logic)."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.config = ChunkingConfig()
        self.strategy = IntelligentChunkingStrategy(self.config)
        self.today = date.today()
    
    def test_skip_recent_updates(self):
        """Test that recent updates are skipped."""
        recent_date = self.today - timedelta(days=1)
        
        plan = self.strategy.determine_fetch_plan("AAPL", recent_date)
        assert plan == []
    
    def test_new_symbol_full_fetch(self):
        """Test that new symbols get full fetch."""
        plan = self.strategy.determine_fetch_plan("NEWSTOCK", None)
        
        assert len(plan) == 1
        assert plan[0].symbol == "NEWSTOCK"
        assert plan[0].output_size == "full"
    
    def test_very_old_symbol_full_fetch(self):
        """Test that very old symbols get full fetch."""
        very_old_date = self.today - timedelta(days=2100)  # > 2000 days
        
        plan = self.strategy.determine_fetch_plan("OLDSTOCK", very_old_date)
        
        assert len(plan) == 1
        assert plan[0].output_size == "full"
    
    def test_small_gap_compact_fetch(self):
        """Test that small gaps use single compact fetch."""
        small_gap_date = self.today - timedelta(days=50)  # Within RECENT_DAYS_THRESHOLD
        
        plan = self.strategy.determine_fetch_plan("AAPL", small_gap_date)
        
        assert len(plan) == 1
        assert plan[0].output_size == "compact"
        assert plan[0].chunk_number == 1
        assert plan[0].total_chunks == 1
    
    def test_medium_gap_chunked_fetch(self):
        """Test that medium gaps use chunked fetching."""
        medium_gap_date = self.today - timedelta(days=250)  # Requires 3 chunks (250/100 = 2.5 -> 3)
        
        plan = self.strategy.determine_fetch_plan("AAPL", medium_gap_date)
        
        assert len(plan) == 3  # 250 days / 100 = 3 chunks
        
        # Verify all chunks are properly configured
        for i, instruction in enumerate(plan, 1):
            assert instruction.symbol == "AAPL"
            assert instruction.output_size == "compact"
            assert instruction.chunk_number == i
            assert instruction.total_chunks == 3
    
    def test_chunk_calculation_accuracy(self):
        """Test chunk calculation for various gap sizes."""
        test_cases = [
            (101, 2),   # Just over 100 days -> 2 chunks
            (200, 2),   # Exactly 200 days -> 2 chunks
            (201, 3),   # Just over 200 days -> 3 chunks
            (300, 3),   # 300 days -> 3 chunks
            (350, 4),   # 350 days -> 4 chunks
        ]
        
        for gap_days, expected_chunks in test_cases:
            gap_date = self.today - timedelta(days=gap_days)
            plan = self.strategy.determine_fetch_plan("TEST", gap_date)
            
            assert len(plan) == expected_chunks, f"Gap of {gap_days} days should produce {expected_chunks} chunks"
            
            # Verify chunk metadata
            for i, instruction in enumerate(plan, 1):
                assert instruction.chunk_number == i
                assert instruction.total_chunks == expected_chunks
    
    def test_excessive_gap_protection(self):
        """Test that excessively large gaps fall back to full fetch."""
        huge_gap_date = self.today - timedelta(days=1100)  # Would require 11 chunks -> falls back to full
        
        plan = self.strategy.determine_fetch_plan("HUGESTOCK", huge_gap_date)
        
        assert len(plan) == 1
        assert plan[0].output_size == "full"
    
    def test_custom_chunk_size_configuration(self):
        """Test strategy behavior with custom chunk size."""
        custom_config = ChunkingConfig()
        custom_config.CHUNK_SIZE_DAYS = 50  # Smaller chunks
        custom_config.RECENT_DAYS_THRESHOLD = 60
        
        strategy = IntelligentChunkingStrategy(custom_config)
        
        # 150 days with 50-day chunks should produce 3 chunks
        gap_date = self.today - timedelta(days=150)
        plan = strategy.determine_fetch_plan("AAPL", gap_date)
        
        assert len(plan) == 3  # 150 / 50 = 3 chunks
    
    def test_gap_analyzer_integration(self):
        """Test that gap analyzer can be set and accessed."""
        mock_analyzer = Mock()
        
        self.strategy.set_gap_analyzer(mock_analyzer)
        assert self.strategy.gap_analyzer == mock_analyzer


class TestChunkingStrategyFactory:
    """Test cases for ChunkingStrategyFactory."""
    
    def test_create_default_strategy(self):
        """Test creation of default strategy."""
        strategy = ChunkingStrategyFactory.create_strategy(FetchStrategy.DEFAULT)
        
        assert isinstance(strategy, DefaultChunkingStrategy)
        assert hasattr(strategy, 'config')
    
    def test_create_chunking_strategy(self):
        """Test creation of intelligent chunking strategy."""
        strategy = ChunkingStrategyFactory.create_strategy(FetchStrategy.CHUNKING)
        
        assert isinstance(strategy, IntelligentChunkingStrategy)
    
    def test_create_full_strategy(self):
        """Test creation of full fetch strategy."""
        strategy = ChunkingStrategyFactory.create_strategy(FetchStrategy.FULL)
        
        assert isinstance(strategy, ChunkingStrategy)
        # Test that it returns full fetch instructions
        plan = strategy.determine_fetch_plan("TEST", date.today())
        assert len(plan) == 1
        assert plan[0].output_size == "full"
    
    def test_invalid_strategy_type(self):
        """Test handling of invalid strategy types."""
        with pytest.raises(ValueError, match="Unknown strategy type"):
            ChunkingStrategyFactory.create_strategy("invalid_strategy")
    
    def test_custom_config_injection(self):
        """Test that custom config is properly injected."""
        custom_config = ChunkingConfig()
        custom_config.CHUNK_SIZE_DAYS = 200
        
        strategy = ChunkingStrategyFactory.create_strategy(FetchStrategy.CHUNKING, custom_config)
        
        assert strategy.config.CHUNK_SIZE_DAYS == 200
    
    def test_gap_analyzer_injection(self):
        """Test that gap analyzer is properly set for intelligent strategies."""
        mock_analyzer = Mock()
        
        strategy = ChunkingStrategyFactory.create_strategy(
            FetchStrategy.CHUNKING, 
            gap_analyzer=mock_analyzer
        )
        
        assert isinstance(strategy, IntelligentChunkingStrategy)
        assert strategy.gap_analyzer == mock_analyzer
    
    def test_convenience_methods(self):
        """Test factory convenience methods."""
        # Test compact strategy convenience method
        compact_strategy = ChunkingStrategyFactory.get_compact_strategy()
        assert isinstance(compact_strategy, CompactChunkingStrategy)
        
        # Test intelligent strategy convenience method
        intelligent_strategy = ChunkingStrategyFactory.get_intelligent_strategy()
        assert isinstance(intelligent_strategy, IntelligentChunkingStrategy)
        
        # Test intelligent strategy with gap analyzer
        mock_analyzer = Mock()
        intelligent_with_analyzer = ChunkingStrategyFactory.get_intelligent_strategy(
            gap_analyzer=mock_analyzer
        )
        assert intelligent_with_analyzer.gap_analyzer == mock_analyzer


class TestChunkingStrategyEdgeCases:
    """Test edge cases and boundary conditions across all strategies."""
    
    def test_exactly_threshold_boundaries(self):
        """Test behavior exactly at threshold boundaries."""
        config = ChunkingConfig()
        strategy = IntelligentChunkingStrategy(config)
        today = date.today()
        
        # Test exactly at MIN_DAYS_THRESHOLD
        boundary_date = today - timedelta(days=config.MIN_DAYS_THRESHOLD)
        plan = strategy.determine_fetch_plan("TEST", boundary_date)
        assert len(plan) == 1  # Should not be skipped
        
        # Test exactly at RECENT_DAYS_THRESHOLD
        boundary_date = today - timedelta(days=config.RECENT_DAYS_THRESHOLD)
        plan = strategy.determine_fetch_plan("TEST", boundary_date)
        assert len(plan) == 1
        assert plan[0].output_size == "compact"
        
        # Test just over RECENT_DAYS_THRESHOLD
        boundary_date = today - timedelta(days=config.RECENT_DAYS_THRESHOLD + 1)
        plan = strategy.determine_fetch_plan("TEST", boundary_date)
        assert len(plan) == 2  # Should start chunking
    
    def test_weekend_and_holiday_considerations(self):
        """Test that strategies handle weekends and holidays reasonably."""
        config = ChunkingConfig()
        strategy = IntelligentChunkingStrategy(config)
        
        # Test with weekend dates
        saturday = date(2024, 8, 17)  # A Saturday
        sunday = date(2024, 8, 18)    # A Sunday
        
        for weekend_date in [saturday, sunday]:
            plan = strategy.determine_fetch_plan("WEEKEND", weekend_date)
            # Should still produce valid plans regardless of weekend
            assert isinstance(plan, list)
    
    def test_leap_year_calculations(self):
        """Test that date calculations work correctly with leap years."""
        config = ChunkingConfig()
        strategy = IntelligentChunkingStrategy(config)
        
        # Test with leap year dates
        leap_day = date(2024, 2, 29)  # 2024 is a leap year
        plan = strategy.determine_fetch_plan("LEAP", leap_day)
        
        assert isinstance(plan, list)
        # Should handle leap year dates without errors
    
    def test_strategy_logging_coverage(self):
        """Test that strategies log decisions appropriately."""
        # This test ensures logging statements don't cause errors
        # In a real environment, you might capture and verify log messages
        
        strategies = [
            DefaultChunkingStrategy(),
            CompactChunkingStrategy(),
            IntelligentChunkingStrategy()
        ]
        
        today = date.today()
        test_dates = [
            today - timedelta(days=1),    # Recent
            today - timedelta(days=50),   # Small gap
            today - timedelta(days=200),  # Medium gap
            None                          # New symbol
        ]
        
        for strategy in strategies:
            for test_date in test_dates:
                # Should not raise exceptions during logging
                plan = strategy.determine_fetch_plan("LOGTEST", test_date)
                assert isinstance(plan, list)


# Integration test fixtures for use with actual database testing
@pytest.fixture
def test_config():
    """Create test configuration with smaller values for faster testing."""
    config = ChunkingConfig()
    config.CHUNK_SIZE_DAYS = 10          # Smaller for faster testing
    config.MIN_DAYS_THRESHOLD = 1        # Faster test cycles  
    config.RECENT_DAYS_THRESHOLD = 20    # Lower threshold for testing
    config.MAX_LOOKBACK_DAYS = 50        # Smaller lookback for testing
    return config


@pytest.fixture
def mock_gap_analyzer():
    """Create mock gap analyzer for testing."""
    analyzer = Mock()
    analyzer.analyze_data_gaps.return_value = Mock(
        earliest_date=date(2020, 1, 1),
        latest_date=date(2024, 1, 1),
        gaps=[],
        is_continuous=True
    )
    return analyzer