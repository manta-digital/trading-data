"""
Data Service Interface Module

This module defines the IDataService protocol that all data services must implement
for standardized health monitoring, gap detection, and quality reporting.

The protocol enables unified monitoring across all data services (minute, tick, realtime)
and provides a consistent API for Tier 2 (Analysis) components to consume.

Example usage:
    ```python
    from manta_trading.data.base.service_interface import IDataService, HealthMetrics

    class MyDataService:
        def get_health_metrics(self) -> HealthMetrics:
            return HealthMetrics(
                status='healthy',
                error_count=0,
                last_error=None,
                last_update=datetime.now(timezone.utc),
                quality_score=1.0
            )

        def detect_gaps(self, symbol: str, start: datetime, end: datetime) -> list[GapInfo]:
            # Implementation here
            pass

        def get_quality_report(self, symbol: str) -> QualityReport:
            # Implementation here
            pass
    ```
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class HealthMetrics:
    """
    Health metrics for a data service.

    Attributes:
        status: Service health status ('healthy', 'degraded', 'unhealthy')
        error_count: Number of errors in recent time period
        last_error: Description of most recent error, if any
        last_update: Timestamp of last health check update
        quality_score: Overall quality score from 0.0 (worst) to 1.0 (best)
    """
    status: str
    error_count: int
    last_error: str | None
    last_update: datetime
    quality_score: float

    def __post_init__(self):
        """Validate health metrics values."""
        if self.status not in ('healthy', 'degraded', 'unhealthy'):
            raise ValueError(f"Invalid status: {self.status}. Must be 'healthy', 'degraded', or 'unhealthy'")
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError(f"Invalid quality_score: {self.quality_score}. Must be between 0.0 and 1.0")
        if self.error_count < 0:
            raise ValueError(f"Invalid error_count: {self.error_count}. Must be non-negative")


@dataclass
class GapInfo:
    """
    Information about a detected gap in data coverage.

    Attributes:
        symbol: Stock symbol for this gap
        gap_start: Start timestamp of the gap
        gap_end: End timestamp of the gap
        expected_bars: Number of bars expected in this gap
        gap_type: Type of gap ('trading_hours', 'extended_hours', 'full_day')
    """
    symbol: str
    gap_start: datetime
    gap_end: datetime
    expected_bars: int
    gap_type: str

    def __post_init__(self):
        """Validate gap info values."""
        if self.gap_type not in ('trading_hours', 'extended_hours', 'full_day'):
            raise ValueError(
                f"Invalid gap_type: {self.gap_type}. "
                "Must be 'trading_hours', 'extended_hours', or 'full_day'"
            )
        if self.expected_bars < 0:
            raise ValueError(f"Invalid expected_bars: {self.expected_bars}. Must be non-negative")
        if self.gap_start >= self.gap_end:
            raise ValueError("gap_start must be before gap_end")


@dataclass
class QualityReport:
    """
    Data quality report for a symbol.

    Attributes:
        symbol: Stock symbol for this report
        completeness_score: Data completeness (0.0-1.0)
        accuracy_score: Data accuracy (0.0-1.0)
        timeliness_score: Data timeliness (0.0-1.0)
        consistency_score: Data consistency (0.0-1.0)
        last_analyzed: Timestamp when this report was generated
    """
    symbol: str
    completeness_score: float
    accuracy_score: float
    timeliness_score: float
    consistency_score: float
    last_analyzed: datetime

    def __post_init__(self):
        """Validate quality scores."""
        scores = {
            'completeness_score': self.completeness_score,
            'accuracy_score': self.accuracy_score,
            'timeliness_score': self.timeliness_score,
            'consistency_score': self.consistency_score
        }
        for name, score in scores.items():
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"Invalid {name}: {score}. Must be between 0.0 and 1.0")


class IDataService(Protocol):
    """
    Protocol defining the interface that all data services must implement.

    This provides a standardized contract for health monitoring, gap detection,
    and quality reporting across all data service implementations (minute data,
    tick data, realtime data, etc.).

    Using Protocol enables duck typing - any class implementing these methods
    will be considered compatible with IDataService.
    """

    def get_health_metrics(self) -> HealthMetrics:
        """
        Get current health metrics for this data service.

        Returns:
            HealthMetrics with current service status, error count, and quality score

        Example:
            ```python
            metrics = service.get_health_metrics()
            if metrics.status == 'unhealthy':
                print(f"Service unhealthy: {metrics.last_error}")
            ```
        """
        ...

    def detect_gaps(self, symbol: str, start: datetime, end: datetime) -> list[GapInfo]:
        """
        Detect gaps in data coverage for a symbol over a time range.

        Args:
            symbol: Stock symbol to check
            start: Start of time range to check
            end: End of time range to check

        Returns:
            List of GapInfo objects describing detected gaps

        Example:
            ```python
            gaps = service.detect_gaps('AAPL', start_date, end_date)
            for gap in gaps:
                print(f"Gap from {gap.gap_start} to {gap.gap_end}: {gap.expected_bars} bars missing")
            ```
        """
        ...

    def get_quality_report(self, symbol: str) -> QualityReport:
        """
        Get data quality report for a symbol.

        Args:
            symbol: Stock symbol to analyze

        Returns:
            QualityReport with completeness, accuracy, timeliness, and consistency scores

        Example:
            ```python
            report = service.get_quality_report('AAPL')
            print(f"Completeness: {report.completeness_score:.2%}")
            print(f"Accuracy: {report.accuracy_score:.2%}")
            ```
        """
        ...


__all__ = [
    'IDataService',
    'HealthMetrics',
    'GapInfo',
    'QualityReport',
]
