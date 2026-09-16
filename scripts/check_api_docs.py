"""Check the hand-written API documents against the committed OpenAPI artifact.

The two consumer documents — ``docs/api/reference.md`` and ``docs/api/agents.md``
— are hand-written on purpose (slice 190 D4): the value in them is exactly the
part a generator cannot produce. This gate checks the properties a machine
*can* verify, so neither document can go quietly wrong the way the FastAPI
route ``description`` did.

Run:
    uv run python scripts/check_api_docs.py            # report, always exit 0
    uv run python scripts/check_api_docs.py --check    # exit 1 on any failure

Reads the committed ``docs/api/openapi.json``, not the live app: the artifact
is what consumers hold. No database and no ``MT_TIMESCALE_DB_URL`` are needed.

How a document declares what it documents
-----------------------------------------
Paths, parameters and statuses are declared with **explicit HTML-comment
markers**, never inferred from prose. The design says the gate "parses both
documents' path and parameter mentions"; inferring those from English with a
regex is the fragile-parsing trap this project's rules name — a sentence that
mentions ``/api/v1/status`` in passing is not documentation of it, and a
parameter named mid-sentence would be missed or double-counted depending on
punctuation. A marker states the intent (slice 190, task 2.1).

Each documented endpoint section carries one endpoint marker::

    <!-- endpoint: /api/v1/bars/{symbol}
         params: granularity:Granularity, start:date, end:date,
                 adjusted:boolean, format:json|msgpack
         errors: 404, 422, 500, 504 -->

``params`` and ``errors`` are optional and may wrap across lines; ``params: -``
and ``errors: -`` state "none" explicitly, so a section that forgot the line
and one that means none are distinguishable. Parameter types use the canonical
spelling this module derives from the artifact (see ``canonical_type``), which
``--check``'s failure output prints for any mismatch.

A value that must track code carries a value marker naming the symbol::

    The ceiling is 75,000 rows.
    <!-- from: manta_trading.constants.API_MAX_BARS_PER_REQUEST -->

The gate imports the symbol and compares it to the documented value: scalars
by value, enums as **token sets** (the members written in backticks), so a new
enum member fails rather than quietly extending a documented list. The value is
read from the marker's own line, or from the line above when the marker stands
alone — a long dotted path does not fit beside prose and stay inside the line
length.

What this gate cannot check, stated plainly because pretending otherwise would
be worse than the gap: whether a sentence of prose is *true*. It knows a
section claims to document a path that exists, with the parameters that exist.
Prose accuracy is the reviewer's job and the verification walkthrough's.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _artifact_path() -> Path:
    """Reuse ``dump_openapi.ARTIFACT_PATH`` rather than recomputing it.

    ``scripts/`` is not an installed package, so the sibling module is loaded
    by path — the same reason and the same pattern as
    ``test_openapi_artifact.py``. Recomputing the location here would give the
    artifact two definitions that could drift apart.
    """
    script_path = Path(__file__).resolve().parent / "dump_openapi.py"
    spec = importlib.util.spec_from_file_location("dump_openapi", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path: Path = module.ARTIFACT_PATH
    return path


ARTIFACT_PATH = _artifact_path()
"""The committed schema, as ``dump_openapi.py`` defines it."""

REFERENCE_PATH = REPO_ROOT / "docs" / "api" / "reference.md"
"""The human reference (route-indexed)."""

AGENTS_PATH = REPO_ROOT / "docs" / "api" / "agents.md"
"""The agent reference (capability-indexed)."""

DOCUMENT_PATHS = (REFERENCE_PATH, AGENTS_PATH)

NONE_MARKER = "-"
"""Literal that states a marker field is deliberately empty."""

ENDPOINT_MARKER = re.compile(
    r"<!--\s*endpoint:\s*(?P<body>.*?)-->",
    re.DOTALL,
)
"""``<!-- endpoint: /path … -->``, possibly spanning lines."""

VALUE_MARKER = re.compile(r"<!--\s*from:\s*(?P<symbol>[\w.]+)\s*-->")
"""``<!-- from: dotted.symbol.path -->``, closing the line it documents."""

_MARKER_FIELD = re.compile(
    r"^\s*(?P<key>params|errors)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)

REMEDY = (
    "fix the document, not the gate: a check relaxed to make prose pass is the "
    "drift this slice exists to prevent"
)


@dataclass(frozen=True)
class EndpointMarker:
    """One ``<!-- endpoint: -->`` block, as written in a document."""

    document: Path
    path: str
    params: dict[str, str]
    errors: frozenset[str]
    line: int

    @property
    def where(self) -> str:
        return f"{self.document.name}:{self.line}"


@dataclass(frozen=True)
class ValueMarker:
    """One ``<!-- from: -->`` marker and the prose line that carries it."""

    document: Path
    symbol: str
    text: str
    line: int

    @property
    def where(self) -> str:
        return f"{self.document.name}:{self.line}"


@dataclass
class Report:
    """Accumulated failures and the counts a passing run prints."""

    failures: list[str] = field(default_factory=list)
    paths_checked: int = 0
    params_checked: int = 0
    statuses_checked: int = 0
    markers_checked: int = 0

    def fail(self, message: str) -> None:
        self.failures.append(message)

    @property
    def ok(self) -> bool:
        return not self.failures


# --- Reading the artifact ---------------------------------------------------


def load_artifact(path: Path = ARTIFACT_PATH) -> dict[str, Any]:
    """Load the committed schema. Raises ``FileNotFoundError`` if absent."""
    text = path.read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def canonical_type(schema: dict[str, Any]) -> str:
    """Render a parameter's schema as the one spelling a document must use.

    FastAPI emits four shapes for what a reader thinks of as one type: a plain
    ``type``, a ``$ref`` to an enum, an ``anyOf`` union (how ``X | None`` and
    ``date | datetime`` arrive), and an inline ``enum`` list. Without one
    canonical rendering, "the documented type" is not a well-defined thing to
    compare against and the check would be unwritable.

    Nullability is deliberately **not** part of the rendering: every optional
    query parameter is nullable, so carrying it would add ``|null`` to nearly
    every row of every table while distinguishing nothing.
    """
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]

    if "anyOf" in schema:
        rendered = [
            canonical_type(member)
            for member in schema["anyOf"]
            if member.get("type") != "null"
        ]
        deduped = list(dict.fromkeys(rendered))
        return "|".join(deduped) if deduped else "null"

    if "enum" in schema:
        return "|".join(str(value) for value in schema["enum"])

    declared = schema.get("type")
    if declared is None:
        raise ValueError(f"parameter schema declares no type: {schema}")
    # ``format`` is what separates a date from a timestamp, and the two have
    # different window semantics — the reason the reference has a time base
    # column at all.
    if declared == "string" and "format" in schema:
        return str(schema["format"])
    return str(declared)


def artifact_parameters(operation: dict[str, Any]) -> dict[str, str]:
    """Query parameters of one operation, name to canonical type."""
    return {
        parameter["name"]: canonical_type(parameter["schema"])
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query"
    }


def artifact_statuses(operation: dict[str, Any]) -> frozenset[str]:
    """Status codes the operation declares."""
    return frozenset(operation.get("responses", {}))


# --- Reading the documents --------------------------------------------------


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


FENCE = re.compile(r"^\s*(?:```|~~~)")


def blank_fenced_blocks(text: str) -> str:
    """Replace fenced code block contents with blank lines.

    A document that explains its own marker convention shows one, and a marker
    inside a fence is an example rather than a declaration — parsing it makes
    the "Keeping this accurate" section fail the gate it documents. Lines are
    blanked rather than removed so every reported line number still matches the
    file as an author sees it.
    """
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if FENCE.match(line):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def _parse_params(raw: str, where: str) -> dict[str, str]:
    if raw.strip() == NONE_MARKER:
        return {}
    params: dict[str, str] = {}
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        name, separator, declared = entry.partition(":")
        if not separator:
            raise ValueError(
                f"{where}: parameter '{entry}' is missing its type; "
                f"write 'name:type' (or 'params: {NONE_MARKER}' for none)"
            )
        params[name.strip()] = declared.strip()
    return params


def _parse_errors(raw: str, where: str) -> frozenset[str]:
    if raw.strip() == NONE_MARKER:
        return frozenset()
    statuses = {part.strip() for part in raw.split(",") if part.strip()}
    for status in statuses:
        if not status.isdigit():
            raise ValueError(f"{where}: '{status}' is not a status code")
    return frozenset(statuses)


def parse_endpoint_markers(document: Path, text: str) -> list[EndpointMarker]:
    """Extract every ``<!-- endpoint: -->`` block from one document."""
    markers: list[EndpointMarker] = []
    for match in ENDPOINT_MARKER.finditer(text):
        line = _line_of(text, match.start())
        where = f"{document.name}:{line}"
        body = match.group("body")
        lines = body.splitlines()
        path = lines[0].strip()
        if not path.startswith("/"):
            raise ValueError(
                f"{where}: endpoint marker must open with a path, got {path!r}"
            )
        params: dict[str, str] = {}
        errors: frozenset[str] = frozenset()
        # Fields may wrap: a continuation line belongs to the field above it.
        current: str | None = None
        buffered: dict[str, list[str]] = {"params": [], "errors": []}
        for raw_line in lines[1:]:
            field_match = _MARKER_FIELD.match(raw_line)
            if field_match:
                current = field_match.group("key").lower()
                buffered[current].append(field_match.group("value"))
            elif current and raw_line.strip():
                buffered[current].append(raw_line.strip())
        if buffered["params"]:
            params = _parse_params(" ".join(buffered["params"]), where)
        if buffered["errors"]:
            errors = _parse_errors(" ".join(buffered["errors"]), where)
        markers.append(
            EndpointMarker(
                document=document,
                path=path,
                params=params,
                errors=errors,
                line=line,
            )
        )
    return markers


def _ends_a_sentence(line: str) -> bool:
    """Whether ``line`` closes a sentence, so the line below starts a new one.

    A trailing colon counts: the sentence that introduces a list is not part of
    the list it introduces.
    """
    return line.rstrip().endswith((".", ":", "!", "?"))


def parse_value_markers(document: Path, text: str) -> list[ValueMarker]:
    """Extract every ``<!-- from: -->`` marker with the prose it documents.

    The documented value is the **sentence** the marker closes, which may wrap
    across lines: text is collected upwards from the marker until a sentence
    end (or a blank line) is found. A token set long enough to need a marker is
    often long enough to wrap, and reading a single line would silently check
    half of it. Stopping at the sentence boundary rather than the paragraph's
    is what keeps the check narrow — a neighbouring sentence naming
    ``granularity`` in backticks must not be read as part of a documented
    token set.
    """
    lines = text.splitlines()
    markers: list[ValueMarker] = []
    for match in VALUE_MARKER.finditer(text):
        line = _line_of(text, match.start())
        carrier = lines[line - 1]
        own = carrier[: carrier.index("<!--")].strip()
        collected = [own] if own else []
        # Walk upwards until the collected text is a whole sentence: stop as
        # soon as a line has been taken that the line above it ends before.
        for above in range(line - 2, -1, -1):
            if collected and _ends_a_sentence(lines[above]):
                break
            candidate = lines[above].strip()
            if not candidate:
                break
            collected.append(candidate)
        prose = " ".join(part for part in reversed(collected) if part)
        markers.append(
            ValueMarker(
                document=document,
                symbol=match.group("symbol"),
                text=prose,
                line=line,
            )
        )
    return markers


# --- The checks -------------------------------------------------------------


def check_paths(
    report: Report,
    declared: set[str],
    markers_by_document: dict[Path, list[EndpointMarker]],
) -> None:
    """Every declared path is documented in both files; every documented path
    exists."""
    for document, markers in markers_by_document.items():
        documented = {marker.path for marker in markers}
        for path in sorted(declared - documented):
            report.fail(
                f"{document.name}: {path} is declared in "
                f"{ARTIFACT_PATH.name} but not documented; add an endpoint "
                f"section with '<!-- endpoint: {path} -->'"
            )
        for path in sorted(documented - declared):
            marker = next(m for m in markers if m.path == path)
            report.fail(
                f"{marker.where}: {path} is documented but not declared in "
                f"{ARTIFACT_PATH.name}; the route was renamed or removed — "
                f"{REMEDY}"
            )
        for path in sorted(documented):
            occurrences = [m for m in markers if m.path == path]
            if len(occurrences) > 1:
                lines = ", ".join(str(m.line) for m in occurrences)
                report.fail(
                    f"{document.name}: {path} carries {len(occurrences)} "
                    f"endpoint markers (lines {lines}); one section per path"
                )


def check_parameters(
    report: Report,
    operations: dict[str, dict[str, Any]],
    markers: list[EndpointMarker],
) -> None:
    """Documented parameters must exist with the documented type, and every
    declared parameter must be documented."""
    for marker in markers:
        operation = operations.get(marker.path)
        if operation is None:
            continue  # already reported as an unknown path
        declared = artifact_parameters(operation)
        for name, documented_type in sorted(marker.params.items()):
            report.params_checked += 1
            if name not in declared:
                report.fail(
                    f"{marker.where}: {marker.path} documents parameter "
                    f"'{name}', which the route does not declare; it declares "
                    f"{sorted(declared) or 'no query parameters'}"
                )
                continue
            if declared[name] != documented_type:
                report.fail(
                    f"{marker.where}: {marker.path} parameter '{name}' is "
                    f"documented as '{documented_type}' but declared as "
                    f"'{declared[name]}'"
                )
        for name in sorted(set(declared) - set(marker.params)):
            report.fail(
                f"{marker.where}: {marker.path} declares parameter '{name}' "
                f"({declared[name]}) which is not documented; add it to the "
                f"marker's params and to the field table"
            )


def check_statuses(
    report: Report,
    operations: dict[str, dict[str, Any]],
    markers: list[EndpointMarker],
) -> None:
    """Documented error statuses must be a subset of the declared statuses.

    Subset, not equality: a document may reasonably omit a status it has
    nothing useful to say about, but must never promise one the route cannot
    send. ``/api/v1/credits`` declares only ``200`` — it issues no statement
    (189 D8) — so documenting a ``504`` there is the defining failure.
    """
    for marker in markers:
        operation = operations.get(marker.path)
        if operation is None:
            continue
        declared = artifact_statuses(operation)
        for status in sorted(marker.errors):
            report.statuses_checked += 1
            if status not in declared:
                report.fail(
                    f"{marker.where}: {marker.path} documents status "
                    f"{status}, which the route does not declare; it declares "
                    f"{sorted(declared)}"
                )


def resolve_symbol(dotted: str) -> Any:
    """Import ``package.module.SYMBOL`` and return the attribute.

    Raises ``ImportError`` or ``AttributeError`` — never returns a sentinel.
    Silently skipping an unresolvable marker would defeat the check the marker
    exists to perform.
    """
    module_path, _, attribute = dotted.rpartition(".")
    if not module_path:
        raise ImportError(f"'{dotted}' is not a dotted path to a symbol")
    module = importlib.import_module(module_path)
    try:
        return getattr(module, attribute)
    except AttributeError as error:
        raise AttributeError(
            f"module '{module_path}' has no attribute '{attribute}'"
        ) from error


def _documented_tokens(text: str) -> set[str]:
    """Tokens quoted in backticks on the marker's line."""
    return set(re.findall(r"`([^`]+)`", text))


