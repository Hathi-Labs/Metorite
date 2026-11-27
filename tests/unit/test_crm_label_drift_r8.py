"""R8 — the pick-list drift statement of task 3869, on a real Postgres.

Spec: ``project-docs/specs/crm_platform.md`` §18.7, CRM-Q6a (task 3869).

``tests/unit/test_crm_module_reread.py`` proves the rules of the engine with a
fake session, and a fake agrees with any SQL. This file runs
``engine._LABEL_DRIFT_SQL`` itself, through ``tenant_session`` as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser, so each read below is the first thing the app role does.

1. Rows with a listed label, a missing label twice, ``-None-``, an empty
   string, a number, a list, a JSON null and a key that is no select field
   give exactly one ``(key, value)`` row: the missing label.
2. Org B holds a missing label too, and another module of org A holds one.
   The read of org A for its module sees neither.
3. ``engine._missing_labels`` gives one mark, the mark of that row, through
   its LOCAL ``statement_timeout``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_module_records_r8 import mz  # noqa: F401
from tests.unit.test_crm_sync_engine_r8 import ns  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

#: The current options of the select field ``Stage``: "Draft" was renamed.
LABELS = {"Stage": ["Pending", "Won"]}

_ROWS_A: list[tuple[str, str, dict[str, Any]]] = [
    ("Quotes", "q1", {"Stage": "Pending"}),      # a listed label
    ("Quotes", "q2", {"Stage": "Draft"}),        # a label the options lost
    ("Quotes", "q3", {"Stage": "Draft"}),        # the same value: one row
    ("Quotes", "q4", {"Stage": "-None-"}),
    ("Quotes", "q5", {"Stage": ""}),
    ("Quotes", "q6", {"Stage": 7}),              # not a string
    ("Quotes", "q7", {"Stage": ["Draft"]}),      # a list, not a string
    ("Quotes", "q8", {"Stage": None}),           # a JSON null
    ("Quotes", "q9", {"Subject": "Draft"}),      # no select field
    ("Invoices", "i1", {"Stage": "Lost"}),       # another module
]
_ROWS_B: list[tuple[str, str, dict[str, Any]]] = [
    ("Quotes", "b1", {"Stage": "Archived"}),     # org B's own drift
]


def _seed(p: Any, org: str, rows: list[tuple[str, str, dict[str, Any]]]) -> None:
    with p.admin_engine.begin() as c:
        for module, ext_id, fields in rows:
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :e, "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": ext_id, "f": json.dumps(fields)})


async def _drift(org: str, module: str) -> list[tuple[str, str]]:
    from acb_common.db import tenant_session
    from gateway.crm_sync import engine

    async with tenant_session(org) as db:
        rows = (await db.execute(text(engine._LABEL_DRIFT_SQL), {
            "org": org, "src": "zoho", "module": module,
            "labels": json.dumps(LABELS), "cap": engine.LABEL_DRIFT_CAP,
        })).fetchall()
    return [(str(r.key), str(r.value)) for r in rows]


async def test_only_a_string_that_the_options_lost_is_drift(mz) -> None:
    p, _a, _b = mz
    _seed(p, p.org_a, _ROWS_A)
    _seed(p, p.org_b, _ROWS_B)
    assert await _drift(p.org_a, "Quotes") == [("Stage", "Draft")]


async def test_each_org_and_each_module_reads_its_own_drift(mz) -> None:
    p, _a, _b = mz
    _seed(p, p.org_a, _ROWS_A)
    _seed(p, p.org_b, _ROWS_B)
    assert await _drift(p.org_b, "Quotes") == [("Stage", "Archived")]
    assert await _drift(p.org_a, "Invoices") == [("Stage", "Lost")]


async def test_the_engine_read_gives_one_mark_for_the_drifted_value(mz) -> None:
    from gateway.crm_sync import engine

    p, _a, _b = mz
    _seed(p, p.org_a, _ROWS_A)
    marks = await engine._missing_labels(SimpleNamespace(org=p.org_a), "Quotes", LABELS)
    assert marks == [engine._drift_mark("Stage", "Draft")]
