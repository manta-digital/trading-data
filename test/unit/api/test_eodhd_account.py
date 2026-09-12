"""Unit tests: reading the EODHD credit position (slice 922, Task 4.1/4.2).

Two things this module must get right:

- ``apiRequests`` is credits **used**, not remaining. The field name reads
  like a request count, and on a plan where an ``/intraday`` call costs five
  credits that reading would understate spend fivefold.
- A payload it does not recognise raises rather than defaulting. A credit
  number that is quietly wrong is worse than one the overview reports as
  unavailable — the operator would size a backfill against it.

The call is deliberately not routed through ``eodhd_get``: asking how many
credits are left must not spend one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from manta_trading.api.eodhd_account import CreditUsage, fetch_credit_usage
from manta_trading.constants import (
    EODHD_ACCOUNT_TIMEOUT_SECONDS,
    EODHD_USER_ENDPOINT,
)

# The shape the endpoint actually returns, as recorded during slice 921's
# cutover work (test_cutover_921.py) against the live account.
_LIVE_PAYLOAD = {
    "apiRequests": 6_858,
    "dailyRateLimit": 100_000,
    "extraLimit": 500_000,
}


def _client(
    payload: object, *, status: int = 200, api_key: str = "k"
) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.status_code = status
    if status >= 400:
        # The real message, not a bare status: httpx renders the full
        # request URL into HTTPStatusError, which is how the token leaked
        # onto the overview screen and into the journal (922 review F001).
        # A fixture that only said "401" could not catch that.
        url = f"{EODHD_USER_ENDPOINT}?api_token={api_key}&fmt=json"
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"Client error '{status}' for url '{url}'",
            request=MagicMock(),
            response=response,
        )
    client = MagicMock()
    client.get.return_value = response
    return client


class TestParsing:
    def test_the_three_fields_parse(self) -> None:
        usage = fetch_credit_usage("k", client=_client(_LIVE_PAYLOAD))
        assert usage == CreditUsage(used=6_858, daily_limit=100_000, extra=500_000)

    def test_remaining_counts_the_extras(self) -> None:
        """Purchased extras are what let a pass run past the plan limit."""
        usage = fetch_credit_usage("k", client=_client(_LIVE_PAYLOAD))
        assert usage.remaining == 593_142

    def test_a_missing_extra_limit_defaults_to_none_purchased(self) -> None:
        payload = {"apiRequests": 10, "dailyRateLimit": 100_000}
        usage = fetch_credit_usage("k", client=_client(payload))
        assert usage.extra == 0
        assert usage.remaining == 99_990

    def test_string_numbers_are_coerced(self) -> None:
        payload = {"apiRequests": "10", "dailyRateLimit": "100000"}
        assert fetch_credit_usage("k", client=_client(payload)).used == 10

    def test_a_fully_spent_day_reports_zero_remaining(self) -> None:
        payload = {"apiRequests": 100_000, "dailyRateLimit": 100_000}
        assert fetch_credit_usage("k", client=_client(payload)).remaining == 0


class TestFailures:
    def test_a_non_200_raises(self) -> None:
        """Not swallowed here: the caller decides what unavailable means."""
        with pytest.raises(httpx.HTTPStatusError):
            fetch_credit_usage("k", client=_client({}, status=401))

    def test_a_missing_field_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError, match="unexpected payload"):
            fetch_credit_usage("k", client=_client({"dailyRateLimit": 100_000}))

    def test_a_non_numeric_field_raises(self) -> None:
        payload = {"apiRequests": "lots", "dailyRateLimit": 100_000}
        with pytest.raises(ValueError, match="unexpected payload"):
            fetch_credit_usage("k", client=_client(payload))

    def test_a_list_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="unexpected payload"):
            fetch_credit_usage("k", client=_client([1, 2, 3]))

    def test_a_timeout_propagates(self) -> None:
        client = MagicMock()
        client.get.side_effect = httpx.ReadTimeout("too slow")
        with pytest.raises(httpx.ReadTimeout):
            fetch_credit_usage("k", client=client)

    def test_the_error_text_does_not_leak_the_token(self) -> None:
        """Journald persists ERROR lines; a key must never land there."""
        with pytest.raises(ValueError) as caught:
            fetch_credit_usage("sekrit-key", client=_client({}))
        assert "sekrit-key" not in str(caught.value)
        assert "REDACTED" in str(caught.value)

    def test_a_status_error_does_not_leak_the_token_either(self) -> None:
        """The path that actually leaked: httpx puts the URL in the message.

        The overview prints this text on a screen whose docstring invites
        pasting it into an issue, and logs it at WARNING. Redacting at the
        raise site means no caller can leak it by accident.
        """
        with pytest.raises(httpx.HTTPStatusError) as caught:
            fetch_credit_usage(
                "sekrit-key", client=_client({}, status=403, api_key="sekrit-key")
            )
        assert "sekrit-key" not in str(caught.value)
        assert "REDACTED" in str(caught.value)

    def test_a_status_error_still_names_the_status(self) -> None:
        """Redaction must not cost the operator the reason."""
        with pytest.raises(httpx.HTTPStatusError) as caught:
            fetch_credit_usage("k", client=_client({}, status=403))
        assert "403" in str(caught.value)


class TestTheCall:
    def test_it_hits_the_account_endpoint_with_the_key(self) -> None:
        client = _client(_LIVE_PAYLOAD)
        fetch_credit_usage("my-key", client=client)
        url = client.get.call_args.args[0]
        assert url.startswith(EODHD_USER_ENDPOINT)
        assert "api_token=my-key" in url

    def test_it_is_bounded_by_the_short_timeout(self) -> None:
        """Half the overview's ten-second budget; the database gets the rest."""
        client = _client(_LIVE_PAYLOAD)
        fetch_credit_usage("k", client=client)
        assert client.get.call_args.kwargs["timeout"] == (EODHD_ACCOUNT_TIMEOUT_SECONDS)
        assert EODHD_ACCOUNT_TIMEOUT_SECONDS <= 5.0

    def test_it_makes_exactly_one_call(self) -> None:
        """No retry: a status line is not worth waiting through a backoff."""
        client = _client(_LIVE_PAYLOAD)
        fetch_credit_usage("k", client=client)
        assert client.get.call_count == 1

    def test_it_works_with_no_quota_bucket_in_scope(self) -> None:
        """Asking how many credits are left must not spend one.

        Routing through ``eodhd_get`` would do both: it calls
        ``bucket.consume`` before every request, and with no bucket in scope
        it raises ``QuotaBucketUnsetError`` instead of answering. This test
        runs with no bucket set and expects an answer, which pins both.
        """
        from manta_trading.api.eodhd_sync import (
            QuotaBucketUnsetError,
            _resolve_bucket,
        )

        with pytest.raises(QuotaBucketUnsetError):
            _resolve_bucket()
        usage = fetch_credit_usage("k", client=_client(_LIVE_PAYLOAD))
        assert usage.used == 6_858
