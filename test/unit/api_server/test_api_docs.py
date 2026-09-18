"""The hand-written API documents must stay consistent with the artifact.

Runs ``scripts/check_api_docs.py`` in the suite (slice 190 D4), alongside the
artifact drift test. Two kinds of test live here, and the second is the reason
the file is worth reading:

1. The gate passes on the committed tree.
2. The gate **fails** on each class of drift it exists to catch. A gate never
   observed to fail is not a gate — it is a function that returns zero. Each
   mutation is applied to a copied fixture, never to the committed artifact.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_gate() -> Any:
    """Import ``scripts/check_api_docs.py`` by path.

    ``scripts/`` is not an installed package, and making it one to satisfy a
    test would change what this project ships — the same reasoning as
    ``test_openapi_artifact.py``. The module is registered in ``sys.modules``
    before execution because it defines dataclasses, and ``@dataclass``
    resolves annotations through the module entry.
    """
    script_path = REPO_ROOT / "scripts" / "check_api_docs.py"
    spec = importlib.util.spec_from_file_location("check_api_docs", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


# --- The gate on the committed tree ----------------------------------------


def test_gate_passes_on_the_committed_tree() -> None:
    """Both documents cover the whole surface (slice 190, task 5.1).

    Fails until sections 3 and 4 land — that is the point of writing the gate
    before the prose.
    """
    report = gate.run_checks()
    assert report.ok, "\n".join(report.failures)


def test_gate_checks_every_declared_path() -> None:
    """The report's path count is the artifact's, not a number typed here."""
    report = gate.run_checks()
    declared = json.loads(gate.ARTIFACT_PATH.read_text(encoding="utf-8"))["paths"]
    assert report.paths_checked == len(declared)


def test_gate_needs_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate reads the committed artifact, so it runs on any checkout."""
    monkeypatch.delenv("MT_TIMESCALE_DB_URL", raising=False)
    assert gate.run_checks().paths_checked > 0


# --- Fixtures: a mutable copy of the real artifact and real documents -------


