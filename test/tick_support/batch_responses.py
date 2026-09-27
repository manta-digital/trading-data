"""Batch API responses for the tick adapter tests, in the SDK's documented shape.

The job record's fields are those of the SDK quickstart's ``submit_job`` /
``list_jobs`` output (``symbols`` a comma-joined string, timestamps as
``YYYY-MM-DD HH:MM:SS+00:00``). No recorded batch fixture exists: recording
one would mean buying data, which slice 220 never does.
"""

from __future__ import annotations

import hashlib
from typing import Any

from tick_support.fake_historical import FakeApi

JOB_ID = "GLBX-20250112-ABCDEFGHIJ"


def job_record(
    job_id: str = JOB_ID,
    schema: str = "trades",
    state: str = "done",
    start: str = "2025-01-06 00:00:00+00:00",
    end: str = "2025-01-11 00:00:00+00:00",
) -> dict[str, Any]:
    return {
        "id": job_id,
        "user_id": "TESTUSER",
        "bill_id": None,
        "cost_usd": 1.25,
        "dataset": "GLBX.MDP3",
        "symbols": "ES.c.0",
        "stype_in": "continuous",
        "stype_out": "instrument_id",
        "schema": schema,
        "start": start,
        "end": end,
        "limit": None,
        "encoding": "dbn",
        "compression": "zstd",
        "pretty_px": False,
        "pretty_ts": False,
        "split_duration": "day",
        "split_size": None,
        "split_symbols": False,
        "packaging": None,
        "delivery": "download",
        "record_count": 2_000_000,
        "billed_size": 96_000_000,
        "actual_size": 96_000_000,
        "package_size": 30_000_000,
        "state": state,
        "ts_received": "2025-01-12 00:24:03.786913+00:00",
        "ts_queued": "2025-01-12 00:24:04+00:00",
        "ts_process_start": "2025-01-12 00:24:10+00:00",
        "ts_process_done": "2025-01-12 00:25:10+00:00",
        "ts_expiration": "2025-02-11 00:25:10+00:00",
    }


def file_record(name: str, content: bytes) -> dict[str, Any]:
    return {
        "filename": name,
        "size": len(content),
        "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "urls": {
            "https": f"https://download.databento.test/{JOB_ID}/{name}",
            "ftp": f"ftp://ftp.databento.test/{JOB_ID}/{name}",
        },
    }


def batch_api(jobs: dict[str, dict[str, Any]] | None = None) -> FakeApi:
    """Submits create ``JOB_ID``; details come from ``jobs`` (default: one job)."""
    jobs = jobs if jobs is not None else {JOB_ID: job_record()}
    return FakeApi(
        {
            "submit_job": job_record(state="queued"),
            "get_job_details": lambda kwargs: jobs[kwargs["job_id"]],
            "list_jobs": [
                {"id": job_id, "state": job["state"], "ts_received": job["ts_received"]}
                for job_id, job in jobs.items()
            ],
        }
    )
