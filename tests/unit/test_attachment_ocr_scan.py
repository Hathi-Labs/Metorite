"""CIV-33d: the source-tree scan of ``test_attachment_ocr.py``.

``_read_calls`` parses every Python file of ``apps`` and ``packages``, so
``affected_tests`` runs this file on every Python change. It lives apart from
the large OCR file so that file runs only when its own imports change.
The assertions are unchanged.
"""

from __future__ import annotations

import ast

from tests.unit.test_attachment_ocr import CALLERS, REPO, _asks_for_no_ocr, _Calls


def _read_calls() -> list[tuple[tuple[str, str, str], ast.Call]]:
    found: list[tuple[tuple[str, str, str], ast.Call]] = []
    for root in ("apps", "packages"):
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "/node_modules/" in rel or "/.venv/" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            visitor = _Calls(rel)
            visitor.visit(tree)
            found.extend(visitor.found)
    return found

class TestTheCallers:
    """Fix round 2, P1. The scan reads the source, so it finds DIRECT calls
    only: ``parse_bounded(...)`` or ``x.parse_bounded(...)``. A call through
    another name (``f = parse_bounded``, ``getattr``, ``functools.partial``,
    a callback) is not found. A review must catch that form."""

    def test_every_call_of_a_read_that_can_reach_ocr_is_listed(self) -> None:
        found = {key for key, _call in _read_calls()}
        assert found == set(CALLERS), (
            "a new caller can reach OCR. List it in CALLERS and in spec §4.1, and "
            f"pass ocr=False when its budget is short: {sorted(found ^ set(CALLERS))}"
        )

    def test_a_caller_with_a_short_budget_asks_for_no_ocr(self) -> None:
        for key, call in _read_calls():
            reaches_ocr, why = CALLERS[key]
            assert _asks_for_no_ocr(call) is (not reaches_ocr), (key, why)