@pytest.fixture
def artifact() -> dict[str, Any]:
    """The committed schema, parsed. Mutating this dict cannot touch disk."""
    loaded: dict[str, Any] = json.loads(gate.ARTIFACT_PATH.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture
def tree(tmp_path: Path, artifact: dict[str, Any]) -> Any:
    """A throwaway tree: the artifact plus two minimal, *passing* documents.

    The documents are rendered once from the **pristine** schema and never
    re-rendered; ``schema`` is the copy a test mutates. Rendering them from the
    mutated schema instead would carry every mutation into the prose, leaving
    the two sides in agreement and the gate with nothing to catch — the fixture
    would silently assert nothing.
    """
    pristine = json.loads(json.dumps(artifact))

    class Tree:
        def __init__(self) -> None:
            self.artifact_path = tmp_path / "openapi.json"
            self.reference = tmp_path / "reference.md"
            self.agents = tmp_path / "agents.md"
            self.schema = artifact
            self._documents = _documents_for(pristine)

        def write(self) -> None:
            self.artifact_path.write_text(
                json.dumps(self.schema, indent=2), encoding="utf-8"
            )
            if not self.reference.exists():
                self.reference.write_text(self._documents, encoding="utf-8")
                self.agents.write_text(self._documents, encoding="utf-8")

        def run(self) -> Any:
            self.write()
            return gate.run_checks(
                artifact_path=self.artifact_path,
                document_paths=(self.reference, self.agents),
            )

    return Tree()


def _documents_for(schema: dict[str, Any]) -> str:
    """Render endpoint markers covering every path the schema declares."""
    blocks = ["# Fixture document\n"]
    for path, operations in sorted(schema["paths"].items()):
        operation = operations["get"]
        params = gate.artifact_parameters(operation)
        statuses = sorted(gate.artifact_statuses(operation))
        rendered = (
            ", ".join(f"{name}:{kind}" for name, kind in sorted(params.items()))
            or gate.NONE_MARKER
        )
        blocks.append(
            f"## `GET {path}`\n\n"
            f"<!-- endpoint: {path}\n"
            f"     params: {rendered}\n"
            f"     errors: {', '.join(statuses)} -->\n"
        )
    return "\n".join(blocks)


def _failures(report: Any) -> str:
    return "\n".join(report.failures)


def test_fixture_tree_passes_before_mutation(tree: Any) -> None:
    """The baseline every mutation test depends on."""
    report = tree.run()
    assert report.ok, _failures(report)


# --- Proof the gate fails: the three drift classes --------------------------


def test_added_path_fails(tree: Any) -> None:
    """A route ships and nobody documents it."""
    tree.schema["paths"]["/api/v1/newly/added"] = {
        "get": {"responses": {"200": {"description": "ok"}}}
    }
    report = tree.run()
    assert not report.ok
    assert any("/api/v1/newly/added" in failure for failure in report.failures), (
        _failures(report)
    )
    assert any("not documented" in failure for failure in report.failures)


def test_removed_path_fails(tree: Any) -> None:
    """A route is removed and the documents keep describing it."""
    tree.schema["paths"].pop("/api/v1/overview")
    report = tree.run()
    assert not report.ok
    assert any(
        "/api/v1/overview" in failure and "not declared" in failure
        for failure in report.failures
    ), _failures(report)


def test_renamed_parameter_fails(tree: Any) -> None:
    """A parameter is renamed under a document that still names the old one."""
    parameters = tree.schema["paths"]["/api/v1/symbols"]["get"]["parameters"]
    renamed = next(p for p in parameters if p["name"] == "search")
    renamed["name"] = "query"
    report = tree.run()
    assert not report.ok
    joined = _failures(report)
    assert "search" in joined and "query" in joined, joined
    assert "/api/v1/symbols" in joined


def test_retyped_parameter_fails(tree: Any) -> None:
    """A parameter keeps its name and changes its type."""
    parameters = tree.schema["paths"]["/api/v1/status"]["get"]["parameters"]
    retyped = next(p for p in parameters if p["name"] == "all")
    retyped["schema"] = {"type": "string", "title": "All"}
    report = tree.run()
    assert not report.ok
    assert any(
        "'all'" in failure and "boolean" in failure for failure in report.failures
    ), _failures(report)


def test_changed_ceiling_value_fails(tree: Any) -> None:
    """The documented ceiling stops matching the constant."""
    marker = "manta_trading.constants.API_MAX_BARS_PER_REQUEST"
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nThe ceiling is 999 rows. <!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any(marker in failure and "999" in failure for failure in report.failures), (
        _failures(report)
    )


def test_matching_ceiling_value_passes(tree: Any) -> None:
    """The same marker with the real value does not fail — the mutation test
    above must fail for its value, not for carrying a marker at all."""
    from manta_trading.constants import API_MAX_BARS_PER_REQUEST

    marker = "manta_trading.constants.API_MAX_BARS_PER_REQUEST"
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nThe ceiling is {API_MAX_BARS_PER_REQUEST:,} rows. "
        f"<!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


# --- The credits 504 case (task 2.4's defining case) ------------------------


def test_documenting_a_504_on_credits_fails(tree: Any) -> None:
    """``/api/v1/credits`` declares only ``200``: it issues no statement about
    provider reachability (189 D8), so a documented ``504`` is a promise the
    route cannot keep."""
    tree.write()
    text = tree.reference.read_text(encoding="utf-8").replace(
        "<!-- endpoint: /api/v1/credits\n     params: -\n     errors: 200 -->",
        "<!-- endpoint: /api/v1/credits\n     params: -\n     errors: 200, 504 -->",
    )
    assert "errors: 200, 504" in text, "the credits marker was not rewritten"
    tree.reference.write_text(text, encoding="utf-8")
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any(
        "/api/v1/credits" in failure and "504" in failure for failure in report.failures
    ), _failures(report)


def test_omitting_a_declared_status_passes(tree: Any) -> None:
    """Statuses are checked as a subset: a document may stay silent about a
    status, but never invent one."""
    tree.write()
    before = tree.reference.read_text(encoding="utf-8")
    text = before.replace("     errors: 200, 422, 504 -->", "     errors: 200 -->")
    # Guard the rewrite. Without this, a change to the artifact's status set
    # makes the needle vanish, `replace` a no-op, and the test a tautology
    # that passes while asserting nothing about the subset check.
    assert text != before, (
        "fixture drift: no marker carried 'errors: 200, 422, 504', so this "
        "test would pass without omitting any status"
    )
    tree.reference.write_text(text, encoding="utf-8")
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


# --- Marker resolution ------------------------------------------------------


def test_unresolvable_marker_fails(tree: Any) -> None:
    """An unresolvable marker fails rather than being skipped — silently
    ignoring one defeats the check it was written to perform."""
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + "\nSomething. <!-- from: manta_trading.constants.NO_SUCH_SYMBOL -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any(
        "NO_SUCH_SYMBOL" in failure and "does not resolve" in failure
        for failure in report.failures
    ), _failures(report)


