"""The slice 172 cutover script's control flow (Task 4.4).

``scripts/`` is not a package: the modules load by path with the directory on
``sys.path``, as ``test_cutover_921.py`` does. Every host, subprocess and
database seam is patched; what is pinned is the order of operations, because
the script pauses production jobs and must always resume them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> Any:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cutover() -> Any:
    return _load("cutover_172_eligible_rollups")


class _Job:
    def __init__(self, job_id: int, view: str, scheduled: bool = True) -> None:
        self.job_id = job_id
        self.view_name = view
        self.scheduled = scheduled


class Harness:
    """Records every seam call, in order, as a readable event."""

    def __init__(self, cutover: Any) -> None:
        self.cutover = cutover
        self.build = cutover.build
        self.events: list[str] = []
        self.mt_codes: dict[str, int] = {}
        self.leftovers: list[_Job] = []
        self.headroom_error: Exception | None = None
        self.sweep_error: Exception | None = None
        self.installed = "old"
        self.verify_output = "[]"

    def mt(self, *args: str) -> tuple[int, str]:
        name = (
            args[0]
            if args[0] != "repair"
            else f"repair {args[args.index('--granularity') + 1]}"
        )
        if "--verify" in args:
            name = "coverage --verify"
        self.events.append(f"mt {name}")
        output = self.verify_output if args[0] == "verify" else ""
        return self.mt_codes.get(name, 0), output

    def install(self, *_args: str) -> str:
        self.events.append("install")
        return "mt, version 0.24.1"

    def jobs(self, _conn: Any, view: str) -> list[_Job]:
        return [_Job(len(view), view)]

    def set_scheduled(self, _conn: Any, jobs: list[_Job], scheduled: bool) -> list[int]:
        verb = "resume" if scheduled else "pause"
        self.events += [f"{verb} {job.view_name}" for job in jobs]
        return [job.job_id for job in jobs]

    def headroom(self, _url: str) -> float:
        self.events.append("headroom")
        if self.headroom_error:
            raise self.headroom_error
        return 812.5

    def sweep(self, _conn: Any) -> None:
        self.events.append("sweep check")
        if self.sweep_error:
            raise self.sweep_error

    def patches(self, tmp_path: Path) -> list[Any]:
        c, b = self.cutover, self.build
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        return [
            patch.object(c, "_mt", side_effect=self.mt),
            patch.object(c, "_connect", return_value=conn),
            patch.object(c, "_maintenance_url", return_value="postgresql://x/db"),
            patch.object(c, "_installed_commit", side_effect=lambda: self.installed),
            patch.object(c, "preflight", return_value="abc123def456"),
            patch.object(c, "install", side_effect=self.install),
            patch.object(
                c, "migrate", side_effect=lambda *a: self.events.append("migrate")
            ),
            patch.object(
                c,
                "restart_serve_if_active",
                side_effect=lambda: self.events.append("restart serve"),
            ),
            patch.object(c, "unit_active", return_value=True),
            patch.object(c, "wait_for_unit_to_end"),
            patch.object(
                c,
                "run",
                side_effect=lambda args, **k: self.events.append(" ".join(args)),
            ),
            patch.object(c, "start_log"),
            patch.object(c, "NOTES_DIR", tmp_path),
            patch.object(b, "jobs", side_effect=self.jobs),
            patch.object(b, "set_scheduled", side_effect=self.set_scheduled),
            patch.object(b, "paused_next_jobs", side_effect=lambda _c: self.leftovers),
            patch.object(b, "require_sweep_complete", side_effect=self.sweep),
            patch.object(b, "headroom_gb", side_effect=self.headroom),
            patch.object(b, "row_counts", return_value={"minute_5min_ohlcv_next": 7}),
            patch.object(b, "refresh_load", return_value=[]),
        ]

    def run(self, tmp_path: Path) -> int:
        patches = self.patches(tmp_path)
        for p in patches:
            p.start()
        try:
            return self.cutover.main(["v0.24.1"])
        finally:
            for p in reversed(patches):
                p.stop()


@pytest.fixture()
def harness(cutover: Any) -> Harness:
    return Harness(cutover)


def _repairs(events: list[str]) -> list[str]:
    return [e for e in events if e.startswith("mt repair")]


def test_build_refused_while_history_unevaluated(
    harness: Harness, tmp_path: Path
) -> None:
    harness.sweep_error = harness.build.BuildRefused("3 minute chunk(s) still hold")
    assert harness.run(tmp_path) == 1
    assert _repairs(harness.events) == []
    assert not any(e.startswith("pause") for e in harness.events)
    report = next(tmp_path.glob("*-172-cutover.md")).read_text()
    assert "3 minute chunk(s) still hold" in report


def test_headroom_failure_stops_before_any_repair(
    harness: Harness, tmp_path: Path
) -> None:
    harness.headroom_error = harness.build.BuildRefused("could not measure")
    assert harness.run(tmp_path) == 1
    assert "headroom" in harness.events
    assert _repairs(harness.events) == []


def test_order_is_4h_1h_15m_5m_then_coverage_then_verify(
    harness: Harness, tmp_path: Path
) -> None:
    assert harness.run(tmp_path) == 0
    mt = [e for e in harness.events if e.startswith("mt ")]
    assert mt == [
        "mt repair 4h",
        "mt repair 1h",
        "mt repair 15m",
        "mt repair 5m",
        "mt rebuild-coverage",
        "mt coverage --verify",
        "mt verify",
    ]


def test_every_pause_is_followed_by_its_resume(
    harness: Harness, tmp_path: Path
) -> None:
    assert harness.run(tmp_path) == 0
    paused = [e.split()[1] for e in harness.events if e.startswith("pause ")]
    assert paused == [
        *(v for _, v in harness.build.BUILD_ORDER),
        "minute_coverage_next",
    ]
    for view in paused:
        i = harness.events.index(f"pause {view}")
        assert harness.events[i + 2] == f"resume {view}", harness.events[i : i + 3]


def test_repair_failure_still_resumes_and_exits_nonzero(
    harness: Harness, tmp_path: Path
) -> None:
    harness.mt_codes["repair 1h"] = 2
    assert harness.run(tmp_path) == 1
    events = harness.events
    view_1h = dict(harness.build.BUILD_ORDER)[harness.build.Granularity.H1]
    assert events.index(f"resume {view_1h}") > events.index("mt repair 1h")
    assert "mt repair 15m" not in events
    assert "mt verify" not in events


def test_leftover_paused_job_is_resumed_at_start(
    harness: Harness, tmp_path: Path
) -> None:
    harness.leftovers = [_Job(1201, "minute_hourly_ohlcv_next", scheduled=False)]
    assert harness.run(tmp_path) == 0
    assert harness.events.index("resume minute_hourly_ohlcv_next") < (
        harness.events.index("sweep check")
    )


def test_failing_verify_exits_nonzero_naming_the_view(
    harness: Harness, tmp_path: Path
) -> None:
    harness.mt_codes["verify"] = 2
    harness.verify_output = "INFO pool opened\n" + json.dumps(
        [
            {"view": "minute_5min_ohlcv_next", "in_parity": False},
            {"view": "minute_4hour_ohlcv_next", "in_parity": True},
        ]
    )
    assert harness.run(tmp_path) == 1
    report = next(tmp_path.glob("*-172-cutover.md")).read_text()
    assert "out of parity: minute_5min_ohlcv_next" in report
    assert (
        "minute_4hour_ohlcv_next"
        not in report.split("out of parity:")[1].split("\n")[0]
    )


def test_rerun_skips_release_steps_and_builds(harness: Harness, tmp_path: Path) -> None:
    harness.installed = "abc123def456"  # the ref is already installed
    assert harness.run(tmp_path) == 0
    assert "install" not in harness.events
    assert "restart serve" not in harness.events
    assert "migrate" in harness.events  # migrate itself skips an applied 063
    assert len(_repairs(harness.events)) == 4


def test_timers_are_released_before_the_build(harness: Harness, tmp_path: Path) -> None:
    assert harness.run(tmp_path) == 0
    released = harness.events.index("systemctl start mt-minute-pass.timer")
    assert released < harness.events.index("mt repair 4h")
    assert harness.events.index("systemctl stop mt-minute-pass.timer") < (
        harness.events.index("migrate")
    )


# --- the build helpers --------------------------------------------------------


@pytest.fixture(scope="module")
def build() -> Any:
    return _load("cutover_172_build")


_CLUSTERS = (
    "17 main 5432 online postgres /var/lib/postgresql/17/main /var/log/main.log\n"
    "17 tick 5433 online postgres /data/postgresql/17/tick    /var/log/tick.log\n"
)


def test_headroom_measures_the_clusters_data_directory(build: Any) -> None:
    seen: list[str] = []

    def usage(path: str) -> Any:
        seen.append(path)
        return MagicMock(free=50 * 1024**3)

    url = "postgresql://u:p@127.0.0.1:5433/tick"
    assert build.headroom_gb(url, lambda: _CLUSTERS, usage) == 50.0
    assert seen == ["/data/postgresql/17/tick"]


def test_headroom_unknown_port_refuses(build: Any) -> None:
    with pytest.raises(build.BuildRefused, match="port 6000"):
        build.headroom_gb("postgresql://u:p@127.0.0.1:6000/x", lambda: _CLUSTERS)


def test_headroom_unreadable_volume_refuses(build: Any) -> None:
    def usage(_path: str) -> Any:
        raise PermissionError("denied")

    with pytest.raises(build.BuildRefused, match="denied"):
        build.headroom_gb("postgresql://u:p@localhost/x", lambda: _CLUSTERS, usage)


def test_headroom_remote_host_refuses(build: Any) -> None:
    with pytest.raises(build.BuildRefused, match="not this machine"):
        build.headroom_gb("postgresql://u:p@192.0.2.10/x", lambda: _CLUSTERS)


def test_sweep_gate_names_the_oldest_pending_chunk(build: Any) -> None:
    chunk = MagicMock()
    chunk.name = "_timescaledb_internal._hyper_1_9_chunk"
    with patch.object(build, "unevaluated_chunks", return_value=[chunk, chunk]):
        with pytest.raises(build.BuildRefused, match="2 minute chunk") as exc:
            build.require_sweep_complete(MagicMock())
    assert chunk.name in str(exc.value)
    assert "sweep" in str(exc.value)
    with patch.object(build, "unevaluated_chunks", return_value=[]):
        build.require_sweep_complete(MagicMock())


def test_report_lists_load_and_rows(build: Any) -> None:
    load = [
        build.RefreshLoad(
            "minute_5min_ohlcv_next", timedelta(seconds=4), timedelta(minutes=5)
        )
    ]
    text = build.render_report(
        "2026-10-19T10:00:00+00:00",
        "v0.24.1",
        True,
        [("build", "PASS", ["minute_4hour_ohlcv_next: repaired"])],
        ({"minute_5min_ohlcv_next": 1234}, load),
        "[]",
    )
    assert "Outcome: PASS" in text
    assert "minute_5min_ohlcv_next: 1,234" in text
    assert "0:00:04 every 0:05:00" in text
