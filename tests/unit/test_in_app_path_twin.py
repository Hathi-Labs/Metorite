"""The Python path check agrees with the TypeScript one (WS-62 CF-3).

``orchestrator/in_app_path.is_in_app_path`` is the twin of ``isInAppPath`` in
``workbench/control_plane/src/lib/inAppLink.ts``. Both read ONE table,
``src/lib/inAppPaths.cases.json``. This suite drives the Python side through
every row, and ``inAppLink.test.ts`` drives the TypeScript side through the
same rows, so the two checks cannot drift apart without a red test on one
side.

Mutation, seen to fail first: let the server accept ``//evil.example`` (drop
the ``startswith("//")`` clause), and the ``//evil.example`` rows go red here.

Run::

    uv run pytest tests/unit/test_in_app_path_twin.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from orchestrator.in_app_path import is_in_app_path

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "workbench" / "control_plane" / "src" / "lib" / "inAppPaths.cases.json"


def _rows() -> list[dict]:
    with TABLE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data["cases"]
    assert len(rows) >= 20, "the table lost its rows"
    return rows


ROWS = _rows()


@pytest.mark.parametrize("row", ROWS, ids=[repr(r["path"]) for r in ROWS])
def test_each_row_gives_the_tables_verdict(row: dict) -> None:
    assert is_in_app_path(row["path"]) is row["ok"], row["why"]


def test_the_table_holds_both_verdicts() -> None:
    """A table with one verdict only would prove nothing."""
    verdicts = {r["ok"] for r in ROWS}
    assert verdicts == {True, False}


def test_the_table_names_the_hostile_shapes() -> None:
    """The shapes the spec names stay in the table (§4.4: a scheme, a host, a
    ``//`` start), with a backslash and a control mark beside them."""
    paths = {r["path"] for r in ROWS if not r["ok"]}
    for must in ("https://evil.example/login", "//evil.example",
                 "/\\evil.example/login", "/projects\tx", "javascript:alert(1)"):
        assert must in paths, must


def test_a_non_string_is_refused() -> None:
    assert is_in_app_path(None) is False
    assert is_in_app_path(42) is False  # type: ignore[arg-type]
    assert is_in_app_path(["/projects"]) is False  # type: ignore[arg-type]
