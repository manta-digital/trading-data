"""Track → database registry invariants (slice 923 D1, D5, D6)."""

from __future__ import annotations

from collections import Counter

import pytest

from manta_trading.market.schema.databases import Database
from manta_trading.market.schema.migrations import (
    DEFAULT_TRACK,
    DEFAULT_TRACK_FOR,
    TRACK_REGISTRY,
    TRACKS,
)
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID


def test_every_track_names_a_database() -> None:
    for name, spec in TRACK_REGISTRY.items():
        assert isinstance(spec.database, Database), name


def test_every_database_has_a_registered_default_track() -> None:
    for database in Database:
        track = DEFAULT_TRACK_FOR[database]
        assert TRACK_REGISTRY[track].database is database


def test_primary_default_track_is_unchanged() -> None:
    assert DEFAULT_TRACK == "minute"


def test_tracks_is_derived_from_registry() -> None:
    assert set(TRACKS) == set(TRACK_REGISTRY)
    for name, spec in TRACK_REGISTRY.items():
        assert TRACKS[name] is spec.migrations


def test_every_track_starts_with_the_bootstrap() -> None:
    for name, migrations in TRACKS.items():
        assert migrations[0]["id"] == BOOTSTRAP_MIGRATION_ID, name


@pytest.mark.parametrize("database", list(Database))
def test_non_bootstrap_ids_unique_per_database(database: Database) -> None:
    """The misroute guard maps an id back to its track; ids must not collide."""
    ids = Counter(
        m["id"]
        for spec in TRACK_REGISTRY.values()
        if spec.database is database
        for m in spec.migrations
        if m["id"] != BOOTSTRAP_MIGRATION_ID
    )
    assert [i for i, n in ids.items() if n > 1] == []


def test_non_bootstrap_ids_unique_across_databases() -> None:
    seen: dict[str, str] = {}
    for name, migrations in TRACKS.items():
        for m in migrations:
            if m["id"] == BOOTSTRAP_MIGRATION_ID:
                continue
            owner = seen.setdefault(m["id"], name)
            assert TRACK_REGISTRY[owner].database is TRACK_REGISTRY[name].database, m[
                "id"
            ]


def test_tick_ids_after_bootstrap_are_prefixed() -> None:
    for m in TRACKS["tick"][1:]:
        assert m["id"].startswith("tick_"), m["id"]