def test_unimportable_module_in_marker_fails(tree: Any) -> None:
    """The module half of the dotted path is resolved too."""
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + "\nSomething. <!-- from: manta_trading.no_such_module.THING -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any("no_such_module" in failure for failure in report.failures)


def test_enum_missing_a_member_fails(tree: Any) -> None:
    """Enums compare as token sets, so a new member fails rather than quietly
    extending a documented list."""
    from manta_trading.data.acquisition.pass_runs import PassKind

    marker = "manta_trading.data.acquisition.pass_runs.PassKind"
    members = sorted(str(member.value) for member in PassKind)
    documented = " ".join(f"`{member}`" for member in members[:-1])
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nKinds: {documented}. <!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any(
        members[-1] in failure and "PassKind" in failure for failure in report.failures
    ), _failures(report)


def test_complete_enum_set_passes(tree: Any) -> None:
    """The full token set resolves clean — the counterpart to the test above."""
    from manta_trading.data.acquisition.pass_runs import PassKind

    marker = "manta_trading.data.acquisition.pass_runs.PassKind"
    documented = " ".join(f"`{member.value}`" for member in PassKind)
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nKinds: {documented}. <!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


# --- Missing documents and malformed markers --------------------------------


def test_missing_documents_fail_by_name(tmp_path: Path) -> None:
    """A missing document is named, not a traceback."""
    absent = tmp_path / "absent.md"
    report = gate.run_checks(document_paths=(absent,))
    assert not report.ok
    assert any(
        "missing document" in failure and absent.name in failure
        for failure in report.failures
    ), _failures(report)


def test_malformed_marker_fails_with_its_location(tree: Any) -> None:
    """A parameter without a type is a marker bug, reported with its line."""
    tree.write()
    before = tree.reference.read_text(encoding="utf-8")
    text = before.replace("     params: search:string", "     params: search")
    # A drifted needle here fails the test rather than passing it vacuously,
    # but the guard says *why* — otherwise the failure reads as "the gate
    # stopped catching malformed markers" when the fixture simply changed.
    assert text != before, (
        "fixture drift: no marker carried 'params: search:string', so nothing "
        "was made malformed"
    )
    tree.reference.write_text(text, encoding="utf-8")
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any("missing its type" in failure for failure in report.failures), _failures(
        report
    )


def test_duplicate_endpoint_marker_fails(tree: Any) -> None:
    """One section per path: two markers for one path means two descriptions
    to keep in step, which is the condition this slice ends."""
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + "\n<!-- endpoint: /api/v1/health\n     params: -\n     errors: 200 -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any(
        "/api/v1/health" in failure and "endpoint markers" in failure
        for failure in report.failures
    ), _failures(report)


def test_wrapped_token_set_resolves(tree: Any) -> None:
    """A token set that wraps across lines is read whole.

    Reading only the marker's own line would check half the set and report the
    wrapped-off members as missing, pushing an author to reformat correct prose
    to appease the gate.
    """
    from manta_trading.data.kalshi.constants import MarketStatus

    marker = "manta_trading.data.kalshi.constants.MarketStatus"
    members = [f"`{member.value}`" for member in MarketStatus]
    half = len(members) // 2
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nStatus tokens are {' '.join(members[:half])}\n"
        f"{' '.join(members[half:])}.\n<!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


