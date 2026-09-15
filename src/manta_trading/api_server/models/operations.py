"""Pydantic response models for the operations and freshness routes (189).

Pure translation from slice 922's frozen dataclasses to the wire shape. No
I/O and no derivation: :func:`build_overview` computes everything these models
carry, and a second computation here would be SC2 failing (189 D3).

Two fields of the CLI screen are deliberately absent, each for a reason
recorded on the model that drops it: ``abandoned`` (D4, on
:class:`RunningRecord`) and the credit line (D2, served by
:class:`CreditsResponse` on its own route).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from manta_trading.api.eodhd_account import CreditUsage
from manta_trading.cli.overview_types import Overview, PassLine
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome

__all__ = [
    "CreditsRecord",
    "CreditsResponse",
    "HealthBlock",
    "LastRunRecord",
    "OverviewResponse",
    "PassLineRecord",
    "RunningRecord",
    "SourceRecord",
    "UniverseBlock",
]


class RunningRecord(BaseModel):
    """One still-open run of a pass kind.

    Mirrors 922's ``RunningRow`` **minus ``abandoned``** (189 D4). That field
    is not omitted by oversight and must not be restored here: 922 infers it
    by testing the recorded pid against the *local* process table, which is
    sound for a CLI run beside the pass and meaningless in the API server,
    where a pid recorded on another host says nothing about a process here.
    The route passes ``pid_alive=lambda _pid: True`` so the value would be
    structurally ``False`` for every row, and publishing a field that is
    always false is worse than publishing none. Real abandonment over HTTP
    needs a heartbeat column written by the pass itself (D7, future work).
    """

    phase: str | None
    done: int | None
    total: int | None
    since: datetime
    progress_at: datetime | None
    hostname: str
    pid: int


class LastRunRecord(BaseModel):
    """The most recent ended run of a pass kind (922 ``LastRun``)."""

    started_at: datetime
    ended_at: datetime
    outcome: PassRunOutcome
    exit_code: int | None
    detail: str | None


class PassLineRecord(BaseModel):
    """One entry of the ``passes`` array (922 ``PassLine``).

    ``running`` is a list because two live runs of one kind is a real state
    922 already renders and tests; collapsing it to an optional single would
    hide exactly the condition worth seeing.
    """

    # ``pass`` is a Python keyword, so the attribute carries a trailing
    # underscore and the wire name comes from the alias. Serialization uses
    # the alias (``model_config`` below), which is what puts ``"pass"`` in
    # both the response body and the published schema.
    pass_: PassKind = Field(alias="pass")
    cadence: str
    running: list[RunningRecord]
    last_run: LastRunRecord | None
    next_firing: datetime | None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class SourceRecord(BaseModel):
    """The newest row in one source table (922 ``SourceFreshness``)."""

    name: str
    newest: datetime | None


class HealthBlock(BaseModel):
    """The health verdict and when it was recorded."""

    verdict: str | None
    at: datetime | None


class UniverseBlock(BaseModel):
    """The universe accounting line and when it was recorded.

    ``summary`` is ``None`` where the screen renders ``NO_ACCOUNTING``: the
    API serves nulls and leaves the prose to the client.
    """

    summary: str | None
    at: datetime | None


class OverviewResponse(BaseModel):
    """Response body for ``GET /api/v1/overview``.

    Not a strict superset of ``mt data overview --json`` (189 D2/SC7): the
    credit fields live at ``GET /api/v1/credits`` so this route is pure
    database and safe to poll, and ``abandoned`` is a local-host judgment
    available only from the CLI (D4).
    """

    now: datetime
    passes: list[PassLineRecord]
    sources: list[SourceRecord]
    health: HealthBlock
    universe: UniverseBlock

    @classmethod
    def from_overview(cls, overview: Overview) -> OverviewResponse:
        """Translate a 922 ``Overview`` into the wire shape.

        Every field of ``Overview`` is carried except ``credits`` and
        ``credits_text`` (D2) and, within each running row, ``abandoned``
        (D4).
        """
        return cls(
            now=overview.now,
            passes=[_pass_line(line) for line in overview.passes],
            sources=[
                SourceRecord(name=source.name, newest=source.newest)
                for source in overview.sources
            ],
            health=HealthBlock(
                verdict=overview.health_verdict, at=overview.health_at
            ),
            universe=UniverseBlock(
                summary=overview.universe, at=overview.universe_at
            ),
        )


def _pass_line(line: PassLine) -> PassLineRecord:
    """One ``PassLine`` as its wire record.

    ``last`` becomes ``last_run: null`` where the screen renders
    ``NEVER_RUN`` — the sentinel string is a rendering concern and is
    deliberately not imported here.
    """
    # Constructed through the alias rather than the field name: ``pass`` is
    # a keyword, so it cannot be written as a keyword argument at all.
    return PassLineRecord(
        **{"pass": line.kind},
        cadence=line.cadence,
        running=[
            RunningRecord(
                phase=row.phase,
                done=row.done,
                total=row.total,
                since=row.since,
                progress_at=row.progress_at,
                hostname=row.hostname,
                pid=row.pid,
            )
            for row in line.running
        ],
        last_run=(
            None
            if line.last is None
            else LastRunRecord(
                started_at=line.last.started_at,
                ended_at=line.last.ended_at,
                outcome=line.last.outcome,
                exit_code=line.last.exit_code,
                detail=line.last.detail,
            )
        ),
        next_firing=line.next_firing,
    )


class CreditsRecord(BaseModel):
    """The day's EODHD credit position (``CreditUsage``).

    ``remaining`` is a computed property on the dataclass, not a stored
    field, so it is read through the property rather than copied from one.
    """

    used: int
    daily_limit: int
    extra: int
    remaining: int

    @classmethod
    def from_usage(cls, usage: CreditUsage) -> CreditsRecord:
        return cls(
            used=usage.used,
            daily_limit=usage.daily_limit,
            extra=usage.extra,
            remaining=usage.remaining,
        )


class CreditsResponse(BaseModel):
    """Response body for ``GET /api/v1/credits``.

    Always 200 (189 D6/SC6): a provider that will not answer is a reported
    condition, not a server fault, so the failure arrives as ``error`` text
    with ``credits: null``.
    """

    credits: CreditsRecord | None
    error: str | None
