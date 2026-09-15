"""Reading the EODHD credit position as a *report* rather than a raise.

One policy, in one place, for the question "how many credits are left?" when
the answer may be unavailable. Both callers — ``mt data overview`` and
``GET /api/v1/credits`` — need the same four things, and had grown their own
copies of all four (189 code review F003):

1. an unset key is a **setting**, not a fault, and says so;
2. a provider that will not answer becomes text, never an exception, because
   the rest of the screen (and the rest of the API) is still true;
3. the failure text is **redacted** before it reaches a body or the journal,
   since the message can carry the request URL and the key rides in that URL's
   query string (922 review F001);
4. the failure is logged **with its stack, redacted** — the catch is broad by
   contract, and a one-line warning would hide a programming error inside the
   fetch behind a plausible "unavailable" line (922 re-review F003). Plain
   ``exc_info=True`` is not enough: it renders the exception's own text
   unredacted at the end of the traceback.

Keeping those four together is the point. Redaction and the broad catch are a
security and a correctness property respectively, and a second copy is a place
for one of them to be forgotten.
"""

from __future__ import annotations

import traceback
from typing import Any

from manta_trading.api.eodhd_account import CreditUsage, fetch_credit_usage
from manta_trading.api.eodhd_sync import redact_token
from manta_trading.logging import get_logger

__all__ = [
    "CREDITS_NO_KEY",
    "CreditReport",
    "credits_unavailable",
    "read_credits",
]

_logger = get_logger(__name__)

CREDITS_NO_KEY = "unavailable (MT_EODHD_API_KEY not configured)"
"""Shown when no API key is configured — a setting, not a fault."""


def credits_unavailable(reason: str) -> str:
    """The credit line when the account endpoint could not be reached."""
    return f"unavailable ({reason})"


class CreditReport:
    """What was learned about the credit position: a usage, or a reason.

    Exactly one of the two is set. Deliberately not an exception and not a
    bare ``None``: "no key configured" and "provider timed out" are different
    answers an operator needs told apart, and both are answers rather than
    failures.
    """

    __slots__ = ("credits", "error")

    def __init__(
        self, *, credits: CreditUsage | None = None, error: str | None = None
    ) -> None:
        self.credits = credits
        self.error = error


def read_credits(
    settings: Any, *, fetch: Any = None, context: str = "credits"
) -> CreditReport:
    """Read the credit position, reporting every failure rather than raising.

    Args:
        settings: anything carrying ``eodhd_api_key``. Read as a plain
            attribute, never through a ``getattr`` default — an unset key is a
            value this function handles, while a *renamed* field is a bug that
            should fail loudly rather than render a plausible "not configured".
        fetch: the account reader, injectable so tests need no network.
            ``None`` resolves :func:`fetch_credit_usage` **at call time**,
            through the module attribute. That indirection is deliberate: as a
            default argument the function object would be captured when this
            module is imported, and a test patching
            ``credit_report.fetch_credit_usage`` would appear to stub the
            network while the real call still went out to EODHD — spending
            quota and, on a machine with a valid key, passing anyway. Resolving
            here makes the patch point work.
        context: prefix for the log line, so the journal says which caller
            was asking.

    Returns:
        A :class:`CreditReport` carrying either the usage or a reason.
    """
    api_key = settings.eodhd_api_key
    if not api_key:
        return CreditReport(error=CREDITS_NO_KEY)

    reader = fetch if fetch is not None else fetch_credit_usage

    try:
        return CreditReport(credits=reader(str(api_key)))
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        # Swallowed on purpose, and scoped to the one outbound call: reaching
        # a third party is exactly the step whose failure this function exists
        # to turn into a readable line. Re-raising would make a provider's bad
        # afternoon fail a status screen and 500 an API route.
        reason = redact_token(str(exc))
        # The stack, but NOT the exception's own text. ``exc_info=True`` would
        # render the traceback's final line as the raw exception — unredacted —
        # so a message carrying ``api_token=…`` reached the journal in full
        # even though the summary line above it said REDACTED. Verified by
        # capturing log output: the summary read ``api_token=REDACTED`` while
        # the traceback ended ``RuntimeError: boom api_token=6890abc…``.
        #
        # The frames are what made ``exc_info`` worth having — they show a
        # programming error inside the fetch rather than hiding it behind a
        # plausible "unavailable" line (922 re-review F003) — so they are kept
        # and only the rendering is scrubbed.
        _logger.warning(
            "%s: credit lookup failed: %s\n%s",
            context,
            reason,
            _redacted_stack(exc),
        )
        return CreditReport(error=credits_unavailable(reason))


def _redacted_stack(exc: BaseException) -> str:
    """The traceback frames, with every token scrubbed from the rendering.

    Redaction is applied to the whole formatted string rather than to the
    message alone: a frame's source line can show the URL being built, so the
    key can appear in the body of the trace as well as in its final line.
    """
    formatted = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return redact_token(formatted)