def _documented_number(text: str) -> str | None:
    """The last number on the line, thousands separators removed."""
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    return numbers[-1].replace(",", "") if numbers else None


def check_value_markers(report: Report, markers: list[ValueMarker]) -> None:
    """Resolve each ``from:`` marker and compare it to the documented value.

    Enums compare as token sets so a new member fails rather than quietly
    extending a documented list; scalars compare by value.
    """
    for marker in markers:
        report.markers_checked += 1
        try:
            symbol = resolve_symbol(marker.symbol)
        except (ImportError, AttributeError) as error:
            report.fail(
                f"{marker.where}: marker '{marker.symbol}' does not resolve "
                f"({error}); a marker naming an unimportable symbol is not a "
                f"check — {REMEDY}"
            )
            continue

        if isinstance(symbol, type) and issubclass(symbol, Enum):
            expected = {str(member.value) for member in symbol}
            documented = _documented_tokens(marker.text)
            if documented != expected:
                missing = sorted(expected - documented)
                extra = sorted(documented - expected)
                report.fail(
                    f"{marker.where}: {marker.symbol} token set differs — "
                    f"missing {missing or 'nothing'}, "
                    f"undeclared {extra or 'nothing'}; "
                    f"the symbol defines {sorted(expected)}"
                )
            continue

        documented_number = _documented_number(marker.text)
        if documented_number is None:
            report.fail(
                f"{marker.where}: {marker.symbol} resolves to {symbol!r} but "
                f"the line states no value to compare it against"
            )
            continue
        if documented_number != str(symbol):
            report.fail(
                f"{marker.where}: {marker.symbol} is {symbol!r} but the line "
                f"documents {documented_number}"
            )


