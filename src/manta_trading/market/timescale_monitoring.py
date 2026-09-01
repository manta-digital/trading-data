"""
TimescaleDB Monitoring and Alerting Integration
Provides performance metrics logging and operational alerts for TimescaleDB operations
"""

import time
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
from manta_trading.logging import get_logger
import json

_logger = get_logger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for TimescaleDB operations"""

    operation: str
    symbol: str
    start_time: float
    end_time: float
    duration_seconds: float
    rows_processed: int
    rows_per_second: float
    memory_usage_mb: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging"""
        return asdict(self)


@dataclass
class SystemAlert:
    """System alert for monitoring"""

    alert_type: str
    severity: str  # 'info', 'warning', 'error', 'critical'
    message: str
    details: dict[str, Any]
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging"""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


class TimescaleMonitor:
    """
    Monitoring and alerting system for TimescaleDB operations
    Integrates with structured logging for operational visibility
    """

    def __init__(
        self, enable_performance_logging: bool = True, enable_alerts: bool = True
    ):
        """
        Initialize monitoring system

        Args:
            enable_performance_logging: Enable detailed performance metrics logging
            enable_alerts: Enable alert generation for system issues
        """
        self.enable_performance_logging = enable_performance_logging
        self.enable_alerts = enable_alerts
        self.performance_thresholds = {
            "bulk_write_min_rows_per_sec": 10000.0,  # Alert if bulk writes are slower
            "query_max_duration_sec": 30.0,  # Alert if queries take too long
            "connection_timeout_sec": 10.0,  # Alert if connections take too long
        }

        _logger.info(
            "TimescaleMonitor initialized with performance_logging=%s, alerts=%s",
            enable_performance_logging,
            enable_alerts,
        )

    @asynccontextmanager
    async def monitor_operation(self, operation: str, symbol: str = "N/A"):
        """
        Context manager to monitor database operations with automatic metrics collection

        Args:
            operation: Operation name (e.g., 'bulk_write', 'query', 'aggregation')
            symbol: Symbol being processed

        Usage:
            async with monitor.monitor_operation('bulk_write', 'TSLA') as metrics:
                # Perform database operation
                metrics.rows_processed = 1000
        """
        start_time = time.perf_counter()
        metrics = PerformanceMetrics(
            operation=operation,
            symbol=symbol,
            start_time=start_time,
            end_time=0,
            duration_seconds=0,
            rows_processed=0,
            rows_per_second=0,
        )

        try:
            yield metrics

        except Exception as e:
            metrics.error = str(e)
            if self.enable_alerts:
                await self._generate_alert(
                    "operation_error",
                    "error",
                    f"Operation {operation} failed for {symbol}",
                    {"operation": operation, "symbol": symbol, "error": str(e)},
                )
            raise

        finally:
            # Calculate final metrics
            end_time = time.perf_counter()
            metrics.end_time = end_time
            metrics.duration_seconds = end_time - start_time

            if metrics.rows_processed > 0 and metrics.duration_seconds > 0:
                metrics.rows_per_second = (
                    metrics.rows_processed / metrics.duration_seconds
                )

            # Log performance metrics
            if self.enable_performance_logging:
                await self._log_performance_metrics(metrics)

            # Check for performance alerts
            if self.enable_alerts:
                await self._check_performance_alerts(metrics)

    async def _log_performance_metrics(self, metrics: PerformanceMetrics):
        """Log performance metrics in structured format"""
        log_data = {"event_type": "timescale_performance", "metrics": metrics.to_dict()}

        if metrics.error:
            _logger.error(
                "TimescaleDB operation failed: %s", json.dumps(log_data, indent=2)
            )
        elif metrics.duration_seconds > 10:  # Log slow operations as warnings
            _logger.warning(
                "TimescaleDB slow operation: %s", json.dumps(log_data, indent=2)
            )
        else:
            _logger.info(
                "TimescaleDB operation completed: %s in %.3fs (%d rows, %.0f rows/s)",
                metrics.operation,
                metrics.duration_seconds,
                metrics.rows_processed,
                metrics.rows_per_second,
            )

    async def _check_performance_alerts(self, metrics: PerformanceMetrics):
        """Check performance metrics against thresholds and generate alerts"""
        # Check bulk write performance
        if (
            metrics.operation == "bulk_write"
            and metrics.rows_per_second > 0
            and metrics.rows_per_second
            < self.performance_thresholds["bulk_write_min_rows_per_sec"]
        ):
            await self._generate_alert(
                "bulk_write_performance",
                "warning",
                f"Bulk write performance below threshold: {metrics.rows_per_second:.0f} rows/s",
                {
                    "symbol": metrics.symbol,
                    "actual_performance": metrics.rows_per_second,
                    "threshold": self.performance_thresholds[
                        "bulk_write_min_rows_per_sec"
                    ],
                    "duration": metrics.duration_seconds,
                },
            )

        # Check query duration
        if (
            metrics.operation in ["query", "aggregation"]
            and metrics.duration_seconds
            > self.performance_thresholds["query_max_duration_sec"]
        ):
            await self._generate_alert(
                "query_performance",
                "warning",
                f"Query duration exceeded threshold: {metrics.duration_seconds:.1f}s",
                {
                    "symbol": metrics.symbol,
                    "operation": metrics.operation,
                    "duration": metrics.duration_seconds,
                    "threshold": self.performance_thresholds["query_max_duration_sec"],
                },
            )

    async def _generate_alert(
        self, alert_type: str, severity: str, message: str, details: dict[str, Any]
    ):
        """Generate and log system alert"""
        alert = SystemAlert(
            alert_type=alert_type,
            severity=severity,
            message=message,
            details=details,
            timestamp=datetime.now(timezone.utc),
        )

        log_data = {"event_type": "timescale_alert", "alert": alert.to_dict()}

        if severity == "critical":
            _logger.critical(
                "TimescaleDB CRITICAL ALERT: %s", json.dumps(log_data, indent=2)
            )
        elif severity == "error":
            _logger.error("TimescaleDB ERROR ALERT: %s", json.dumps(log_data, indent=2))
        elif severity == "warning":
            _logger.warning(
                "TimescaleDB WARNING ALERT: %s", json.dumps(log_data, indent=2)
            )
        else:
            _logger.info("TimescaleDB INFO ALERT: %s", json.dumps(log_data, indent=2))

    async def log_system_health(self, health_data: dict[str, Any]):
        """Log system health metrics in structured format"""
        log_data = {
            "event_type": "timescale_system_health",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health": health_data,
        }

        overall_status = health_data.get("overall_status", "unknown")

        if overall_status == "error":
            _logger.error(
                "TimescaleDB system health check failed: %s",
                json.dumps(log_data, indent=2),
            )
        elif overall_status == "unhealthy":
            _logger.warning(
                "TimescaleDB system health issues detected: %s",
                json.dumps(log_data, indent=2),
            )
        else:
            _logger.info(
                "TimescaleDB system health check: status=%s, symbols=%s, total_rows=%s",
                overall_status,
                health_data.get("coverage_summary", {}).get("symbols_tracked", 0),
                health_data.get("coverage_summary", {}).get("total_rows", 0),
            )

    async def log_compression_event(
        self,
        symbol: str,
        before_size: int,
        after_size: int,
        compression_ratio: float,
        duration: float,
    ):
        """Log compression events for monitoring"""
        savings_mb = (before_size - after_size) / (1024 * 1024)

        log_data = {
            "event_type": "timescale_compression",
            "symbol": symbol,
            "before_size_bytes": before_size,
            "after_size_bytes": after_size,
            "savings_mb": savings_mb,
            "compression_ratio": compression_ratio,
            "duration_seconds": duration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        _logger.info(
            "TimescaleDB compression completed for %s: %.1f%% savings (%.1fMB) in %.1fs",
            symbol,
            compression_ratio * 100,
            savings_mb,
            duration,
        )

        # Alert if compression fails or is ineffective
        if compression_ratio < 0.5:  # Less than 50% compression
            await self._generate_alert(
                "compression_efficiency",
                "warning",
                f"Low compression efficiency for {symbol}: {compression_ratio:.1%}",
                log_data,
            )

    def set_performance_threshold(self, metric: str, value: float):
        """Update performance threshold for monitoring"""
        if metric in self.performance_thresholds:
            old_value = self.performance_thresholds[metric]
            self.performance_thresholds[metric] = value
            _logger.info(
                "Updated performance threshold: %s=%s (was %s)",
                metric,
                value,
                old_value,
            )
        else:
            _logger.warning("Unknown performance threshold: %s", metric)

    def get_monitoring_summary(self) -> dict[str, Any]:
        """Get monitoring system configuration summary"""
        return {
            "performance_logging_enabled": self.enable_performance_logging,
            "alerts_enabled": self.enable_alerts,
            "performance_thresholds": self.performance_thresholds.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Global monitor instance for easy access
_global_monitor: Optional[TimescaleMonitor] = None


def get_monitor() -> TimescaleMonitor:
    """Get or create global TimescaleDB monitor instance"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = TimescaleMonitor()
    return _global_monitor


def configure_monitor(
    enable_performance_logging: bool = True, enable_alerts: bool = True
):
    """Configure global TimescaleDB monitor"""
    global _global_monitor
    _global_monitor = TimescaleMonitor(enable_performance_logging, enable_alerts)
    return _global_monitor
