"""WS-53 CRM-C4c — the type menu of the studio offers the gateway's matrix.

Spec: ``project-docs/specs/crm_platform.md`` §20.7, "CRM-C4c: the studio UI",
scope item 2 and done-when item 11.

``TYPE_CHANGES`` in ``routes/crm/field_studio.py`` is the boundary: a type
change that it does not name gives 422 ``field_type_change``. The studio keeps
the same matrix as ``TYPE_CHANGES`` in ``app/crm/lib/fieldStudio.ts``, and its
type menu offers only those changes. Two hand-kept copies drift in silence:

* a change in Python and not in TypeScript is one that no admin can choose,
  so the gateway allows a change that the studio hides;
* a change in TypeScript and not in Python is a menu item that answers 422
  the moment an admin picks it.

So this test READS the TypeScript, as ``test_crm_stage_discipline_parity.py``
reads ``board.ts``, and compares it with the Python by value. The three label
limits of the studio forms compare in the same way, because a limit that is
higher in TypeScript lets a form send a label that the gateway refuses.

R7: this file is the fence that ``fieldStudio.ts`` names.
"""

from __future__ import annotations

import re
from pathlib import Path

FIELD_STUDIO_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench" / "control_plane" / "src" / "app" / "crm" / "lib" / "fieldStudio.ts"
)

#: ``export const TYPE_CHANGES: … = { text: ["textarea"], … };``
_MATRIX_RE = re.compile(
    r"export\s+const\s+TYPE_CHANGES\b[^=]*=\s*\{(?P<body>.*?)\n\};",
    re.S,
)
#: ``number: ["decimal", "currency", "percent"],`` — one row of the matrix.
_ROW_RE = re.compile(r"(?P<from>\w+)\s*:\s*\[(?P<to>[^\]]*)\]")

#: The label limits of the studio forms. Each one is a numeric literal.
LIMITS = ("LABEL_MAX", "OPTION_LABEL_MAX", "HELP_MAX")


def _source() -> str:
    return FIELD_STUDIO_TS.read_text(encoding="utf-8")


def typescript_matrix(source: str) -> dict[str, frozenset[str]]:
    """The matrix of ``fieldStudio.ts``, as ``field_studio.TYPE_CHANGES`` holds it."""
    found = _MATRIX_RE.search(source)
    assert found is not None, (
        f"{FIELD_STUDIO_TS.name} no longer exports a TYPE_CHANGES object literal "
        "that this fence can read. If it moved, move the fence with it, and do "
        "not delete the parity check."
    )
    rows: dict[str, frozenset[str]] = {}
    for row in _ROW_RE.finditer(found.group("body")):
        source_type = row.group("from")
        assert source_type not in rows, f"TYPE_CHANGES names {source_type!r} twice"
        rows[source_type] = frozenset(re.findall(r'"([^"]+)"', row.group("to")))
    return rows


def _ts_number(source: str, name: str) -> int:
    found = re.search(rf"export\s+const\s+{name}\s*=\s*(?P<value>[0-9][0-9_]*)\s*;", source)
    assert found is not None, f"{FIELD_STUDIO_TS.name} has no numeric literal {name}"
    return int(found.group("value").replace("_", ""))


def test_the_matrix_is_the_same_on_both_sides() -> None:
    from gateway.routes.crm.field_studio import TYPE_CHANGES

    client = typescript_matrix(_source())
    gateway = {k: frozenset(v) for k, v in TYPE_CHANGES.items() if v}
    assert client == gateway, (
        "fieldStudio.ts::TYPE_CHANGES and field_studio.TYPE_CHANGES have drifted. "
        f"Only in the client: {sorted(set(client.items()) - set(gateway.items()))}. "
        f"Only in the gateway: {sorted(set(gateway.items()) - set(client.items()))}."
    )


def test_each_type_of_the_matrix_is_a_studio_type() -> None:
    """A word that is not a type is a menu item that no field can ever take."""
    from gateway.routes.crm.field_studio import FIELD_TYPES

    client = typescript_matrix(_source())
    words = set(client) | {t for targets in client.values() for t in targets}
    assert words <= set(FIELD_TYPES), sorted(words - set(FIELD_TYPES))


def test_no_row_changes_a_type_to_itself() -> None:
    """The menu always offers the current type. A row that names it again
    would offer it twice."""
    client = typescript_matrix(_source())
    assert all(source not in targets for source, targets in client.items())


def test_the_label_limits_are_the_same_on_both_sides() -> None:
    from gateway.routes.crm import field_studio

    source = _source()
    for name in LIMITS:
        assert _ts_number(source, name) == getattr(field_studio, name), name


def test_the_reader_finds_a_drift() -> None:
    """The parser must see a one-sided change, or the fence above proves nothing."""
    good = 'export const TYPE_CHANGES: X = {\n  text: ["textarea"],\n};'
    bad = 'export const TYPE_CHANGES: X = {\n  text: ["textarea", "email"],\n};'
    assert typescript_matrix(good) == {"text": frozenset({"textarea"})}
    assert typescript_matrix(bad) != typescript_matrix(good)