# --- Orchestration ----------------------------------------------------------


def run_checks(
    artifact_path: Path = ARTIFACT_PATH,
    document_paths: tuple[Path, ...] = DOCUMENT_PATHS,
) -> Report:
    """Run every check and return the accumulated report."""
    report = Report()

    try:
        artifact = load_artifact(artifact_path)
    except FileNotFoundError:
        report.fail(
            f"missing artifact: {artifact_path}; "
            "run: uv run python scripts/dump_openapi.py"
        )
        return report

    operations = {
        path: operation["get"]
        for path, operation in artifact["paths"].items()
        if "get" in operation
    }
    declared_paths = set(operations)
    report.paths_checked = len(declared_paths)

    # A missing or unparseable document contributes no markers, which the path
    # check then reports as every path being undocumented in it. That is the
    # accurate reading — an absent reference documents nothing — and it names
    # each path rather than reporting a bare count.
    markers_by_document: dict[Path, list[EndpointMarker]] = {}
    value_markers: list[ValueMarker] = []
    for document in document_paths:
        markers_by_document[document] = []
        if not document.exists():
            report.fail(
                f"missing document: {document}; slice 190 delivers it — "
                f"the gate cannot check a file that is not there"
            )
            continue
        text = blank_fenced_blocks(document.read_text(encoding="utf-8"))
        try:
            markers_by_document[document] = parse_endpoint_markers(document, text)
        except ValueError as error:
            report.fail(str(error))
            continue
        value_markers.extend(parse_value_markers(document, text))

    check_paths(report, declared_paths, markers_by_document)
    for markers in markers_by_document.values():
        check_parameters(report, operations, markers)
        check_statuses(report, operations, markers)
    check_value_markers(report, value_markers)
    return report


def print_report(report: Report) -> None:
    """Print what was checked, then any failures."""
    print(
        f"checked {report.paths_checked} paths, "
        f"{report.params_checked} documented parameters, "
        f"{report.statuses_checked} documented statuses, "
        f"{report.markers_checked} from: markers"
    )
    for failure in report.failures:
        print(f"  FAIL {failure}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 on any failure instead of reporting and exiting 0",
    )
    args = parser.parse_args(argv)

    report = run_checks()
    print_report(report)

    if not args.check:
        return 0
    if report.ok:
        print(f"docs/api documentation is consistent with {ARTIFACT_PATH.name}")
        return 0
    print(
        f"{len(report.failures)} documentation failure(s); {REMEDY}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