def test_marker_does_not_read_the_preceding_sentence(tree: Any) -> None:
    """The carrier is one sentence, not the paragraph.

    Widening it to the paragraph makes a neighbouring sentence's backticked
    words part of the documented set — which is how this boundary was chosen:
    a `granularity` mention one line above a Granularity token set failed the
    gate until the scope was narrowed.
    """
    from manta_trading.data.acquisition.pass_runs import PassKind

    marker = "manta_trading.data.acquisition.pass_runs.PassKind"
    members = " ".join(f"`{member.value}`" for member in PassKind)
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nThe `pass` field names a kind.\nKinds are {members}.\n"
        f"<!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


def test_markers_inside_fenced_blocks_are_examples(tree: Any) -> None:
    """A marker shown inside a code fence documents the convention; it does not
    declare anything.

    Without this, the "Keeping this accurate" section — which must show an
    endpoint marker and a from: marker to explain them — fails the gate it
    describes: a duplicate path, and a from: marker whose surrounding prose is
    the explanation rather than a token set.
    """
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8") + "\n## How to write one\n\n```\n"
        "<!-- endpoint: /api/v1/health\n     params: -\n     errors: 200 -->\n"
        "```\n\n```\nTokens are `a` `b`.\n"
        "<!-- from: manta_trading.constants.NO_SUCH_SYMBOL -->\n```\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


def test_inline_marker_mention_is_not_a_declaration(tree: Any) -> None:
    """Naming the convention in prose does not declare a path.

    The "Keeping this accurate" sections describe the marker format in inline
    code. Parsing those mentions made an empty marker — which crashed the gate
    with an IndexError rather than reporting anything.
    """
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + "\nEach section carries a `<!-- endpoint: -->` block, and a value "
        "may carry `<!-- from: some.symbol -->`.\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


def test_empty_marker_reports_rather_than_crashes(tree: Any) -> None:
    """A malformed marker is a failure with a location, never a traceback."""
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8") + "\n<!-- endpoint: -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any("must open with a path" in failure for failure in report.failures), (
        _failures(report)
    )


def test_unclosed_fence_is_named_as_the_cause(tree: Any) -> None:
    """A missing closing fence is reported as itself, not as a wall of
    'path not documented' failures pointing away from the cause.

    Everything below an unterminated fence reads as a code block, so its
    markers become invisible and every path below it looks undocumented.
    """
    tree.write()
    text = tree.reference.read_text(encoding="utf-8")
    tree.reference.write_text("```sh\n" + text, encoding="utf-8")
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert "never closed" in report.failures[0], (
        f"the fence must be reported first, got: {report.failures[0]}"
    )


def test_balanced_fences_report_no_fence_error(tree: Any) -> None:
    """The counterpart: a document whose fences pair cleanly says nothing
    about fences, so the check above fails for its fence and not for merely
    containing one."""
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8") + "\n```sh\necho hi\n```\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


def test_scalar_matches_any_number_in_the_sentence(tree: Any) -> None:
    """A tracked scalar need only appear in its sentence.

    Taking the *last* number instead would compare '10' to the ceiling here
    and fail correct prose.
    """
    from manta_trading.constants import API_MAX_BARS_PER_REQUEST

    marker = "manta_trading.constants.API_MAX_BARS_PER_REQUEST"
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nThe ceiling is {API_MAX_BARS_PER_REQUEST:,} rows across 10 tables. "
        f"<!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert report.ok, _failures(report)


def test_scalar_absent_from_the_sentence_fails(tree: Any) -> None:
    """The check still fails when the tracked value is nowhere in the
    sentence — the leniency above must not become 'any number will do'."""
    marker = "manta_trading.constants.API_MAX_BARS_PER_REQUEST"
    tree.write()
    tree.reference.write_text(
        tree.reference.read_text(encoding="utf-8")
        + f"\nThe ceiling is 999 rows across 10 tables. <!-- from: {marker} -->\n",
        encoding="utf-8",
    )
    report = gate.run_checks(
        artifact_path=tree.artifact_path,
        document_paths=(tree.reference, tree.agents),
    )
    assert not report.ok
    assert any(marker in failure for failure in report.failures), _failures(report)
