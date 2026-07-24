# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Static guards for the two Python floors this repository has.

The daemon and its tooling run on the interpreter `pyproject.toml` declares. The add-on payloads
under `trackball_daemon/plugins/` never run there: host applications execute them inside their own
embedded Python, so their floor is set by the oldest supported host rather than by this project.
Rhino 8 embeds CPython 3.9, which is what pins that floor today.

Raising the project's floor must not raise the payload floor with it. That is the whole reason
these two are separated here.
"""

import ast
from pathlib import Path
import re
import warnings


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_TREE = ROOT / "trackball_daemon" / "plugins"
PAYLOAD_FLOOR = "3.9"
PAYLOAD_FLOOR_REASON = "Rhino 8 embeds CPython 3.9 and executes these files itself"


def _parse(path, record=None):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tree = ast.parse(path.read_text(encoding="utf-8"))
    if record is not None:
        # Only warnings about the source being compiled. Unrelated warnings from elsewhere in the
        # process can land in the recorder and say nothing about this file.
        record.extend(
            (path, warning) for warning in caught
            if issubclass(warning.category, SyntaxWarning))
    return tree


def _payload_sources(record=None):
    for path in sorted(PAYLOAD_TREE.rglob("*.py")):
        yield path, _parse(path, record)


def _uses_pep604_annotation(tree):
    annotations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            annotations.append(node.returns)
    return any(
        isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr)
        for annotation in annotations
        for child in ast.walk(annotation)
    )


def test_declared_floor_is_what_continuous_integration_actually_tests():
    """A floor nothing tests is a claim, not a guarantee.

    This repository shipped a dependency set CI never exercised because the two disagreed, so the
    agreement is asserted rather than assumed.
    """
    declared = re.search(
        r'requires-python\s*=\s*">=(\d+\.\d+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1)
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert f'python-version: "{declared}"' in workflow
    for command in re.findall(r"uv sync [^\n]*--python (\d+\.\d+)", workflow):
        assert command == declared, (
            f"CI synchronizes Python {command} but the package declares >={declared}")


def test_payload_floor_is_recorded_with_the_host_that_sets_it():
    """The payload floor is a host constraint, so it must not be silently lowered or raised."""
    contributing = " ".join((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())

    assert PAYLOAD_FLOOR in contributing
    assert "Rhino" in contributing


def test_payloads_parse_without_syntax_warnings():
    """An invalid escape sequence is a SyntaxWarning today and a SyntaxError in a future Python.

    Payloads are executed by host interpreters this project does not control or pin, so a warning
    that a newer host turns into an error has to be fixed here rather than tolerated.
    """
    record = []
    list(_payload_sources(record))
    reported = [
        f"{path.relative_to(ROOT)}: {warning.category.__name__}: {warning.message}"
        for path, warning in record]

    assert reported == []


def test_payload_annotations_stay_within_the_host_interpreter_floor():
    offenders = []
    for path, tree in _payload_sources():
        if not _uses_pep604_annotation(tree):
            continue
        future_annotations = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in tree.body
        )
        if not future_annotations:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        f"PEP 604 annotations are evaluated at runtime on Python {PAYLOAD_FLOOR}; "
        f"{PAYLOAD_FLOOR_REASON}")


def test_payloads_avoid_syntax_newer_than_the_host_interpreter_floor():
    """`match` is 3.10 syntax and will not even parse in a 3.9 host."""
    offenders = []
    for path, tree in _payload_sources():
        for node in ast.walk(tree):
            if isinstance(node, getattr(ast, "Match", ())):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], f"match statements need 3.10; {PAYLOAD_FLOOR_REASON}"


def test_payloads_avoid_path_helpers_newer_than_the_host_interpreter_floor():
    """`Path.read_text`/`write_text` gained `newline` in 3.13; on 3.9 it is a TypeError."""
    offenders = []
    for path, tree in _payload_sources():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"read_text", "write_text"}
                    and any(keyword.arg == "newline" for keyword in node.keywords)):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], (
        f"use Path.open(newline=...) in payloads; {PAYLOAD_FLOOR_REASON}")
