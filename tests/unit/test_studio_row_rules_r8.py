"""WS-STU C8a: app roles and row rules on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §19.7 and the §14 C8a row. Code:
``acb_common/studio_access.py`` (the grammar and the scope) and
``gateway/studio/objects_store.py`` (the one reader and the one writer, with
``studio_objects.access`` of the migration ``studio_row_rules``). The pure half
and the one-reader fence are ``test_studio_row_rules.py``.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations. A viewer holds
its roles directly: C8b gives a member the roles of the role map of an install.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_row_rules_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**The world.** A ``slip`` names an ``employee`` and a ``manager`` (hot person
fields) and a ``team``. ``hr`` reads and writes all. ``manager`` reads the
slips where ``manager = $me``. ``employee`` reads and writes the slips where
``employee = $me``, and ``bank_account`` and ``bonus`` are hidden from it. The
formula ``total`` reads ``bonus``, so it is hidden too. A ``team`` is open, and
its roll-up ``headcount`` counts the slips of the team. A ``claim`` is a line
of a slip (C13).

**Acceptance (C8a).** An employee gets only ``employee = me`` records from a
get and a list, a manager only ``manager = me``, and a holder of ``hr`` gets
all. A get of another person's record answers None, and an update or a delete
of it answers "not found". A hidden key is absent from the get, the list and
each write answer.

**The mutations (R7).** Commit before each, and restore with git after. Each
result below was run on 2026-11-11.

* M1. ``objects_store._q_where``: drop the ``_q_access`` part. The list tests of
  the employee and the manager go red.
* M2. ``objects_store._lock_records``: lock with ``_LOCK_RECORD_SQL`` always.
  ``test_r8_an_update_or_a_delete_of_another_persons_record_answers_not_found``
  goes red.
* M3. ``objects_store._View.shown``: return the record as it is. The hidden-key
  tests go red.
* M4. ``objects_store._check_sees``: return at once.
  ``test_r8_a_create_or_a_move_into_the_rows_of_another_person_is_refused`` goes red.
* M5. ``objects_store._check_line_parents``: use ``_LINE_PARENTS_SQL`` always.
  ``test_r8_a_line_cannot_name_a_parent_that_the_viewer_cannot_read`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from acb_common.studio_access import AccessError, Viewer
from acb_common.studio_fields import MAX_RECORD_BYTES, FieldError
from acb_common.studio_query import QueryError
from gateway.studio import objects_store as store
from gateway.studio.objects_store import (
    AccessDenied,
    BatchError,
    RecordNotFound,
    Write,
)
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
TAG = uuid.uuid4().hex[:8]
OWNER = f"owner.{TAG}@stuc8a.test"
E1, E2, E3 = (f"e{i}.{TAG}@stuc8a.test" for i in (1, 2, 3))
M1, M2 = (f"m{i}.{TAG}@stuc8a.test" for i in (1, 2))
HR = f"hr.{TAG}@stuc8a.test"

AS_OWNER = Viewer(OWNER)
AS_E1 = Viewer(E1, frozenset({"employee"}))
AS_M1 = Viewer(M1, frozenset({"manager"}))
AS_M2 = Viewer(M2, frozenset({"manager"}))
AS_HR = Viewer(HR, frozenset({"hr"}))
AS_STRANGER = Viewer(f"x.{TAG}@stuc8a.test", frozenset({"intern"}))
# Fix round 1: two roles of one member.
AS_M1_BOTH = Viewer(M1, frozenset({"manager", "employee"}))
AS_E1_AUDIT = Viewer(E1, frozenset({"auditor", "employee"}))
# Fix round 2: a clerk grant reads and writes all, and hides one key.
CEO = f"ceo.{TAG}@stuc8a.test"
X1 = f"x1.{TAG}@stuc8a.test"
AS_CLERK_EMP = Viewer(X1, frozenset({"clerk", "employee"}))
AS_CLERK = Viewer(f"clerk.{TAG}@stuc8a.test", frozenset({"clerk"}))
AS_MOVER = Viewer(M1, frozenset({"mover"}))
# Fix round 3: the viewers of the third package (org B).
S1, S2 = (f"s{i}.{TAG}@stuc8a.test" for i in (1, 2))
W1, W2 = (f"w{i}.{TAG}@stuc8a.test" for i in (1, 2))
H1, H2 = (f"h{i}.{TAG}@stuc8a.test" for i in (1, 2))
AS_MOVER2 = Viewer(M1, frozenset({"mover2"}))
AS_SPENDER = Viewer(S1, frozenset({"spender"}))
AS_WORKER = Viewer(W1, frozenset({"worker"}))
AS_HOLDER = Viewer(H1, frozenset({"holder"}))

TEAM_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "headcount", "type": "number"},
    {"key": "lead", "type": "person", "hot": True},
]
#: Fix round 1 (P1-3): a team is restricted. An employee reads and changes only the
#: team that they lead.
TEAM_ACCESS: list[dict[str, Any]] = [
    {"role": "hr", "read": "all", "write": "all"},
    {"role": "employee", "read": {"where": "lead = $me"}, "write": "all"},
]
SLIP_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "manager", "type": "person", "hot": True},
    {"key": "team", "type": "relation", "to": "team", "hot": True},
    {"key": "month", "type": "text", "hot": True},
    {"key": "amount", "type": "number"},
    {"key": "bonus", "type": "number"},
    {"key": "total", "type": "number"},
    {"key": "bank_account", "type": "text"},
    {"key": "approver", "type": "person"},
    {"key": "claims", "type": "relation", "to": "claim", "lines": "slip"},
]
CLAIM_FIELDS: list[dict[str, Any]] = [
    {"key": "slip", "type": "relation", "to": "slip", "hot": True},
    {"key": "what", "type": "text"},
]
SLIP_RULES: list[dict[str, Any]] = [
    {"name": "pay", "kind": "formula", "target": "total", "rounding": "half_up",
     "expr": {"fn": "add", "args": [{"ref": "amount"}, {"ref": "bonus"}]}},
    {"name": "total_ok", "kind": "range", "field": "total", "max": 1_000_000},
]
TEAM_RULES: list[dict[str, Any]] = [
    {"name": "heads", "kind": "rollup", "target": "headcount", "source": "slip",
     "via": "team", "agg": "count"},
]
SLIP_ACCESS: list[dict[str, Any]] = [
    {"role": "hr", "read": "all", "write": "all"},
    {"role": "manager", "read": {"where": "manager = $me"}},
    {"role": "employee", "read": {"where": "employee = $me"}, "write": "all",
     "hide": ["bank_account", "bonus"]},
    # Fix round 1 (P1-1): reads every record, and writes none.
    {"role": "auditor", "read": "all"},
]
_HIDDEN = {"bank_account", "bonus", "total"}

# ── The fix round 2 world: a second package of org A ─────────────────────────
#: P1-A. A clerk reads and writes all, and `salary` is hidden from it. An
#: employee reads the own record with no key hidden.
STAFF_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "salary", "type": "number"},
    {"key": "note", "type": "text"},
]
STAFF_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["salary"]},
    {"role": "employee", "read": {"where": "employee = $me"}},
]
#: P1-B. `net = gross - tax`, and `gross` is hidden from a clerk. A crew is
#: open, and its roll-up `net_total` adds the `net` of its paychecks.
CREW_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "net_total", "type": "number"},
]
CREW_RULES: list[dict[str, Any]] = [
    {"name": "net_sum", "kind": "rollup", "target": "net_total", "source": "paycheck",
     "via": "crew", "agg": "sum", "field": "net"},
]
PAYCHECK_FIELDS: list[dict[str, Any]] = [
    {"key": "crew", "type": "relation", "to": "crew", "hot": True},
    {"key": "gross", "type": "number"},
    {"key": "tax", "type": "number"},
    {"key": "net", "type": "number", "hot": True},
]
PAYCHECK_RULES: list[dict[str, Any]] = [
    {"name": "net_of", "kind": "formula", "target": "net", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "gross"}, {"ref": "tax"}]}},
]
PAYCHECK_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["gross"]},
]
#: P1-C. A stock that never goes below 0, and a mover who reads and writes the
#: own movements only.
MOVEMENT_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "sku", "type": "text", "hot": True},
    {"key": "qty", "type": "number", "hot": True},
]
MOVEMENT_RULES: list[dict[str, Any]] = [
    {"name": "stock", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": 0},
]
MOVEMENT_ACCESS: list[dict[str, Any]] = [
    {"role": "mover", "read": {"where": "by = $me"}, "write": "all"},
]
#: P1-D. `margin = total - cost`, at most 100, and `cost` is hidden from a
#: clerk. `total` adds the `amount` of the lines, and a line is open.
ORDER_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "cost", "type": "number"},
    {"key": "total", "type": "number"},
    {"key": "margin", "type": "number"},
]
ORDER_RULES: list[dict[str, Any]] = [
    {"name": "line_sum", "kind": "rollup", "target": "total", "source": "line",
     "via": "order", "agg": "sum", "field": "amount"},
    {"name": "margin_of", "kind": "formula", "target": "margin", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "total"}, {"ref": "cost"}]}},
    {"name": "margin_ok", "kind": "range", "field": "margin", "max": 100},
]
ORDER_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["cost"]},
]
LINE_FIELDS: list[dict[str, Any]] = [
    {"key": "order", "type": "relation", "to": "order", "hot": True},
    {"key": "amount", "type": "number", "hot": True},
]
#: P2. A guard of the machine reads `hr_note`, which is hidden from an employee.
REQUEST_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "status", "type": "select", "options": ["draft", "approved"]},
    {"key": "hr_note", "type": "text"},
]
REQUEST_RULES: list[dict[str, Any]] = [
    {"name": "flow", "kind": "state_machine", "field": "status", "initial": "draft",
     "moves": [{"from": "draft", "to": "approved",
                "guard": [{"field": "hr_note", "op": "not_empty"}]}]},
]
REQUEST_ACCESS: list[dict[str, Any]] = [
    {"role": "employee", "read": {"where": "by = $me"}, "write": "all", "hide": ["hr_note"]},
]


async def _define(db: Any, pkg: str, name: str, fields: Any, rules: Any = None,
                  access: Any = None) -> Any:
    made = await store.define_object(db, OWNER, package_id=pkg, name=name, fields=fields)
    assert made is not None
    if rules:
        assert await store.set_constraints(db, made.id, rules, package_id=pkg)
    if access:
        assert await store.set_access(db, made.id, access, package_id=pkg)
    return made


# ── The fix round 3 world: a third package, of org B ─────────────────────────
#: Each object hides its data through a chain: a formula of a formula, a formula
#: of a roll-up, or a key rule over rows that the viewer cannot read.
MOVE2_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "sku", "type": "text", "hot": True},
    {"key": "qty_in", "type": "number"},
    {"key": "qty_out", "type": "number"},
    {"key": "qty", "type": "number", "hot": True},
]
MOVE2_RULES: list[dict[str, Any]] = [
    {"name": "qty_of", "kind": "formula", "target": "qty", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "qty_in"}, {"ref": "qty_out"}]}},
    {"name": "stock2", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": 0},
]
MOVE2_ACCESS: list[dict[str, Any]] = [
    {"role": "mover2", "read": {"where": "by = $me"}, "write": "all"},
]
CALC_FIELDS: list[dict[str, Any]] = [
    {"key": "a", "type": "number"},
    {"key": "y", "type": "number"},
    {"key": "cost", "type": "number"},
    {"key": "z", "type": "number"},
    {"key": "label", "type": "text"},
    {"key": "a5", "type": "number"},
]
CALC_RULES: list[dict[str, Any]] = [
    {"name": "y_of", "kind": "formula", "target": "y", "rounding": "half_up",
     "expr": {"fn": "add", "args": [{"ref": "a"}, {"ref": "a"}]}},
    {"name": "z_of", "kind": "formula", "target": "z", "rounding": "half_up",
     "expr": {"fn": "mul", "args": [{"ref": "cost"}, {"ref": "y"}]}},
    # Verifier F1: `a5` reads the blocked `a` alone, so it runs on each change.
    {"name": "a5_of", "kind": "formula", "target": "a5", "rounding": "half_up",
     "expr": {"fn": "add", "args": [{"ref": "a"}, {"num": "5"}]}},
]
CALC_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["cost"]},
]
ORDER2_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "cost", "type": "number"},
    {"key": "total", "type": "number"},
    {"key": "margin", "type": "number"},
]
ORDER2_RULES: list[dict[str, Any]] = [
    {"name": "line2_sum", "kind": "rollup", "target": "total", "source": "line2",
     "via": "order2", "agg": "sum", "field": "amount"},
    {"name": "margin2_of", "kind": "formula", "target": "margin", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "total"}, {"ref": "cost"}]}},
    {"name": "margin2_ok", "kind": "range", "field": "margin", "max": 100},
]
ORDER2_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["cost"]},
]
LINE2_FIELDS: list[dict[str, Any]] = [
    {"key": "order2", "type": "relation", "to": "order2", "hot": True},
    {"key": "qty", "type": "number"},
    {"key": "price", "type": "number"},
    {"key": "amount", "type": "number", "hot": True},
    {"key": "memo", "type": "text"},
]
LINE2_RULES: list[dict[str, Any]] = [
    {"name": "amount_of", "kind": "formula", "target": "amount", "rounding": "half_up",
     "expr": {"fn": "mul", "args": [{"ref": "qty"}, {"ref": "price"}]}},
]
BUDGET_FIELDS: list[dict[str, Any]] = [
    {"key": "owner", "type": "person", "hot": True},
    {"key": "dept", "type": "text", "hot": True},
    {"key": "cap", "type": "number"},
    {"key": "spent", "type": "number"},
    {"key": "left", "type": "number", "hot": True},
]
BUDGET_RULES: list[dict[str, Any]] = [
    {"name": "spent_sum", "kind": "rollup", "target": "spent", "source": "expense",
     "via": "budget", "agg": "sum", "field": "amount"},
    {"name": "left_of", "kind": "formula", "target": "left", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "cap"}, {"ref": "spent"}]}},
    {"name": "dept_floor", "kind": "group_sum_min", "field": "left", "group_by": ["dept"],
     "min": 0},
]
BUDGET_ACCESS: list[dict[str, Any]] = [
    {"role": "spender", "read": {"where": "owner = $me"}, "write": "all"},
]
EXPENSE_FIELDS: list[dict[str, Any]] = [
    {"key": "budget", "type": "relation", "to": "budget", "hot": True},
    {"key": "amount", "type": "number", "hot": True},
    {"key": "memo", "type": "text"},
]
TICKET_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "status", "type": "select", "options": ["open", "done"], "hot": True},
    {"key": "points", "type": "number", "hot": True},
]
TICKET_RULES: list[dict[str, Any]] = [
    {"name": "flow2", "kind": "state_machine", "field": "status", "initial": "open",
     "moves": [{"from": "open", "to": "done"}]},
    {"name": "quota", "kind": "group_sum_min", "field": "points", "group_by": ["status"],
     "min": 1},
]
TICKET_ACCESS: list[dict[str, Any]] = [
    {"role": "worker", "read": {"where": "by = $me"}, "write": "all"},
]
REVIEW_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "manager", "type": "person", "hot": True},
    {"key": "bank", "type": "text"},
    {"key": "notes", "type": "text"},
]
REVIEW_ACCESS: list[dict[str, Any]] = [
    {"role": "manager", "read": {"where": "manager = $me"}, "hide": ["notes"]},
    {"role": "employee", "read": {"where": "employee = $me"}, "write": "all", "hide": ["bank"]},
]
BADGE_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "code", "type": "text", "hot": True},
]
BADGE_RULES: list[dict[str, Any]] = [
    {"name": "code_once", "kind": "unique", "fields": ["code"]},
]
BADGE_ACCESS: list[dict[str, Any]] = [
    {"role": "holder", "read": {"where": "by = $me"}, "write": "all"},
]


# ── The fix round 4 world, in the third package ─────────────────────────────
PY1 = f"py1.{TAG}@stuc8a.test"
AS_PAYEE = Viewer(PY1, frozenset({"payee"}))
AS_PEEKER = Viewer(PY1, frozenset({"peeker"}))
AS_PAYER = Viewer(PY1, frozenset({"payer"}))
#: P1. A sheet near the record cap: the payee writes `notes`, and `salary` and
#: `bank` are hidden from the payee.
SHEET_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "notes", "type": "longText"},
    {"key": "salary", "type": "number"},
    {"key": "bank", "type": "text"},
]
SHEET_ACCESS: list[dict[str, Any]] = [
    {"role": "payee", "read": {"where": "employee = $me"}, "write": "all",
     "hide": ["salary", "bank"]},
]
#: P1, the derived write. A folder is open. `open_sum` adds the open items, and
#: `secret_sum` adds the secrets, whose `amount` is hidden from a peeker.
FOLDER_FIELDS: list[dict[str, Any]] = [
    {"key": "notes", "type": "longText"},
    {"key": "open_sum", "type": "number"},
    {"key": "secret_sum", "type": "number"},
]
FOLDER_RULES: list[dict[str, Any]] = [
    {"name": "open_of", "kind": "rollup", "target": "open_sum", "source": "item",
     "via": "folder", "agg": "sum", "field": "amount"},
    {"name": "secret_of", "kind": "rollup", "target": "secret_sum", "source": "secret",
     "via": "folder", "agg": "sum", "field": "amount"},
]
ITEM_FIELDS: list[dict[str, Any]] = [
    {"key": "folder", "type": "relation", "to": "folder", "hot": True},
    {"key": "amount", "type": "number", "hot": True},
]
SECRET_ACCESS: list[dict[str, Any]] = [
    {"role": "peeker", "read": "all", "hide": ["amount"]},
]
#: P2. A bill and its rows. The grant of a row hides `bill`, the field that
#: links a row to its bill. The store took it, because the row came first.
ROW_FIELDS: list[dict[str, Any]] = [
    {"key": "bill", "type": "relation", "to": "bill", "hot": True},
    {"key": "what", "type": "text"},
]
ROW_ACCESS: list[dict[str, Any]] = [
    {"role": "payer", "read": "all", "write": "all", "hide": ["bill"]},
]
BILL_FIELDS: list[dict[str, Any]] = [
    {"key": "who", "type": "person", "hot": True},
    {"key": "memo", "type": "text"},
    {"key": "rows", "type": "relation", "to": "row", "lines": "bill"},
]
BILL_ACCESS: list[dict[str, Any]] = [
    {"role": "payer", "read": {"where": "who = $me"}, "write": "all"},
]


async def _build_round_four(db: Any, pkg: str) -> dict[str, Any]:
    """The objects and the records of fix round 4, in the package *pkg* of org B."""
    sheet = await _define(db, pkg, "sheet", SHEET_FIELDS, access=SHEET_ACCESS)
    folder = await _define(db, pkg, "folder", FOLDER_FIELDS)
    item = await _define(db, pkg, "item", ITEM_FIELDS)
    secret = await _define(db, pkg, "secret", ITEM_FIELDS, access=SECRET_ACCESS)
    assert await store.set_constraints(db, folder.id, FOLDER_RULES, package_id=pkg)
    row = await _define(db, pkg, "row", ROW_FIELDS, access=ROW_ACCESS)
    bill = await _define(db, pkg, "bill", BILL_FIELDS, access=BILL_ACCESS)

    async def put(*writes: Write) -> list[Any]:
        return await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                        writes=list(writes))

    sh1, sh2 = await put(
        Write("create", sheet.id, data={"employee": PY1, "salary": 9}),
        Write("create", sheet.id, data={"employee": PY1, "salary": 999_999_999_999_999,
                                        "bank": "x" * 400}))
    f1, f2 = await put(Write("create", folder.id, data={}), Write("create", folder.id, data={}))
    await put(Write("create", secret.id, data={"folder": {"id": f1.id}, "amount": 9}),
              Write("create", secret.id, data={"folder": {"id": f2.id},
                                               "amount": 999_999_999_999_999}))
    bill1, bill2 = await put(Write("create", bill.id, data={"who": PY1}),
                             Write("create", bill.id, data={"who": PY1}))
    r1, r2 = await put(Write("create", row.id, data={"bill": {"id": bill1.id}, "what": "a"}),
                       Write("create", row.id, data={"bill": {"id": bill2.id}, "what": "b"}))
    return {"sheet": sheet, "folder": folder, "item": item, "secret": secret, "row": row,
            "bill": bill, "sh1": sh1.id, "sh2": sh2.id, "f1": f1.id, "f2": f2.id,
            "bill1": bill1.id, "bill2": bill2.id, "r1": r1.id, "r2": r2.id}


async def _build_round_three(db: Any, pkg: str) -> dict[str, Any]:
    """The objects and the records of fix round 3, in the package *pkg* of org B."""
    move2 = await _define(db, pkg, "move2", MOVE2_FIELDS, MOVE2_RULES, MOVE2_ACCESS)
    calc = await _define(db, pkg, "calc", CALC_FIELDS, CALC_RULES, CALC_ACCESS)
    order2 = await _define(db, pkg, "order2", ORDER2_FIELDS)
    line2 = await _define(db, pkg, "line2", LINE2_FIELDS, LINE2_RULES)
    assert await store.set_constraints(db, order2.id, ORDER2_RULES, package_id=pkg)
    assert await store.set_access(db, order2.id, ORDER2_ACCESS, package_id=pkg)
    budget = await _define(db, pkg, "budget", BUDGET_FIELDS)
    expense = await _define(db, pkg, "expense", EXPENSE_FIELDS)
    assert await store.set_constraints(db, budget.id, BUDGET_RULES, package_id=pkg)
    assert await store.set_access(db, budget.id, BUDGET_ACCESS, package_id=pkg)
    ticket = await _define(db, pkg, "ticket", TICKET_FIELDS, TICKET_RULES, TICKET_ACCESS)
    review = await _define(db, pkg, "review", REVIEW_FIELDS, access=REVIEW_ACCESS)
    badge = await _define(db, pkg, "badge", BADGE_FIELDS, BADGE_RULES, BADGE_ACCESS)

    async def put(*writes: Write) -> list[Any]:
        return await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                        writes=list(writes))

    mine2, other2 = await put(
        Write("create", move2.id, data={"by": M1, "sku": "A", "qty_in": 1000, "qty_out": 0}),
        Write("create", move2.id, data={"by": M2, "sku": "A", "qty_in": 3, "qty_out": 0}))
    calc1 = (await put(Write("create", calc.id, data={"a": 1, "cost": 7, "label": "c"})))[0]
    o2 = (await put(Write("create", order2.id, data={"name": "O2", "cost": 50})))[0]
    l2 = (await put(Write("create", line2.id, data={
        "order2": {"id": o2.id}, "qty": 1, "price": 10, "memo": "m"})))[0]
    b1, b2 = await put(
        Write("create", budget.id, data={"owner": S1, "dept": "D", "cap": 100}),
        Write("create", budget.id, data={"owner": S2, "dept": "D", "cap": 0}))
    e1 = (await put(Write("create", expense.id, data={
        "budget": {"id": b1.id}, "amount": 1, "memo": "e"})))[0]
    tk2 = (await put(Write("create", ticket.id, data={"by": W2, "points": 5})))[0]
    await put(Write("create", badge.id, data={"by": H2, "code": "B-1"}))
    return {"pkg3": pkg, "move2": move2, "calc": calc, "order2": order2, "line2": line2,
            "budget": budget, "expense": expense, "ticket": ticket, "review": review,
            "badge": badge, "mine2": mine2.id, "other2": other2.id, "calc1": calc1.id,
            "o2": o2.id, "l2": l2.id, "b1": b1.id, "b2": b2.id, "e1": e1.id, "tk2": tk2.id}


async def _build_round_two(db: Any, pkg: str) -> dict[str, Any]:
    """The objects and the records of fix round 2, in the package *pkg*."""
    staff = await _define(db, pkg, "staff", STAFF_FIELDS, access=STAFF_ACCESS)
    crew = await _define(db, pkg, "crew", CREW_FIELDS)
    paycheck = await _define(db, pkg, "paycheck", PAYCHECK_FIELDS, PAYCHECK_RULES, PAYCHECK_ACCESS)
    assert await store.set_constraints(db, crew.id, CREW_RULES, package_id=pkg)
    movement = await _define(db, pkg, "movement", MOVEMENT_FIELDS, MOVEMENT_RULES,
                             MOVEMENT_ACCESS)
    order = await _define(db, pkg, "order", ORDER_FIELDS)
    line = await _define(db, pkg, "line", LINE_FIELDS)
    assert await store.set_constraints(db, order.id, ORDER_RULES, package_id=pkg)
    assert await store.set_access(db, order.id, ORDER_ACCESS, package_id=pkg)
    request = await _define(db, pkg, "request", REQUEST_FIELDS, REQUEST_RULES, REQUEST_ACCESS)

    async def put(*writes: Write) -> list[Any]:
        return await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                        writes=list(writes))

    ceo = (await put(Write("create", staff.id, data={"employee": CEO, "salary": 999_000})))[0]
    c1 = (await put(Write("create", crew.id, data={"name": "Crew 1"})))[0]
    await put(Write("create", paycheck.id, data={"crew": {"id": c1.id}, "gross": 1000, "tax": 200}))
    mv1, mv2, mv3 = await put(
        Write("create", movement.id, data={"by": M1, "sku": "A", "qty": 5}),
        Write("create", movement.id, data={"by": M2, "sku": "A", "qty": -3}),
        Write("create", movement.id, data={"by": M1, "sku": "B", "qty": 1}))
    o1 = (await put(Write("create", order.id, data={"name": "O1", "cost": 50})))[0]
    l0 = (await put(Write("create", line.id, data={"order": {"id": o1.id}, "amount": 10})))[0]
    return {"pkg2": pkg, "staff": staff, "crew": crew, "paycheck": paycheck,
            "movement": movement, "order": order, "line": line, "request": request,
            "ceo": ceo.id, "c1": c1.id, "mv1": mv1.id, "mv2": mv2.id, "mv3": mv3.id,
            "o1": o1.id, "l0": l0.id}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc8a', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc8a", "name": "stuc8a", "purpose": "The C8a R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _run(p: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(p.app_url)):
            return await fn()
    return asyncio.run(go())


def _slip(employee: str, manager: str, team: str, n: int) -> dict[str, Any]:
    return {"employee": employee, "manager": manager, "team": {"id": team},
            "month": f"2026-{n:02d}", "amount": 100 * n, "bonus": 10 * n,
            "bank_account": f"AC-{n}"}


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """One package of org A, owned by OWNER, with a team, its slips and a
    claim object, and one package of org B."""
    p = promoted
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b), ("a2", p.org_a), ("b3", p.org_b)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc8a-{TAG}-{key}", "oe": OWNER, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())

    async def build() -> SimpleNamespace:
        pkg = ids["a"]
        async with tenant_session(p.org_a) as db:
            claim = await store.define_object(db, OWNER, package_id=pkg, name="claim",
                                              fields=CLAIM_FIELDS)
            team = await store.define_object(db, OWNER, package_id=pkg, name="team",
                                             fields=TEAM_FIELDS)
            slip = await store.define_object(db, OWNER, package_id=pkg, name="slip",
                                             fields=SLIP_FIELDS)
            assert claim and team and slip
            await store.set_constraints(db, slip.id, SLIP_RULES, package_id=pkg)
            await store.set_constraints(db, team.id, TEAM_RULES, package_id=pkg)
            done = await store.set_access(db, slip.id, SLIP_ACCESS, package_id=pkg)
            assert done is not None and done.access is not None
            assert await store.set_access(db, team.id, TEAM_ACCESS, package_id=pkg)
            t1, t2 = await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER, writes=[
                Write("create", team.id, data={"name": "Plant 4", "lead": E1}),
                Write("create", team.id, data={"name": "Plant 9", "lead": E3})])
            rows = [_slip(E1, M1, t1.id, 1), _slip(E2, M1, t1.id, 2), _slip(E3, M2, t1.id, 3),
                    _slip(M1, M2, t1.id, 4)]
            made = await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                            writes=[Write("create", slip.id, data=r) for r in rows])
            two = await _build_round_two(db, ids["a2"])
        async with tenant_session(p.org_b) as db:
            three = await _build_round_three(db, ids["b3"])
            four = await _build_round_four(db, ids["b3"])
        return SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg=pkg, pkg_b=ids["b"], slip=slip,
                               team=team, claim=claim, t1=t1.id, t2=t2.id,
                               s1=made[0].id, s2=made[1].id, s3=made[2].id, s4=made[3].id,
                               **two, **three, **four)

    w = _run(p, build)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc8a-{TAG}-%"})


def _list(w: Any, viewer: Viewer, **query: Any) -> store.RecordPage:
    async def go() -> store.RecordPage:
        async with tenant_session(w.a) as db:
            page = await store.query_records(db, package_id=w.pkg, object_id=w.slip.id,
                                             viewer=viewer, limit=100, **query)
        assert page is not None
        return page
    return _run(w.p, go)


def _get(w: Any, viewer: Viewer, rid: str, oid: str | None = None) -> store.Record | None:
    async def go() -> store.Record | None:
        async with tenant_session(w.a) as db:
            return await store.get_record(db, oid or w.slip.id, rid, package_id=w.pkg,
                                          viewer=viewer)
    return _run(w.p, go)


def _write(w: Any, viewer: Viewer, *writes: Write) -> list[Any]:
    async def go() -> list[Any]:
        async with tenant_session(w.a) as db:
            return await store.apply_writes(db, viewer.email, package_id=w.pkg, viewer=viewer,
                                            writes=list(writes))
    return _run(w.p, go)


def _refused(w: Any, viewer: Viewer, *writes: Write) -> Exception:
    with pytest.raises(BatchError) as caught:
        _write(w, viewer, *writes)
    return caught.value.cause


def _stored(w: Any, rid: str) -> dict[str, Any]:
    with w.p.admin_engine.connect() as c:
        return dict(c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                              {"r": rid}).scalar_one())


def _employees(page: store.RecordPage) -> set[str]:
    return {r.data["employee"] for r in page.records}


# ── The reads ───────────────────────────────────────────────────────────────


def test_r8_an_employee_lists_and_gets_only_their_own_records(world) -> None:
    page = _list(world, AS_E1)
    assert [r.id for r in page.records] == [world.s1] and page.total == 1
    own = _get(world, AS_E1, world.s1)
    assert own is not None and own.data["employee"] == E1


def test_r8_a_manager_reads_only_the_records_of_their_team(world) -> None:
    assert _employees(_list(world, AS_M1)) == {E1, E2}
    assert _list(world, AS_M1).total == 2
    assert _employees(_list(world, AS_M2)) == {E3, M1}
    assert _get(world, AS_M1, world.s3) is None


def test_r8_a_holder_of_hr_and_the_owner_read_every_record_and_every_key(world) -> None:
    for viewer in (AS_HR, AS_OWNER):
        page = _list(world, viewer)
        assert _employees(page) == {E1, E2, E3, M1} and page.total == 4
        assert all(set(r.data) >= _HIDDEN for r in page.records)
        got = _get(world, viewer, world.s2)
        assert got is not None and got.data["bank_account"] == "AC-2" and got.data["total"] == 220


def test_r8_many_roles_read_the_union_of_their_rows(world) -> None:
    both = Viewer(M1, frozenset({"manager", "employee"}))
    page = _list(world, both)
    assert _employees(page) == {E1, E2, M1} and page.total == 3
    # The own slip hides the keys of the employee grant. A slip of the team shows them.
    by = {r.data["employee"]: r for r in page.records}
    assert not _HIDDEN & set(by[M1].data)
    assert set(by[E1].data) >= _HIDDEN


def test_r8_a_get_of_another_persons_record_answers_none(world) -> None:
    assert _get(world, AS_E1, world.s2) is None
    assert _get(world, AS_E1, world.s3) is None
    assert _get(world, AS_E1, str(uuid.uuid4())) is None


def test_r8_a_viewer_with_no_grant_reads_nothing_and_writes_nothing(world) -> None:
    page = _list(world, AS_STRANGER)
    assert page.records == () and page.total == 0
    assert _get(world, AS_STRANGER, world.s1) is None
    cause = _refused(world, AS_STRANGER, Write("update", world.slip.id, world.s1, {"month": "x"}))
    assert isinstance(cause, AccessDenied)


def test_r8_a_filter_on_a_person_field_stays_inside_the_row_rule(world) -> None:
    mine = _list(world, AS_M1, filter=[{"field": "employee", "op": "eq", "value": E2}])
    assert [r.id for r in mine.records] == [world.s2]
    other = _list(world, AS_M1, filter=[{"field": "employee", "op": "eq", "value": E3}])
    assert other.records == () and other.total == 0
    some = _list(world, AS_M1, filter=[{"field": "employee", "op": "in", "value": [E1, E3]}])
    assert [r.id for r in some.records] == [world.s1]


def test_r8_each_op_of_a_person_field_on_a_hot_field_and_on_a_cold_one(world) -> None:
    """C8a gives `person` the ops eq, ne, in and is_empty. `manager` is hot, and
    `approver` is cold, so the query reads `data` for it."""
    _write(world, AS_OWNER, Write("update", world.slip.id, world.s3, {"approver": M2}))

    def ids(field: str, op: str, value: Any = None) -> set[str]:
        item: dict[str, Any] = {"field": field, "op": op}
        if value is not None:
            item["value"] = value
        return {r.id for r in _list(world, AS_OWNER, filter=[item]).records}

    assert ids("manager", "eq", M1) == {world.s1, world.s2}
    assert ids("manager", "ne", M1) == {world.s3, world.s4}
    assert ids("manager", "in", [M2]) == {world.s3, world.s4}
    assert ids("manager", "is_empty") == set()
    assert ids("approver", "eq", M2) == {world.s3}
    assert ids("approver", "ne", M2) == set()
    assert ids("approver", "in", [M2, M1]) == {world.s3}
    assert ids("approver", "is_empty") == {world.s1, world.s2, world.s4}
    _write(world, AS_OWNER, Write("update", world.slip.id, world.s3, {"approver": None}))


def test_r8_another_org_reads_nothing_of_the_package(world) -> None:
    async def go() -> tuple[Any, Any]:
        async with tenant_session(world.b) as db:
            got = await store.get_record(db, world.slip.id, world.s1, package_id=world.pkg,
                                         viewer=AS_OWNER)
            page = await store.query_records(db, package_id=world.pkg, object_id=world.slip.id,
                                             viewer=AS_OWNER)
        return got, page

    assert _run(world.p, go) == (None, None)


# ── The hidden keys ─────────────────────────────────────────────────────────


def test_r8_a_hidden_key_leaves_the_get_the_list_and_the_write_answers(world) -> None:
    got = _get(world, AS_E1, world.s1)
    assert got is not None and not _HIDDEN & set(got.data) and got.data["amount"] == 100
    listed = _list(world, AS_E1).records[0]
    assert not _HIDDEN & set(listed.data)
    made, changed = _write(world, AS_E1,
                           Write("create", world.slip.id, data={"employee": E1, "month": "x"}),
                           Write("update", world.slip.id, world.s1, {"month": "2026-01b"}))
    for answer in (made, changed):
        assert isinstance(answer, store.Record) and not _HIDDEN & set(answer.data)
    # The store keeps the keys. Only the answer leaves them out.
    assert _stored(world, world.s1)["bank_account"] == "AC-1"
    _write(world, AS_OWNER, Write("delete", world.slip.id, made.id))


def test_r8_a_write_that_names_a_hidden_key_is_refused(world) -> None:
    for data in ({"bank_account": "AC-9"}, {"bonus": 5}):
        cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1, data))
        assert isinstance(cause, FieldError) and cause.field in data
    assert _stored(world, world.s1)["bank_account"] == "AC-1"


def test_r8_a_filter_or_a_sort_on_a_hidden_key_is_refused(world) -> None:
    for query in ({"filter": [{"field": "bank_account", "op": "eq", "value": "AC-1"}]},
                  {"filter": [{"field": "total", "op": "gt", "value": 0}]},
                  {"sort": {"field": "bonus"}}):
        with pytest.raises(QueryError) as refused:
            _list(world, AS_E1, **query)
        assert refused.value.code == "query_op_not_allowed" and "hidden" in refused.value.message
    assert _list(world, AS_HR, filter=[{"field": "bank_account", "op": "eq",
                                         "value": "AC-2"}]).total == 1


def test_r8_a_roll_up_over_rows_that_the_viewer_cannot_read_is_hidden(world) -> None:
    hidden = _get(world, AS_E1, world.t1, world.team.id)
    assert hidden is not None and "headcount" not in hidden.data and hidden.data["name"] == "Plant 4"
    for viewer in (AS_HR, AS_OWNER):
        shown = _get(world, viewer, world.t1, world.team.id)
        assert shown is not None and shown.data["headcount"] >= 4


def test_r8_a_bisection_of_a_hidden_key_gets_one_refusal_for_every_amount(world) -> None:
    """Fix round 1 (P1-4). `total = amount + bonus` holds a range, and `bonus` is
    hidden from an employee. A refusal that follows the amount would show `bonus`
    by bisection, so the store refuses each write of `amount` by the employee,
    with one text that names no hidden key."""
    said: set[str] = set()
    for amount in (1, 500_000, 999_989, 999_990, 999_991, 2_000_000):
        cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1, {"amount": amount}))
        assert isinstance(cause, AccessDenied), (amount, cause)
        said.add(str(cause))
    assert len(said) == 1
    named = set(re.findall(r"`(\w+)`", said.pop()))
    assert "amount" in named and not named & _HIDDEN
    assert _stored(world, world.s1)["amount"] == 100
    full = _refused(world, AS_OWNER, Write("update", world.slip.id, world.s1, {"amount": 2_000_000}))
    assert "`total`" in full.message




# ── Fix round 1 ─────────────────────────────────────────────────────────────


def _team_heads(world: Any, rid: str) -> Any:
    return _stored(world, rid).get("headcount")


def test_r8_two_roles_write_only_the_rows_that_a_write_grant_reads(world) -> None:
    """P1-1. The write right is decided for each record. The manager grant of M1
    reads s1 and writes nothing, so M1 cannot change or delete s1, also with an
    employee grant that writes. An auditor grant reads all and writes nothing, so
    E1 still writes only the slips of E1."""
    before = {rid: _stored(world, rid) for rid in (world.s1, world.s2)}
    for viewer, rid in ((AS_M1_BOTH, world.s1), (AS_E1_AUDIT, world.s2)):
        for write in (Write("update", world.slip.id, rid, {"month": "taken"}),
                      Write("delete", world.slip.id, rid)):
            cause = _refused(world, viewer, write)
            assert isinstance(cause, AccessDenied), (viewer, write.op, cause)
    assert {rid: _stored(world, rid) for rid in (world.s1, world.s2)} == before
    # The own slip of each still takes a write.
    _write(world, AS_M1_BOTH, Write("update", world.slip.id, world.s4, {"month": "2026-04b"}))
    _write(world, AS_E1_AUDIT, Write("update", world.slip.id, world.s1, {"month": "2026-01c"}))
    _write(world, AS_OWNER, Write("update", world.slip.id, world.s4, {"month": "2026-04"}))


def test_r8_a_write_cannot_widen_what_the_viewer_reads(world) -> None:
    """P1-2. M1 holds the manager and the employee grants. On the own slip s4
    (employee M1, manager M2) the employee grant hides `bank_account`. A write of
    `manager = M1` would make the manager grant read s4 too, and show the key. A
    viewer with no grant that writes all cannot set a person field that a `where`
    of the object reads, unless a write grant of the viewer reads that field."""
    cause = _refused(world, AS_M1_BOTH, Write("update", world.slip.id, world.s4, {"manager": M1}))
    assert isinstance(cause, AccessDenied) and "`manager`" in str(cause)
    assert _stored(world, world.s4)["manager"] == M2
    own = _get(world, AS_M1_BOTH, world.s4)
    assert own is not None and "bank_account" not in own.data
    cause = _refused(world, AS_M1_BOTH,
                     Write("create", world.slip.id, data={"employee": M1, "manager": M1}))
    assert isinstance(cause, AccessDenied)
    made = _write(world, AS_M1_BOTH, Write("create", world.slip.id, data={"employee": M1}))[0]
    _write(world, AS_OWNER, Write("delete", world.slip.id, made.id))


def test_r8_a_child_cannot_change_a_roll_up_parent_that_the_viewer_cannot_read(world) -> None:
    """P1-3. E1 reads every slip (the auditor grant) and writes the own slips. E1
    leads t1 and cannot read t2. A slip of t2 would change the head count of t2,
    so the store refuses it, with the text of a team that is not there."""
    before = _team_heads(world, world.t2)
    cause = _refused(world, AS_E1_AUDIT, Write("create", world.slip.id, data={
        "employee": E1, "team": {"id": world.t2}}))
    gone = _refused(world, AS_E1_AUDIT, Write("create", world.slip.id, data={
        "employee": E1, "team": {"id": str(uuid.uuid4())}}))
    assert isinstance(cause, FieldError) and cause.field == "team"
    assert isinstance(gone, FieldError) and gone.message == cause.message
    assert _team_heads(world, world.t2) == before
    moved = _refused(world, AS_E1_AUDIT, Write("update", world.slip.id, world.s1,
                                                {"team": {"id": world.t2}}))
    assert isinstance(moved, FieldError) and moved.message == cause.message
    made = _write(world, AS_E1_AUDIT, Write("create", world.slip.id, data={
        "employee": E1, "team": {"id": world.t1}}))[0]
    _write(world, AS_OWNER, Write("delete", world.slip.id, made.id))
    assert _team_heads(world, world.t2) == before


def test_r8_a_filter_or_a_sort_on_a_field_that_is_not_text_is_refused(world) -> None:
    """P2. A field name that is a list is refused with `query_field_unknown`, for a
    viewer with hidden keys too. It never reaches a set test (a TypeError, a 500)."""
    for query in ({"filter": [{"field": ["bank_account"], "op": "eq", "value": "x"}]},
                  {"sort": {"field": ["bank_account"]}}):
        with pytest.raises(QueryError) as refused:
            _list(world, AS_E1, **query)
        assert refused.value.code == "query_field_unknown"


# ── The writes ──────────────────────────────────────────────────────────────


def test_r8_an_update_or_a_delete_of_another_persons_record_answers_not_found(world) -> None:
    async def go() -> tuple[Any, Any]:
        async with tenant_session(world.a) as db:
            changed = await store.update_record(db, E1, world.slip.id, world.s2, {"month": "x"},
                                                package_id=world.pkg, viewer=AS_E1)
            gone = await store.delete_record(db, E1, world.slip.id, world.s2,
                                             package_id=world.pkg, viewer=AS_E1)
        return changed, gone

    assert _run(world.p, go) == (None, False)
    cause = _refused(world, AS_E1, Write("delete", world.slip.id, world.s3))
    assert isinstance(cause, RecordNotFound)
    assert _stored(world, world.s2)["month"] == "2026-02"
    assert _get(world, AS_OWNER, world.s3) is not None


def test_r8_a_create_or_a_move_into_the_rows_of_another_person_is_refused(world) -> None:
    cause = _refused(world, AS_E1, Write("create", world.slip.id, data={"employee": E2}))
    assert isinstance(cause, AccessDenied) and "employee" in str(cause)
    cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1, {"employee": E2}))
    assert isinstance(cause, AccessDenied)
    assert _stored(world, world.s1)["employee"] == E1


def test_r8_a_role_with_no_write_cannot_change_a_record_that_it_reads(world) -> None:
    cause = _refused(world, AS_M1, Write("update", world.slip.id, world.s1, {"month": "x"}))
    assert isinstance(cause, AccessDenied)
    done = _write(world, AS_HR, Write("update", world.slip.id, world.s3, {"month": "2026-03"}))
    assert isinstance(done[0], store.Record)


def test_r8_a_line_cannot_name_a_parent_that_the_viewer_cannot_read(world) -> None:
    cause = _refused(world, AS_E1, Write("create", world.claim.id,
                                         data={"slip": {"id": world.s2}, "what": "taxi"}))
    assert isinstance(cause, FieldError) and cause.field == "slip"
    missing = _refused(world, AS_E1, Write("create", world.claim.id,
                                           data={"slip": {"id": str(uuid.uuid4())}, "what": "x"}))
    assert isinstance(missing, FieldError) and missing.message == cause.message
    made = _write(world, AS_E1, Write("create", world.claim.id,
                                      data={"slip": {"id": world.s1}, "what": "taxi"}))[0]
    _write(world, AS_OWNER, Write("delete", world.claim.id, made.id))


# ── The declaration ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("where", "said"), [
    ("month", "not one"), ("approver", "must be hot"), ("team", "not one")])
def test_r8_a_where_on_a_cold_field_or_a_field_that_is_not_a_person_is_refused(
        world, where: str, said: str) -> None:
    async def go() -> str:
        async with tenant_session(world.a) as db:
            with pytest.raises(AccessError) as bad:
                await store.set_access(db, world.slip.id, [
                    {"role": "hr", "read": {"where": f"{where} = $me"}}], package_id=world.pkg)
        return bad.value.message

    assert said in _run(world.p, go)
    with world.p.admin_engine.connect() as c:
        kept = c.execute(text("SELECT access FROM studio_objects WHERE id = CAST(:o AS uuid)"),
                         {"o": world.slip.id}).scalar_one()
    assert kept == SLIP_ACCESS


def test_r8_a_person_field_that_a_row_rule_reads_cannot_turn_cold(world) -> None:
    async def go() -> str:
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError) as bad:
                await store.set_field_hot(db, world.slip.id, "employee", False)
        return str(bad.value)

    assert "row rule" in _run(world.p, go)


def test_r8_the_sync_of_a_publish_stores_the_access_and_a_bad_one_writes_nothing(world) -> None:
    def manifest(access: Any) -> list[dict[str, Any]]:
        return [{"name": "claim", "fields": CLAIM_FIELDS},
                {"name": "team", "fields": TEAM_FIELDS, "rules": TEAM_RULES,
                 "access": TEAM_ACCESS},
                {"name": "slip", "fields": SLIP_FIELDS, "rules": SLIP_RULES, "access": access}]

    narrow = [{"role": "hr", "read": "all", "write": "all"}]

    async def go() -> tuple[Any, str]:
        async with tenant_session(world.a) as db:
            done = await store.sync_objects(db, OWNER, package_id=world.pkg,
                                            objects=manifest(narrow))
        bad_access = [{"role": "hr", "read": {"where": "approver = $me"}}]
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectsOutOfSync) as refused:
                await store.sync_objects(db, OWNER, package_id=world.pkg,
                                         objects=manifest(bad_access))
            kind = refused.value.problems[0].kind
        async with tenant_session(world.a) as db:
            await store.sync_objects(db, OWNER, package_id=world.pkg,
                                     objects=manifest(SLIP_ACCESS))
        return done, kind

    done, kind = _run(world.p, go)
    assert done is not None and done.access_set == ("slip",) and done.changed
    assert kind == store.ACCESS_REFUSED
    with world.p.admin_engine.connect() as c:
        kept = c.execute(text("SELECT access FROM studio_objects WHERE id = CAST(:o AS uuid)"),
                         {"o": world.slip.id}).scalar_one()
    assert kept == SLIP_ACCESS


# ── The migration ───────────────────────────────────────────────────────────


def _migration() -> Path:
    files = [p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
             if "studio_objects ADD COLUMN IF NOT EXISTS access" in p.read_text(encoding="utf-8")]
    assert len(files) == 1
    return files[0]


def test_r8_the_checks_hold_a_person_and_the_shape_of_the_access(world) -> None:
    with world.p.admin_engine.connect() as c:
        hot = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'studio_object_fields_hot_type'")).scalar_one()
    assert "person" in hot and "relation" in hot
    for bad in ('"all"', "[]", json.dumps([{"role": "r"}] * 17)):
        with pytest.raises(Exception, match="studio_objects_access_shape"), \
                world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_objects SET access = CAST(:a AS jsonb) "
                           "WHERE id = CAST(:o AS uuid)"), {"a": bad, "o": world.team.id})


def test_r8_the_migration_is_idempotent_and_a_replay_changes_nothing(world) -> None:
    sql = _migration().read_text(encoding="utf-8")
    raw = world.p.admin_engine.raw_connection()
    try:
        conn = raw.driver_connection
        conn.autocommit = True
        for _ in range(2):
            conn.execute(sql)
        conn.autocommit = False
    finally:
        raw.close()
    with world.p.admin_engine.connect() as c:
        kept = c.execute(text("SELECT access FROM studio_objects WHERE id = CAST(:o AS uuid)"),
                         {"o": world.slip.id}).scalar_one()
        count = c.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'studio_objects_access_shape'")
        ).scalar_one()
    assert kept == SLIP_ACCESS and count == 1


# ── Fix round 2 ─────────────────────────────────────────────────────────────


def _write2(world: Any, viewer: Viewer, *writes: Write) -> list[Any]:
    async def go() -> list[Any]:
        async with tenant_session(world.a) as db:
            return await store.apply_writes(db, viewer.email, package_id=world.pkg2,
                                            viewer=viewer, writes=list(writes))
    return _run(world.p, go)


def _refused2(world: Any, viewer: Viewer, *writes: Write) -> Exception:
    with pytest.raises(BatchError) as caught:
        _write2(world, viewer, *writes)
    return caught.value.cause


def _get2(world: Any, viewer: Viewer, oid: str, rid: str) -> store.Record | None:
    async def go() -> store.Record | None:
        async with tenant_session(world.a) as db:
            return await store.get_record(db, oid, rid, package_id=world.pkg2, viewer=viewer)
    return _run(world.p, go)


def test_r8_a_write_all_grant_cannot_move_a_record_into_a_grant_that_hides_less(world) -> None:
    """P1-A. X1 holds a clerk grant (reads and writes all, `salary` hidden) and an
    employee grant (the own record, nothing hidden). A write of `employee = X1` on
    the CEO's record would show `salary`. Each write of a viewer that a rule binds
    must not shrink the hidden keys of the record, also on a create."""
    cause = _refused2(world, AS_CLERK_EMP, Write("update", world.staff.id, world.ceo,
                                                 {"employee": X1}))
    assert isinstance(cause, AccessDenied), cause
    assert _stored(world, world.ceo)["employee"] == CEO
    seen = _get2(world, AS_CLERK_EMP, world.staff.id, world.ceo)
    assert seen is not None and "salary" not in seen.data
    made = _refused2(world, AS_CLERK_EMP, Write("create", world.staff.id, data={"employee": X1}))
    assert isinstance(made, AccessDenied), made
    # A change that hides no less still passes.
    done = _write2(world, AS_CLERK_EMP, Write("update", world.staff.id, world.ceo, {"note": "ok"}))
    assert isinstance(done[0], store.Record) and "salary" not in done[0].data


def test_r8_a_roll_up_over_a_formula_of_a_hidden_key_is_hidden(world) -> None:
    """P1-B. `net = gross - tax` and `gross` is hidden from a clerk, so `net` is
    hidden too. The crew roll-up `net_total` adds `net`, so it would show `gross`
    for a crew of one. The source view of a roll-up runs the formulas."""
    seen = _get2(world, AS_CLERK, world.crew.id, world.c1)
    assert seen is not None and "net_total" not in seen.data, seen
    full = _get2(world, AS_OWNER, world.crew.id, world.c1)
    assert full is not None and full.data["net_total"] == 800


def test_r8_a_delete_cannot_drive_a_rule_over_rows_that_the_viewer_cannot_read(world) -> None:
    """P1-C. The stock of sku A is 5 - 3 = 2, and M1 reads only the own movements.
    A delete of the +5 would break the floor, and a delete of the +1 of sku B
    would not. Each refusal is the same, so a delete tells M1 nothing."""
    said = set()
    for rid in (world.mv1, world.mv3):
        cause = _refused2(world, AS_MOVER, Write("delete", world.movement.id, rid))
        assert isinstance(cause, AccessDenied), (rid, cause)
        said.add(str(cause))
    assert len(said) == 1
    assert _get2(world, AS_OWNER, world.movement.id, world.mv1) is not None
    assert _get2(world, AS_OWNER, world.movement.id, world.mv3) is not None


def test_r8_a_child_write_cannot_drive_a_parent_rule_over_a_hidden_key(world) -> None:
    """P1-D. `margin = total - cost` is at most 100, `total` adds the lines, and
    `cost` (50) is hidden from a clerk. A line of a chosen amount would tell the
    clerk the cost by the refusal, so each line write of the clerk gets one text,
    a delete too."""
    said = set()
    for amount in (10, 140, 150, 151, 200):
        cause = _refused2(world, AS_CLERK, Write("create", world.line.id, data={
            "order": {"id": world.o1}, "amount": amount}))
        assert isinstance(cause, AccessDenied), (amount, cause)
        said.add(str(cause))
    assert len(said) == 1
    gone = _refused2(world, AS_CLERK, Write("delete", world.line.id, world.l0))
    assert isinstance(gone, AccessDenied), gone
    total = _get2(world, AS_OWNER, world.order.id, world.o1)
    assert total is not None and total.data["total"] == 10


def test_r8_an_employee_creates_a_record_when_a_guard_reads_a_hidden_key(world) -> None:
    """P2. The machine fills `status` with its initial state. That is no key that
    the employee sent, so the create passes. A move of the state still reads the
    hidden key, so it stays refused."""
    made = _write2(world, AS_E1, Write("create", world.request.id, data={"by": E1}))[0]
    assert made.data["status"] == "draft"
    moved = _refused2(world, AS_E1, Write("update", world.request.id, made.id,
                                          {"status": "approved"}))
    assert isinstance(moved, AccessDenied)
    _write2(world, AS_OWNER, Write("delete", world.request.id, made.id))


# ── Fix round 3: the invariant ──────────────────────────────────────────────
#
# For a viewer that a row rule or a hide binds, the outcome of a write (a
# success or a refusal) must not follow any value that the viewer cannot read,
# directly or through a chain.


def _write3(world: Any, viewer: Viewer, *writes: Write) -> list[Any]:
    async def go() -> list[Any]:
        async with tenant_session(world.b) as db:
            return await store.apply_writes(db, viewer.email, package_id=world.pkg3,
                                            viewer=viewer, writes=list(writes))
    return _run(world.p, go)


def _refused3(world: Any, viewer: Viewer, *writes: Write) -> Exception:
    with pytest.raises(BatchError) as caught:
        _write3(world, viewer, *writes)
    return caught.value.cause


class _Undo(Exception):
    """Roll the SAVEPOINT of a probe back after a write that passed."""


def _outcome(org: str, pkg: str, world: Any, viewer: Viewer, write: Write) -> str:
    """The outcome of one write, rolled back: `ok`, or the class and the text."""
    async def go() -> str:
        async with tenant_session(org) as db:
            try:
                async with db.begin_nested():
                    await store.apply_writes(db, viewer.email, package_id=pkg, viewer=viewer,
                                             writes=[write])
                    raise _Undo
            except _Undo:
                return "ok"
            except BatchError as exc:
                return f"{type(exc.cause).__name__}: {exc.cause}"
    return _run(world.p, go)


def _set_data(world: Any, rid: str, **values: Any) -> None:
    """The admin writes a stored value that the viewer cannot read."""
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_records SET data = data || CAST(:d AS jsonb) "
                       "WHERE id = CAST(:r AS uuid)"), {"d": json.dumps(values), "r": rid})


def test_r8_sixty_probes_near_the_cap_of_a_formula_get_one_refusal(world) -> None:
    """The probe of 999999999999990 against 999999999999989: `total = amount +
    bonus`, and `bonus` (10) is hidden. A formula error must never come before
    the refusal of the access, so each amount gets one text."""
    said = set()
    for i in range(60):
        cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1,
                                             {"amount": 999_999_999_999_999 - i}))
        said.add(f"{type(cause).__name__}: {cause}")
    assert len(said) == 1, said
    assert said.pop().startswith("AccessDenied")


def test_r8_a_formula_input_of_a_key_rule_over_other_rows_is_blocked(world) -> None:
    """`qty = qty_in - qty_out`, and a stock of `qty` by sku reads rows that M1
    cannot read. A write of `qty_out` moves `qty`, so it is blocked."""
    cause = _refused3(world, AS_MOVER2, Write("update", world.move2.id, world.mine2,
                                              {"qty_out": 5}))
    assert isinstance(cause, AccessDenied), cause


def test_r8_a_chain_of_formulas_blocks_its_first_input(world) -> None:
    """`y = a + a` and `z = cost * y`, and `cost` is hidden from a clerk. A write
    of `a` moves `z`, so it is blocked. The label stays free."""
    cause = _refused3(world, AS_CLERK, Write("update", world.calc.id, world.calc1, {"a": 5}))
    assert isinstance(cause, AccessDenied), cause
    _write3(world, AS_CLERK, Write("update", world.calc.id, world.calc1, {"label": "d"}))


def test_r8_a_formula_input_of_a_child_of_a_hidden_parent_rule_is_blocked(world) -> None:
    """`amount = qty * price` on a line, `total` adds the amounts, and `margin =
    total - cost` is at most 100 with `cost` hidden. A write of `price` moves the
    margin, so it is blocked. The memo stays free."""
    cause = _refused3(world, AS_CLERK, Write("update", world.line2.id, world.l2, {"price": 99}))
    assert isinstance(cause, AccessDenied), cause
    _write3(world, AS_CLERK, Write("update", world.line2.id, world.l2, {"memo": "n"}))


def test_r8_a_child_of_a_key_rule_over_a_formula_of_a_roll_up_is_blocked(world) -> None:
    """`left = cap - spent`, `spent` adds the expenses, and a floor of `left` by
    dept reads the budget of S2. An expense of S1 moves the floor, so it is
    blocked, for each amount."""
    said = set()
    for amount in (1, 99, 100, 101, 10_000):
        cause = _refused3(world, AS_SPENDER, Write("create", world.expense.id, data={
            "budget": {"id": world.b1}, "amount": amount}))
        said.add(f"{type(cause).__name__}: {cause}")
    assert len(said) == 1 and said.pop().startswith("AccessDenied")


def test_r8_a_delete_gets_one_outcome_whatever_the_stored_hidden_values(world) -> None:
    """A delete of a slip with `bonus` and of a slip with no `bonus` gets the
    same outcome. The refusal never reads a stored hidden value."""
    x, y = _write(world, AS_OWNER,
                  Write("create", world.slip.id, data={"employee": E1, "amount": 1, "bonus": 5}),
                  Write("create", world.slip.id, data={"employee": E1}))
    outcomes = {_outcome(world.a, world.pkg, world, AS_E1, Write("delete", world.slip.id, rid))
                for rid in (x.id, y.id)}
    assert len(outcomes) == 1, outcomes
    _write(world, AS_OWNER, Write("delete", world.slip.id, x.id),
           Write("delete", world.slip.id, y.id))


def test_r8_an_initial_state_that_is_a_key_of_a_floor_above_zero_is_blocked(world) -> None:
    """The floor of `points` by `status` is 1, so each create in a status reads the
    tickets of other workers. The initial state is no exemption then."""
    cause = _refused3(world, AS_WORKER, Write("create", world.ticket.id, data={"by": W1}))
    assert isinstance(cause, AccessDenied), cause


def test_r8_a_manager_and_employee_creates_the_own_review(world) -> None:
    """A legit create of two roles: the manager grant hides `notes`, the employee
    grant hides `bank`, and the new record is read by the employee grant alone."""
    made = _write3(world, AS_M1_BOTH, Write("create", world.review.id, data={"employee": M1}))[0]
    assert "bank" not in made.data
    _write3(world, AS_OWNER, Write("delete", world.review.id, made.id))


def test_r8_a_unique_key_over_rows_that_the_viewer_cannot_read_is_blocked(world) -> None:
    """A `unique` of `code` counts the badges of other holders. A taken code and a
    free code get the same refusal, so the refusal shows no other badge."""
    said = set()
    for code in ("B-1", "B-2"):
        cause = _refused3(world, AS_HOLDER, Write("create", world.badge.id, data={
            "by": H1, "code": code}))
        said.add(f"{type(cause).__name__}: {cause}")
    assert len(said) == 1 and said.pop().startswith("AccessDenied")


_HIDDEN_VALUES = (0, 10, 10**15 - 1, -5)


def _probes(fields: Any, skip: set[str], person: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for f in fields:
        key, kind = f["key"], f["type"]
        if key in skip or f.get("lines"):
            continue
        if kind == "number":
            out += [(key, 1), (key, 999_999_999_999_990)]
        elif kind == "text":
            out.append((key, "probe"))
        elif kind == "person":
            out += [(key, person), (key, f"other.{TAG}@stuc8a.test")]
    return out


def test_r8_no_write_of_a_bound_viewer_follows_a_hidden_value(world) -> None:
    """The invariant, as a property. For each world of a bound viewer, each key
    that the viewer may name and each probe value, the outcome of a change and of
    a create is the same for each hidden value: 0, 10, 10**15 - 1 and -5."""
    cases = [
        # (org, package, viewer, object, record, the fields, computed keys,
        #  the record that holds the hidden value, its key, the data of a create)
        (world.a, world.pkg, AS_E1, world.slip, world.s1, SLIP_FIELDS, {"total"},
         world.s1, "bonus", {"employee": E1}),
        (world.b, world.pkg3, AS_CLERK, world.calc, world.calc1, CALC_FIELDS, {"y", "z"},
         world.calc1, "cost", {}),
        (world.b, world.pkg3, AS_CLERK, world.line2, world.l2, LINE2_FIELDS, {"amount"},
         world.o2, "cost", {"order2": {"id": world.o2}}),
    ]
    wrong: list[str] = []
    for org, pkg, viewer, obj, rid, fields, computed, holder, hidden, base in cases:
        for key, value in _probes(fields, computed, viewer.email):
            for op in ("update", "create"):
                seen = set()
                for h in _HIDDEN_VALUES:
                    _set_data(world, holder, **{hidden: h})
                    write = (Write("update", obj.id, rid, {key: value}) if op == "update"
                             else Write("create", obj.id, data={**base, key: value}))
                    seen.add(_outcome(org, pkg, world, viewer, write))
                if len(seen) != 1:
                    wrong.append(f"{obj.name}.{key}={value} {op}: {sorted(seen)}")
    _set_data(world, world.s1, bonus=10)
    _set_data(world, world.calc1, cost=7)
    _set_data(world, world.o2, cost=50)
    assert wrong == []


def test_r8_no_write_of_a_bound_viewer_follows_a_record_that_it_cannot_read(world) -> None:
    """The invariant, as a property, over a value in a record that the viewer
    cannot read. The owner writes each hidden value through the store, so each
    state is one that the store can reach. A value that the owner cannot write
    is no state, and the test skips it. The outcome of each change and each
    create of the viewer is the same for each value that the owner wrote."""
    cases = [
        # (viewer, object, its fields, computed keys, the record of an update,
        #  the data of a create, the object and the record of the value, its key)
        (AS_MOVER2, world.move2, MOVE2_FIELDS, {"qty"}, world.mine2, {"by": M1},
         world.move2, world.other2, "qty_in"),
        (AS_SPENDER, world.budget, BUDGET_FIELDS, {"spent", "left"}, world.b1,
         {"owner": S1}, world.budget, world.b2, "cap"),
        (AS_SPENDER, world.expense, EXPENSE_FIELDS, set(), world.e1,
         {"budget": {"id": world.b1}}, world.budget, world.b2, "cap"),
        (AS_WORKER, world.ticket, TICKET_FIELDS, set(), world.tk2, {"by": W1},
         world.ticket, world.tk2, "points"),
    ]
    wrong: list[str] = []
    reached = 0
    for viewer, obj, fields, computed, rid, base, holder_obj, holder, hidden in cases:
        start = _get3(world, AS_OWNER, holder_obj.id, holder)
        assert start is not None
        states = []
        for h in _HIDDEN_VALUES:
            said = _outcome(world.b, world.pkg3, world, AS_OWNER,
                            Write("update", holder_obj.id, holder, {hidden: h}))
            if said == "ok":
                states.append(h)
        assert states, (obj.name, hidden)
        reached += len(states)
        for key, value in _probes(fields, computed, viewer.email):
            for op in ("update", "create"):
                seen = set()
                for h in states:
                    _write3(world, AS_OWNER, Write("update", holder_obj.id, holder, {hidden: h}))
                    write = (Write("update", obj.id, rid, {key: value}) if op == "update"
                             else Write("create", obj.id, data={**base, key: value}))
                    seen.add(_outcome(world.b, world.pkg3, world, viewer, write))
                if len(seen) != 1:
                    wrong.append(f"{obj.name}.{key}={value} {op}: {sorted(seen)}")
        _write3(world, AS_OWNER, Write("update", holder_obj.id, holder,
                                       {hidden: start.data.get(hidden)}))
    assert reached >= 12, reached
    assert wrong == []


def _get3(world: Any, viewer: Viewer, oid: str, rid: str) -> Any:
    async def go() -> Any:
        async with tenant_session(world.b) as db:
            return await store.get_record(db, oid, rid, package_id=world.pkg3, viewer=viewer)
    return _run(world.p, go)


# ── Fix round 4 ─────────────────────────────────────────────────────────────


def _notes_of(size: int) -> str:
    """A text whose JSON form takes *size* bytes: `\\u0001` takes 6 and `a` takes 1."""
    six, one = divmod(size - 2, 6)
    text_ = "\x01" * six + "a" * one
    assert len(json.dumps(text_).encode("utf-8")) == size
    return text_


def _outcome3(world: Any, viewer: Viewer, *writes: Write) -> str:
    """The outcome of one batch in the third package, rolled back."""
    async def go() -> str:
        async with tenant_session(world.b) as db:
            try:
                async with db.begin_nested():
                    await store.apply_writes(db, viewer.email, package_id=world.pkg3,
                                             viewer=viewer, writes=list(writes))
                    raise _Undo
            except _Undo:
                return "ok"
            except BatchError as exc:
                return f"{type(exc.cause).__name__}: {exc.cause}"
    return _run(world.p, go)


def test_r8_the_record_cap_never_follows_a_hidden_value(world) -> None:
    """Rule 18. sh1 holds a salary of 1 digit, and sh2 a salary of 15 digits and
    a bank of 400 characters, both hidden from the payee. For each length of
    `notes` near the cap, both sheets get the same outcome."""
    wrong = []
    for size in range(65_000, 65_601, 50):
        notes = _notes_of(size)
        pair = {_outcome3(world, AS_PAYEE, Write("update", world.sheet.id, rid, {"notes": notes}))
                for rid in (world.sh1, world.sh2)}
        if len(pair) != 1:
            wrong.append((size, sorted(pair)))
    assert wrong == []


def test_r8_a_visible_write_of_an_ordinary_size_still_passes(world) -> None:
    """Rule 18. The store counts each hidden value at its largest, and a write
    of an ordinary size still passes on each sheet."""
    for rid in (world.sh1, world.sh2):
        for size in (2, 500, 20_000, 40_000):
            said = _outcome3(world, AS_PAYEE,
                             Write("update", world.sheet.id, rid, {"notes": _notes_of(size)}))
            assert said == "ok", (rid, size, said)


def test_r8_a_derived_write_counts_a_hidden_roll_up_at_its_largest(world) -> None:
    """Rule 18, the derived write. `secret_sum` is 9 on f1 and 15 digits on f2,
    and it is hidden from a peeker. The owner fills `notes` so that f2 would be
    one byte over the cap after an item of 1000000. The item gets the same
    outcome on each folder."""
    d2 = _get3(world, AS_OWNER, world.folder.id, world.f2)
    assert d2 is not None and d2.data["secret_sum"] == 999_999_999_999_999
    rest = {k: v for k, v in d2.data.items() if k != "notes"}
    rest["open_sum"] = 1_000_000
    others = sum(len(json.dumps({k: v}).encode("utf-8")) for k, v in rest.items())
    notes = _notes_of(MAX_RECORD_BYTES + 1 - others - len('{"notes": }'))
    for rid in (world.f1, world.f2):
        _write3(world, AS_OWNER, Write("update", world.folder.id, rid, {"notes": notes}))
    said = {_outcome3(world, AS_PEEKER, Write("create", world.item.id, data={
        "folder": {"id": rid}, "amount": 1_000_000})) for rid in (world.f1, world.f2)}
    for rid in (world.f1, world.f2):
        _write3(world, AS_OWNER, Write("update", world.folder.id, rid, {"notes": None}))
    assert len(said) == 1, said
    assert said.pop().startswith("RecordError")


def test_r8_a_line_save_never_reads_a_hidden_parent_field(world) -> None:
    """Rule 19. A stale grant hides `bill` from a payer. A save of bill1 with r1,
    its own line, and with r2, a line of bill2, gets the same outcome."""
    said = {_outcome3(world, AS_PAYER,
                      Write("update", world.bill.id, world.bill1, {"memo": "m"}),
                      Write("update", world.row.id, rid, {"what": "w"}, parent=0))
            for rid in (world.r1, world.r2)}
    assert len(said) == 1, said
    assert said.pop().startswith("AccessDenied")


def test_r8_a_grant_cannot_hide_the_parent_field_of_a_line(world) -> None:
    """Rule 19. Now that bill names its rows, a grant of a row cannot hide `bill`."""
    async def go() -> None:
        async with tenant_session(world.b) as db:
            with pytest.raises(AccessError) as refused:
                await store.set_access(db, world.row.id, ROW_ACCESS, package_id=world.pkg3)
            assert refused.value.path == "[0].hide"
    _run(world.p, go)


def test_r8_an_employee_deletes_the_own_draft_and_the_own_badge(world) -> None:
    """Rule 14. A guard and a `unique` never run on a delete, so each owner of a
    record deletes it."""
    draft = _write2(world, AS_E1, Write("create", world.request.id, data={"by": E1}))[0]
    _write2(world, AS_E1, Write("delete", world.request.id, draft.id))
    badge = _write3(world, AS_HOLDER, Write("create", world.badge.id, data={"by": H1}))[0]
    _write3(world, AS_HOLDER, Write("delete", world.badge.id, badge.id))


def test_r8_a_delete_that_moves_a_tainted_rule_is_refused_for_each_value(world) -> None:
    """Rule 14. A delete that lowers a stock over rows that the viewer cannot
    read, and a delete of an expense under a floor of its budget, are refused
    with one text, for each value that the owner wrote."""
    cases = [(AS_MOVER2, world.move2, world.mine2, world.move2, world.other2, "qty_in"),
             (AS_SPENDER, world.expense, world.e1, world.budget, world.b2, "cap")]
    for viewer, obj, rid, holder_obj, holder, key in cases:
        start = _get3(world, AS_OWNER, holder_obj.id, holder)
        assert start is not None
        said = set()
        for value in _HIDDEN_VALUES:
            change = Write("update", holder_obj.id, holder, {key: value})
            if _outcome3(world, AS_OWNER, change) != "ok":
                continue
            _write3(world, AS_OWNER, change)
            said.add(_outcome3(world, viewer, Write("delete", obj.id, rid)))
        _write3(world, AS_OWNER, Write("update", holder_obj.id, holder,
                                       {key: start.data.get(key)}))
        assert len(said) == 1, (obj.name, said)
        assert said.pop().startswith("AccessDenied: Your roles cannot delete")



def test_r8_the_access_check_of_a_change_comes_before_each_formula(world) -> None:
    """Rule 13, the order. `a` is blocked for a clerk, because `y = a + a` feeds
    `z = cost * y` and `cost` is hidden. A change of `a` near 10^15 would also
    make `a5 = a + 5` fail. Each probe, for each stored `cost`, gets the one
    AccessDenied text.

    Mutation: move `_check_write(held, mark.after, checked)` after
    `clean = _formulas(held, checked, mark.after)` in `_apply_write`. The
    probes from 999999999999995 up then get a formula error, and this test
    turns red. A formula that reads a hidden input is frozen, so it never
    raises on a change (rule 13). `a5` reads the blocked key alone."""
    said = set()
    for cost in (7, 10**15 - 1):
        _set_data(world, world.calc1, cost=cost)
        for i in range(12):
            said.add(_outcome3(world, AS_CLERK, Write("update", world.calc.id, world.calc1,
                                                      {"a": 999_999_999_999_999 - i})))
    _set_data(world, world.calc1, cost=7)
    assert len(said) == 1, said
    assert said.pop() == ("AccessDenied: Your roles cannot set `a` of `calc`, because a "
                          "rule over data that you cannot read uses it.")
