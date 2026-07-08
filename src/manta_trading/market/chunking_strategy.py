"""
Chunking Strategy Interface and Implementations

This module provides the core chunking strategy interface and concrete implementations
for intelligent data fetching based on gap analysis.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional
from manta_trading.logging import get_logger

from .config import ChunkingConfig

_logger = get_logger(__name__)


class FetchStrategy(Enum):
    """Strategy types for data fetching."""
    DEFAULT = "compact"      # 100 days (legacy behavior)
    CHUNKING = "chunking"    # Intelligent chunking
    FULL = "full"           # Complete history


@dataclass
class FetchInstruction:
    """
    Represents a single fetch operation instruction.
    
    Contains all necessary information for executing a data fetch,
    including chunking metadata for multi-chunk operations.
    """
    symbol: str
    output_size: str  # 'compact', 'full'
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    chunk_number: int = 1
    total_chunks: int = 1
    
    def __str__(self) -> str:
        chunk_info = f"({self.chunk_number}/{self.total_chunks})" if self.total_chunks > 1 else ""
        return f"FetchInstruction[{self.symbol}, {self.output_size}{chunk_info}]"


class ChunkingStrategy(ABC):
    """
    Abstract base class for data fetching strategies.
    
    Provides the interface for determining optimal fetch plans based on
    symbol history, data gaps, and configuration parameters.
    """
    
    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()
        _logger.debug("Initialized %s with config: %s", self.__class__.__name__, self.config)
    
    @abstractmethod
    def determine_fetch_plan(self, symbol: str, last_updated: Optional[date]) -> list[FetchInstruction]:
        """
        Determine the optimal fetch plan for a symbol.
        
        Args:
            symbol: The symbol to fetch data for
            last_updated: The last known update date (can be None for new symbols)
            
        Returns:
            list of FetchInstructions to execute. Empty list means no fetch needed.
        """
        pass
    
    def _days_since_update(self, last_updated: Optional[date]) -> int:
        """Calculate days since last update. Returns large number if None."""
        if last_updated is None:
            return 9999  # Large number to trigger appropriate strategy
        return (date.today() - last_updated).days


class DefaultChunkingStrategy(ChunkingStrategy):
    """
    Legacy strategy implementation - maintains existing compact/full logic.
    
    This strategy preserves the original binary decision making:
    - Recent updates (< threshold): compact fetch
    - Old updates (>= threshold): full fetch
    """
    
    def determine_fetch_plan(self, symbol: str, last_updated: Optional[date]) -> list[FetchInstruction]:
        """
        Implements the original binary compact/full logic.
        
        Maintains backward compatibility with existing system behavior.
        """
        days_since_update = self._days_since_update(last_updated)
        
        # Skip if updated too recently
        if days_since_update < self.config.MIN_DAYS_THRESHOLD:
            _logger.debug("Skipping %s: updated %d days ago (< %d)", symbol, days_since_update, self.config.MIN_DAYS_THRESHOLD)
            return []

        # Use original binary logic
        if days_since_update <= self.config.RECENT_DAYS_THRESHOLD:
            output_size = "compact"
        else:
            output_size = "full"  # This is the problematic behavior we're replacing

        _logger.debug("DefaultStrategy for %s: %d days -> %s", symbol, days_since_update, output_size)
        
        return [FetchInstruction(
            symbol=symbol,
            output_size=output_size,
            chunk_number=1,
            total_chunks=1
        )]


class CompactChunkingStrategy(ChunkingStrategy):
    """
    Strategy for handling small gaps efficiently.
    
    Always uses compact fetches (100 days), suitable for symbols
    with recent updates that just need to catch up.
    """
    
    def determine_fetch_plan(self, symbol: str, last_updated: Optional[date]) -> list[FetchInstruction]:
        """
        Simple compact fetch strategy for small gaps.
        
        Used for symbols that need updates but don't require large historical fetches.
        """
        days_since_update = self._days_since_update(last_updated)
        
        # Skip if updated too recently
        if days_since_update < self.config.MIN_DAYS_THRESHOLD:
            _logger.debug("Skipping %s: updated %d days ago (< %d)", symbol, days_since_update, self.config.MIN_DAYS_THRESHOLD)
            return []

        _logger.debug("CompactStrategy for %s: %d days -> compact", symbol, days_since_update)
        
        return [FetchInstruction(
            symbol=symbol,
            output_size="compact",
            chunk_number=1,
            total_chunks=1
        )]


class IntelligentChunkingStrategy(ChunkingStrategy):
    """
    Smart chunking strategy that analyzes gaps and determines optimal fetch approach.
    
    This is the core strategy that replaces the problematic binary compact/full logic
    with intelligent gap-based decisions:
    - Small gaps: single compact fetch
    - Medium gaps: multiple chunked compact fetches
    - Large/unknown gaps: full fetch
    - New symbols: full fetch
    """
    
    def __init__(self, config: Optional[ChunkingConfig] = None):
        super().__init__(config)
        self.gap_analyzer = None  # Will be set by factory when needed
    
    def set_gap_analyzer(self, gap_analyzer):
        """Set the gap analyzer for data coverage analysis."""
        self.gap_analyzer = gap_analyzer
    
    def determine_fetch_plan(self, symbol: str, last_updated: Optional[date]) -> list[FetchInstruction]:
        """
        Create intelligent fetch plan based on gap analysis and thresholds.
        
        Strategy Logic:
        1. Skip if updated within MIN_DAYS_THRESHOLD
        2. Use single compact if gap <= RECENT_DAYS_THRESHOLD  
        3. Use chunking if gap > RECENT_DAYS_THRESHOLD but reasonable
        4. Use full fetch for new symbols or very large gaps
        """
        days_since_update = self._days_since_update(last_updated)
        
        # Case 1: Skip if updated too recently
        if days_since_update < self.config.MIN_DAYS_THRESHOLD:
            _logger.debug("Skipping %s: updated %d days ago (< %d)", symbol, days_since_update, self.config.MIN_DAYS_THRESHOLD)
            return []

        # Case 2: New symbol or very old update - use full fetch
        if last_updated is None or days_since_update > (self.config.CHUNK_SIZE_DAYS * 20):  # > 2000 days
            _logger.debug("Full fetch for %s: %s", symbol, "new symbol" if last_updated is None else f"{days_since_update} days old")
            return [FetchInstruction(
                symbol=symbol,
                output_size="full",
                chunk_number=1,
                total_chunks=1
            )]
        
        # Case 3: Small gap - single compact fetch
        if days_since_update <= self.config.RECENT_DAYS_THRESHOLD:
            _logger.debug("Compact fetch for %s: %d days gap", symbol, days_since_update)
            return [FetchInstruction(
                symbol=symbol,
                output_size="compact",
                chunk_number=1,
                total_chunks=1
            )]
        
        # Case 4: Medium gap - chunked fetching
        _logger.debug("Chunked fetch for %s: %d days gap", symbol, days_since_update)
        return self._create_chunk_plan(symbol, days_since_update)
    
    def _create_chunk_plan(self, symbol: str, days_gap: int) -> list[FetchInstruction]:
        """
        Create chunked fetching plan for medium-sized gaps.
        
        Calculates the optimal number of chunks needed and creates
        individual fetch instructions for each chunk.
        """
        # Calculate number of chunks needed (round up)
        chunks_needed = (days_gap + self.config.CHUNK_SIZE_DAYS - 1) // self.config.CHUNK_SIZE_DAYS
        
        # Limit maximum chunks to prevent excessive API usage
        max_chunks = 10  # Reasonable limit for chunked operations
        if chunks_needed > max_chunks:
            _logger.warning("Gap too large for chunking (%d chunks), using full fetch for %s", chunks_needed, symbol)
            return [FetchInstruction(
                symbol=symbol,
                output_size="full",
                chunk_number=1,
                total_chunks=1
            )]
        
        # Create chunk instructions
        instructions = []
        for chunk_num in range(1, chunks_needed + 1):
            instructions.append(FetchInstruction(
                symbol=symbol,
                output_size="compact",  # Each chunk is a compact fetch
                chunk_number=chunk_num,
                total_chunks=chunks_needed
            ))
        
        _logger.debug("Created %d chunk plan for %s (%d day gap)", chunks_needed, symbol, days_gap)
        return instructions


class ChunkingStrategyFactory:
    """
    Factory class for creating and managing chunking strategies.
    
    Provides a centralized way to create strategy instances with proper
    configuration and dependencies.
    """
    
    @staticmethod
    def create_strategy(strategy_type: FetchStrategy, 
                       config: Optional[ChunkingConfig] = None,
                       gap_analyzer=None) -> ChunkingStrategy:
        """
        Create a chunking strategy instance.
        
        Args:
            strategy_type: The type of strategy to create
            config: Configuration object (creates default if None)
            gap_analyzer: Gap analyzer for intelligent strategies (optional)
            
        Returns:
            Configured ChunkingStrategy instance
        """
        if config is None:
            config = ChunkingConfig()
        
        if strategy_type == FetchStrategy.DEFAULT:
            return DefaultChunkingStrategy(config)
        elif strategy_type == FetchStrategy.CHUNKING:
            strategy = IntelligentChunkingStrategy(config)
            if gap_analyzer:
                strategy.set_gap_analyzer(gap_analyzer)
            return strategy
        elif strategy_type == FetchStrategy.FULL:
            # For full strategy, we can use a simple compact strategy that forces full
            class FullFetchStrategy(ChunkingStrategy):
                def determine_fetch_plan(self, symbol: str, last_updated: Optional[date]) -> list[FetchInstruction]:
                    return [FetchInstruction(symbol=symbol, output_size="full")]
            return FullFetchStrategy(config)
        else:
            raise ValueError(f"Unknown strategy type: {strategy_type}")
    
    @staticmethod
    def get_compact_strategy(config: Optional[ChunkingConfig] = None) -> ChunkingStrategy:
        """Convenience method for creating compact-only strategy."""
        return CompactChunkingStrategy(config)
    
    @staticmethod
    def get_intelligent_strategy(config: Optional[ChunkingConfig] = None, 
                                gap_analyzer=None) -> ChunkingStrategy:
        """Convenience method for creating intelligent chunking strategy."""
        strategy = IntelligentChunkingStrategy(config)
        if gap_analyzer:
            strategy.set_gap_analyzer(gap_analyzer)
        return strategy