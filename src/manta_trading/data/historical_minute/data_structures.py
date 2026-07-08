"""
Service Data Structures Module

This module defines data structures used by the Historical Minute Data Service
for tracking acquisition operations, results, and status.

These structures provide:
- Type-safe representation of acquisition operations
- Standardized result reporting
- Progress tracking for batch operations
- Error and warning aggregation
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class AcquisitionStatus(Enum):
    """
    Status of a data acquisition operation.

    Attributes:
        PENDING: Operation not yet started
        IN_PROGRESS: Operation currently running
        COMPLETED: Operation completed successfully
        FAILED: Operation failed with errors
        PARTIALLY_COMPLETED: Operation completed with some failures
    """
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"


@dataclass
class AcquisitionResult:
    """
    Result of a single symbol acquisition operation.

    Attributes:
        symbol: Stock symbol that was acquired
        status: Final status of the acquisition
        rows_written: Number of data rows successfully written to storage
        months_processed: Number of month chunks processed
        errors: List of error messages encountered
        warnings: List of warning messages (non-fatal issues)
        start_time: When the acquisition started
        end_time: When the acquisition ended (None if still running)
    """
    symbol: str
    status: AcquisitionStatus
    rows_written: int
    months_processed: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    start_time: datetime = field(default_factory=lambda: datetime.now())
    end_time: datetime | None = None

    @property
    def duration(self) -> timedelta | None:
        """
        Calculate duration of the acquisition operation.

        Returns:
            timedelta if operation has ended, None if still in progress
        """
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    def __post_init__(self):
        """Validate acquisition result values."""
        if self.rows_written < 0:
            raise ValueError(f"Invalid rows_written: {self.rows_written}. Must be non-negative")
        if self.months_processed < 0:
            raise ValueError(f"Invalid months_processed: {self.months_processed}. Must be non-negative")
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must be after start_time")


@dataclass
class BatchAcquisitionResult:
    """
    Result of a batch acquisition operation (multiple symbols).

    Attributes:
        symbols: List of all symbols in the batch
        results: Dictionary mapping symbol to its AcquisitionResult
        total_symbols: Total number of symbols in batch
        successful: Number of successfully completed symbols
        failed: Number of failed symbols
        start_time: When the batch acquisition started
        end_time: When the batch acquisition ended (None if still running)
    """
    symbols: list[str]
    results: dict[str, AcquisitionResult] = field(default_factory=dict)
    total_symbols: int = 0
    successful: int = 0
    failed: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now())
    end_time: datetime | None = None

    @property
    def success_rate(self) -> float:
        """
        Calculate success rate as a percentage.

        Returns:
            Success rate from 0.0 (0%) to 1.0 (100%)
        """
        if self.total_symbols == 0:
            return 0.0
        return self.successful / self.total_symbols

    @property
    def duration(self) -> timedelta | None:
        """
        Calculate duration of the batch operation.

        Returns:
            timedelta if operation has ended, None if still in progress
        """
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    def __post_init__(self):
        """Validate and initialize batch result values."""
        if self.total_symbols == 0:
            self.total_symbols = len(self.symbols)
        if self.total_symbols < 0:
            raise ValueError(f"Invalid total_symbols: {self.total_symbols}. Must be non-negative")
        if self.successful < 0:
            raise ValueError(f"Invalid successful: {self.successful}. Must be non-negative")
        if self.failed < 0:
            raise ValueError(f"Invalid failed: {self.failed}. Must be non-negative")
        if self.successful + self.failed > self.total_symbols:
            raise ValueError("successful + failed cannot exceed total_symbols")
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must be after start_time")


__all__ = [
    'AcquisitionStatus',
    'AcquisitionResult',
    'BatchAcquisitionResult',
]
