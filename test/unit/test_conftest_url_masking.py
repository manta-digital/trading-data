"""The database fixtures must not print their password when a test fails.

pytest renders each fixture's value with ``repr()`` in the header of a failure
traceback. Before this guard, every failing test that used ``ephemeral_db``
printed the full connection URL — password included — into the run's output,
and would have done the same into CI logs.

These tests fail if that regression returns.
"""

from __future__ import annotations

from conftest import MaskedUrl, swap_dbname

_RAW = "postgresql://trading_test_admin:s3cr3t-P4ss@192.168.1.143:5432/postgres"
_SECRET = "s3cr3t-P4ss"


def test_repr_does_not_contain_the_password() -> None:
    """This is the path pytest uses to print fixture values."""
    url = swap_dbname(_RAW, "mt_test_abc123")
    assert _SECRET not in repr(url)
    assert "***" in repr(url)


def test_repr_keeps_enough_to_be_useful() -> None:
    """Masking must not make a failure harder to diagnose."""
    rendered = repr(swap_dbname(_RAW, "mt_test_abc123"))
    assert "trading_test_admin" in rendered
    assert "192.168.1.143:5432" in rendered
    assert "mt_test_abc123" in rendered


def test_the_usable_value_is_unchanged() -> None:
    """psycopg needs the real characters; only the printed form is masked."""
    url = swap_dbname(_RAW, "mt_test_abc123")
    assert isinstance(url, str)
    assert str(url) == (
        "postgresql://trading_test_admin:s3cr3t-P4ss@"
        "192.168.1.143:5432/mt_test_abc123"
    )


def test_a_url_without_a_password_is_left_alone() -> None:
    """No password to hide, so no masking artefacts in the output."""
    url = MaskedUrl("postgresql://someone@example:5432/db")
    assert "***" not in repr(url)
    assert "someone" in repr(url)
