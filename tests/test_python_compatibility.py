"""Static guard for the oldest Python version promised by package metadata and CI."""

import ast
from pathlib import Path
import warnings


ROOT = Path(__file__).resolve().parents[1]


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


def test_pep604_runtime_annotations_are_postponed_for_python_39():
    offenders = []
    for path in sorted((ROOT / "trackball_daemon").rglob("*.py")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(path.read_text(encoding="utf-8"))
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
    assert offenders == []
