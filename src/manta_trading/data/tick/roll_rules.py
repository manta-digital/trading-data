"""Roll rules: which contract of a futures product counts on a session (229).

LLD 229 TD1. A rule is a method (``RollMethod``), a rank (``0`` = front) and,
for calendar only, an offset in sessions before expiry. One text grammar,
``{method}.{rank}[+{offset}s]``, is parsed and rendered here and nowhere else;
the CLI and the API (231) accept exactly this form. ``parse(str(rule)) ==
rule`` for every valid rule, and ``str(parse(text))`` is ``text``'s canonical
form. Pure: no I/O.

The project default and the rules ``status`` shows live here, beside the
class, because they are ``RollRule`` values (``constants`` cannot import this
module without a cycle).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from manta_trading.data.tick.constants import RollMethod

_GRAMMAR = "{method}.{rank}[+{offset}s]"
_RANK = re.compile(r"(-?\d+)")
_OFFSET = re.compile(r"\+(\d+)s")


@dataclass(frozen=True)
class RollRule:
    """A roll method, a rank, and a calendar-only offset in sessions."""

    method: RollMethod
    rank: int = 0  # 0 = front, 1 = next, … (Databento's depth index)
    offset_sessions: int = 0  # calendar only: roll N sessions before expiry

    def __post_init__(self) -> None:
        label = f"roll rule {self.method.value}"
        if self.rank < 0:
            raise ValueError(f"{label}: rank must be >= 0, got {self.rank}")
        if self.offset_sessions < 0:
            raise ValueError(
                f"{label}: offset_sessions must be >= 0, got {self.offset_sessions}"
            )
        if self.offset_sessions and self.method is not RollMethod.CALENDAR:
            raise ValueError(
                f"{label}: offset_sessions is allowed only on calendar "
                f"({RollMethod.CALENDAR.value}), got {self.offset_sessions}"
            )

    def __str__(self) -> str:
        suffix = f"+{self.offset_sessions}s" if self.offset_sessions else ""
        return f"{self.method.value}.{self.rank}{suffix}"

    def symbol(self, product: str) -> str:
        """``{product}.{rule}``; with no offset, Databento's continuous symbol."""
        return f"{product}.{self}"

    @classmethod
    def parse(cls, text: str) -> RollRule:
        """Parse TD1's grammar; lenient on case and surrounding whitespace.

        Raises:
            ValueError: naming what is wrong with ``text``.
        """
        clean = text.strip().lower()
        method_text, dot, rest = clean.partition(".")
        if not dot:
            raise ValueError(f"roll rule {text!r}: expected {_GRAMMAR}")
        method = _method(method_text)
        rank_match = _RANK.match(rest)
        if rank_match is None:
            raise ValueError(f"roll rule {text!r}: rank must be a non-negative integer")
        rank = int(rank_match.group(1))
        if rank < 0:
            raise ValueError(f"roll rule {text!r}: rank must be >= 0, got {rank}")
        offset = _offset(text, method, rest[rank_match.end() :])
        return cls(method, rank, offset)


def _method(text: str) -> RollMethod:
    try:
        return RollMethod(text)
    except ValueError:
        known = ", ".join(m.value for m in RollMethod)
        raise ValueError(f"unknown roll method {text!r}; known: {known}") from None


def _offset(text: str, method: RollMethod, suffix: str) -> int:
    """The offset in ``suffix`` (``""`` or ``+{N}s``), refusing what TD1 refuses."""
    if not suffix:
        return 0
    match = _OFFSET.match(suffix)
    if match is None or match.end() != len(suffix):
        trailing = suffix if match is None else suffix[match.end() :]
        raise ValueError(f"roll rule {text!r}: trailing text {trailing!r}")
    if method is not RollMethod.CALENDAR:
        raise ValueError(
            f"roll rule {text!r}: an offset suffix is allowed only on calendar "
            f"({RollMethod.CALENDAR.value})"
        )
    offset = int(match.group(1))
    if offset == 0:
        raise ValueError(f"roll rule {text!r}: offset +0s is written with no suffix")
    return offset


#: The project default (TD5): every surface that accepts a rule defaults here.
DEFAULT_ROLL_RULE = RollRule(RollMethod.VOLUME, 0)

#: The rules ``status`` (and ``active`` with no ``--rule``) report (TD7).
STATUS_ROLL_RULES: tuple[RollRule, ...] = (
    DEFAULT_ROLL_RULE,
    RollRule(RollMethod.CALENDAR),
)
