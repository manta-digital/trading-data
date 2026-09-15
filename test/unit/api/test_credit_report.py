"""Unit tests: the shared credit-report policy (189 code review F003).

`mt data overview` and `GET /api/v1/credits` ask the same question and had
grown separate copies of the same four-part answer: unset key is a setting,
provider failure is text rather than an exception, the text is redacted, and
the failure is logged with a stack. These tests pin all four in the one place
that now holds them.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from manta_trading.api.credit_report import (
    CREDITS_NO_KEY,
    credits_unavailable,
    read_credits,
)
from manta_trading.api.eodhd_account import CreditUsage

MODULE = "manta_trading.api.credit_report"


def _settings(api_key: str | None) -> SimpleNamespace:
    return SimpleNamespace(eodhd_api_key=api_key)


class TestTheThreeAnswers:
    def test_usage_when_the_fetch_succeeds(self) -> None:
        usage = CreditUsage(98412, 100000, 0)

        report = read_credits(_settings("a-key"), fetch=lambda _key: usage)

        assert report.credits is usage
        assert report.error is None

    def test_no_key_is_a_setting_not_a_fault(self) -> None:
        def _must_not_be_called(_key):  # pragma: no cover - asserted below
            raise AssertionError("fetched with no key configured")

        report = read_credits(_settings(None), fetch=_must_not_be_called)

        assert report.credits is None
        assert report.error == CREDITS_NO_KEY

    def test_a_failure_becomes_text_never_an_exception(self) -> None:
        def _raise(_key):
            raise RuntimeError("connection timed out")

        report = read_credits(_settings("a-key"), fetch=_raise)

        assert report.credits is None
        assert report.error == credits_unavailable("connection timed out")


class TestTheSecurityProperty:
    def test_the_key_is_redacted_out_of_the_reason(self) -> None:
        """The message can carry the request URL, and the key rides in its
        query string (922 review F001). Shaped like the real exception."""
        token = "6890abcdef1234567890"

        def _raise(_key):
            raise RuntimeError(
                "Client error '401 Unauthorized' for url "
                f"'https://eodhd.com/api/user?api_token={token}&fmt=json'"
            )

        report = read_credits(_settings("a-key"), fetch=_raise)

        assert token not in (report.error or "")

    def test_the_key_is_redacted_out_of_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        token = "6890abcdef1234567890"

        def _raise(_key):
            raise RuntimeError(f"boom api_token={token}")

        with caplog.at_level(logging.WARNING):
            read_credits(_settings("a-key"), fetch=_raise)

        assert token not in caplog.text

    def test_the_failure_is_logged_with_a_stack(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The catch is broad by contract, so a one-line warning would hide a
        programming error inside the fetch behind a plausible "unavailable"
        line (922 re-review F003).

        Asserted on the rendered text rather than on ``record.exc_info``: the
        stack is formatted and redacted into the message precisely because
        ``exc_info=True`` prints the exception's own text unredacted. Both
        properties have to hold together, and this pins the one that a fix for
        the other could quietly remove.
        """

        def _raise(_key):
            raise RuntimeError("boom")

        with caplog.at_level(logging.WARNING):
            read_credits(_settings("a-key"), fetch=_raise)

        assert "Traceback (most recent call last)" in caplog.text
        assert "in read_credits" in caplog.text


class TestTheFetchSeam:
    """The patch point must actually intercept the network.

    This exists because the first version of `read_credits` wrote the fetch as
    a **default argument** (`fetch: Any = fetch_credit_usage`). That binds the
    function object when the module is imported, so a test patching
    `credit_report.fetch_credit_usage` stubbed nothing and the real call went
    out to EODHD — spending quota, and on a machine with a valid key passing
    anyway. It was caught only because this machine's key returned 401.

    A test that believes it has stubbed the network but has not is worse than
    no test, so the seam gets its own assertion.
    """

    def test_patching_the_module_attribute_intercepts_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage = CreditUsage(1, 2, 3)
        monkeypatch.setattr(f"{MODULE}.fetch_credit_usage", lambda _key: usage)

        # No `fetch=` argument: this is the production path.
        report = read_credits(_settings("a-key"))

        assert report.credits is usage

    def test_the_default_is_not_bound_at_import(self) -> None:
        """The mechanical form of the same guarantee: a non-None default here
        would re-introduce the trap above."""
        import inspect

        default = inspect.signature(read_credits).parameters["fetch"].default

        assert default is None, (
            "fetch has a bound default, so patching "
            f"{MODULE}.fetch_credit_usage will not intercept the real call"
        )
