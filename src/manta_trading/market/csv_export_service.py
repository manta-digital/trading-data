"""
CSV Export Service for TimescaleDB Minute Data
Based on: project-documents/private/features/02-lld.minute-data.final.md
Purpose: Export TimescaleDB data to various CSV formats
"""

import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from manta_trading.logging import get_logger

from .timescale_minute_db import TimescaleMinuteDataDB

_logger = get_logger(__name__)


class CSVExportService:
    """CSV export service for TimescaleDB data with multiple format support"""

    def __init__(
        self, db: TimescaleMinuteDataDB, export_base_dir: str = "/data/exports"
    ):
        """
        Initialize CSV export service

        Args:
            db: TimescaleMinuteDataDB instance
            export_base_dir: Base directory for exports
        """
        self.db = db
        self.export_base_dir = Path(export_base_dir)
        self.export_base_dir.mkdir(parents=True, exist_ok=True)
        _logger.info(
            "CSV export service initialized with base directory: %s", export_base_dir
        )

    async def export_symbol_to_csv(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        export_format: str = "daily",
    ) -> Dict:
        """
        Export symbol data to CSV files with various formats

        Args:
            symbol: Stock symbol to export
            start_date: Start date for export range
            end_date: End date for export range
            export_format: Format type ('daily', 'monthly', 'single')

        Returns:
            Dict with export statistics and results
        """
        try:
            export_stats = {"files_created": 0, "total_rows": 0, "export_time": 0}
            start_time = time.perf_counter()

            symbol_dir = self.export_base_dir / symbol
            symbol_dir.mkdir(exist_ok=True)

            if export_format == "daily":
                await self._export_daily_files(
                    symbol, start_date, end_date, symbol_dir, export_stats
                )
            elif export_format == "monthly":
                await self._export_monthly_files(
                    symbol, start_date, end_date, symbol_dir, export_stats
                )
            elif export_format == "single":
                await self._export_single_file(
                    symbol, start_date, end_date, symbol_dir, export_stats
                )
            else:
                raise ValueError(f"Unknown export format: {export_format}")

            export_stats["export_time"] = time.perf_counter() - start_time

            _logger.info("CSV export completed for %s: %s", symbol, export_stats)

            return export_stats

        except Exception as e:
            _logger.error("CSV export failed for %s: %s", symbol, e)
            return {"error": str(e)}

    async def _export_daily_files(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        symbol_dir: Path,
        stats: Dict,
    ):
        """Export as daily CSV files (one file per day)"""

        daily_dir = symbol_dir / "daily"
        daily_dir.mkdir(exist_ok=True)

        # Generate date range
        current_date = start_date.date()
        end_date_only = end_date.date()

        while current_date <= end_date_only:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())

            # Get data for this day
            day_data = await self.db.get_minute_data(symbol, day_start, day_end)

            if not day_data.empty:
                file_path = daily_dir / f"{current_date}.csv"
                day_data.to_csv(file_path, float_format="%.4f")

                stats["files_created"] += 1
                stats["total_rows"] += len(day_data)

                _logger.debug(
                    "Exported %d rows for %s on %s", len(day_data), symbol, current_date
                )

            current_date += timedelta(days=1)

    async def _export_monthly_files(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        symbol_dir: Path,
        stats: Dict,
    ):
        """Export as monthly compressed CSV files"""

        monthly_dir = symbol_dir / "monthly"
        monthly_dir.mkdir(exist_ok=True)

        # Group by month
        current_month = start_date.replace(day=1)

        while current_month <= end_date:
            # Calculate month end
            if current_month.month == 12:
                month_end = current_month.replace(
                    year=current_month.year + 1, month=1
                ) - timedelta(days=1)
            else:
                month_end = current_month.replace(
                    month=current_month.month + 1
                ) - timedelta(days=1)

            month_end = min(month_end, end_date)

            # Get data for this month
            month_data = await self.db.get_minute_data(symbol, current_month, month_end)

            if not month_data.empty:
                file_path = monthly_dir / f"{current_month.strftime('%Y-%m')}.csv.gz"
                month_data.to_csv(file_path, float_format="%.4f", compression="gzip")

                stats["files_created"] += 1
                stats["total_rows"] += len(month_data)

                _logger.debug(
                    "Exported %d rows for %s in %s",
                    len(month_data),
                    symbol,
                    current_month.strftime("%Y-%m"),
                )

            # Move to next month
            if current_month.month == 12:
                current_month = current_month.replace(
                    year=current_month.year + 1, month=1
                )
            else:
                current_month = current_month.replace(month=current_month.month + 1)

    async def _export_single_file(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        symbol_dir: Path,
        stats: Dict,
    ):
        """Export as single large file"""

        data = await self.db.get_minute_data(symbol, start_date, end_date)

        if not data.empty:
            file_path = (
                symbol_dir
                / f"{symbol}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
            )
            data.to_csv(file_path, float_format="%.4f")

            stats["files_created"] = 1
            stats["total_rows"] = len(data)

            _logger.debug("Exported %d rows for %s to single file", len(data), symbol)

    async def export_aggregated_bars(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        aggregations: list[str] = None,
    ) -> dict:
        """
        Export pre-aggregated OHLCV bars from continuous aggregations

        Args:
            symbol: Stock symbol to export
            start_date: Start date for export range
            end_date: End date for export range
            aggregations: List of aggregation levels to export

        Returns:
            Dict with export statistics for each aggregation level
        """
        if not aggregations:
            aggregations = ["5min", "15min", "1hour", "1day"]

        try:
            export_stats = {"aggregations": {}, "total_time": 0}
            start_time = time.perf_counter()

            bars_dir = self.export_base_dir / symbol / "bars"
            bars_dir.mkdir(parents=True, exist_ok=True)

            for agg in aggregations:
                try:
                    agg_data = await self.db.get_minute_data(
                        symbol, start_date, end_date, aggregation=agg
                    )

                    if not agg_data.empty:
                        file_path = bars_dir / f"{symbol}_{agg}_bars.csv"
                        agg_data.to_csv(file_path, float_format="%.4f")

                        export_stats["aggregations"][agg] = {
                            "rows": len(agg_data),
                            "file": str(file_path),
                        }

                        _logger.debug(
                            "Exported %d %s bars for %s", len(agg_data), agg, symbol
                        )
                    else:
                        _logger.warning("No %s data available for %s", agg, symbol)

                except Exception as e:
                    _logger.error("Failed to export %s bars for %s: %s", agg, symbol, e)
                    export_stats["aggregations"][agg] = {"error": str(e)}

            export_stats["total_time"] = time.perf_counter() - start_time

            _logger.info(
                "Aggregated bars export completed for %s: %s", symbol, export_stats
            )

            return export_stats

        except Exception as e:
            _logger.error("Aggregated bars export failed for %s: %s", symbol, e)
            return {"error": str(e)}

    async def export_bulk_symbols(
        self,
        symbols: list[str],
        start_date: datetime,
        end_date: datetime,
        export_format: str = "monthly",
        max_workers: int = 4,
    ) -> Dict:
        """
        Export multiple symbols in parallel

        Args:
            symbols: List of symbols to export
            start_date: Start date for export range
            end_date: End date for export range
            export_format: Format type for exports
            max_workers: Maximum number of parallel workers

        Returns:
            Dict with results for each symbol
        """
        try:
            start_time = time.perf_counter()
            results = {}

            # Use asyncio for concurrent exports
            import asyncio

            async def export_symbol(symbol: str):
                try:
                    return await self.export_symbol_to_csv(
                        symbol, start_date, end_date, export_format
                    )
                except Exception as e:
                    _logger.error("Bulk export failed for %s: %s", symbol, e)
                    return {"error": str(e)}

            # Create tasks for all symbols
            tasks = [export_symbol(symbol) for symbol in symbols]

            # Execute with limited concurrency
            semaphore = asyncio.Semaphore(max_workers)

            async def limited_export(task):
                async with semaphore:
                    return await task

            limited_tasks = [limited_export(task) for task in tasks]
            export_results = await asyncio.gather(*limited_tasks)

            # Combine results
            for symbol, result in zip(symbols, export_results):
                results[symbol] = result

            total_time = time.perf_counter() - start_time

            # Calculate summary statistics
            total_files = sum(
                r.get("files_created", 0) for r in results.values() if "error" not in r
            )
            total_rows = sum(
                r.get("total_rows", 0) for r in results.values() if "error" not in r
            )
            successful_symbols = len([r for r in results.values() if "error" not in r])

            summary = {
                "symbols_processed": len(symbols),
                "successful_exports": successful_symbols,
                "total_files_created": total_files,
                "total_rows_exported": total_rows,
                "total_time": total_time,
                "results": results,
            }

            _logger.info("Bulk export completed: %s", summary)

            return summary

        except Exception as e:
            _logger.error("Bulk export failed: %s", e)
            return {"error": str(e)}

    def get_export_summary(self, symbol: str = None) -> Dict:
        """
        Get summary of existing exports

        Args:
            symbol: Optional symbol to summarize (if None, summarizes all)

        Returns:
            Dict with export summary information
        """
        try:
            summary = {"symbols": {}, "total_files": 0, "total_size": 0}

            if symbol:
                symbol_dirs = [self.export_base_dir / symbol]
            else:
                symbol_dirs = [d for d in self.export_base_dir.iterdir() if d.is_dir()]

            for symbol_dir in symbol_dirs:
                if not symbol_dir.exists():
                    continue

                symbol_name = symbol_dir.name
                symbol_info = {"formats": {}, "total_files": 0, "total_size": 0}

                # Check each format directory
                for format_dir in symbol_dir.iterdir():
                    if not format_dir.is_dir():
                        continue

                    format_name = format_dir.name
                    files = list(format_dir.glob("*.csv*"))

                    if files:
                        format_size = sum(f.stat().st_size for f in files)
                        symbol_info["formats"][format_name] = {
                            "files": len(files),
                            "size_bytes": format_size,
                        }
                        symbol_info["total_files"] += len(files)
                        symbol_info["total_size"] += format_size

                if symbol_info["total_files"] > 0:
                    summary["symbols"][symbol_name] = symbol_info
                    summary["total_files"] += symbol_info["total_files"]
                    summary["total_size"] += symbol_info["total_size"]

            return summary

        except Exception as e:
            _logger.error("Failed to generate export summary: %s", e)
            return {"error": str(e)}
