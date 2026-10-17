"""R8 — ``PUT /crm/catalogue/{module}``, the corrections of CRM-Q31-4
(WS-53 CRM-Q32-2 item 3).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.6 and CRM-Q31-4 items 3,
4 and 6 of §18.13.6.

Against a REAL Postgres, as the non-privileged role of the phase-4
rehearsal, with the seed of ``test_crm_questions_r8.py``.

* A member who is not an admin gets 403. Native mode gets 409. A field of
  the wrong type gets 422. ``"@auto"`` clears the admin part. An admin of
  org B cannot change the catalogue of org A (CRM-Q31-4 item 6).
* An org outside ``CRM_QUESTIONS_ORGS`` gets 404 ``questions_off``.
* The PUT and the answer route write through the one writer.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit.test_crm_questions_answer_r8 import item_of, versions
from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    call,
    mirror,
    q_env,
    seed_a,
    seed_b,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

REPO = Path(__file__).resolve().parents[2]
PUT = "/crm/catalogue/{}"


async def put(p: Any, org: str, module: str, body: dict, *, admin: bool = True) -> Any:
    return await call(p, _user(org, admin=admin), "PUT", PUT.format(module), body)


async def test_an_admin_changes_a_part_and_auto_clears_it(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    got = await put(p, p.org_a, "Invoices", {"amount": "Billed_Value", "purpose": "invoice"})
    assert got.status_code == 200, got.text
    entry = got.json()["entry"]
    assert (entry["fields"]["amount"]["key"], entry["by"]["amount"]) == ("Billed_Value", "admin")
    assert entry["by"]["purpose"] == "admin"
    # null: no field holds the part, by the admin.
    got = await put(p, p.org_a, "Invoices", {"date": None})
    assert got.json()["entry"]["fields"]["date"] is None
    assert got.json()["entry"]["by"]["date"] == "admin"
    # "@auto": the admin part goes, and the answer of code shows again.
    got = await put(p, p.org_a, "Invoices", {"date": "@auto", "purpose": "@auto"})
    assert got.status_code == 200, got.text
    entry = got.json()["entry"]
    assert (entry["fields"]["date"]["key"], entry["by"]["date"]) == ("Invoice_Date", "code")
    assert entry["by"]["purpose"] == "code"
    item = item_of(p, p.org_a, "Invoices")
    assert "date" not in item and "purpose" not in item
    assert item["by"] == {"amount": "admin"}


async def test_a_field_of_the_wrong_type_is_422(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    before = versions(p, p.org_a)
    for body in ({"amount": "Invoice_Date"}, {"amount": "Nope"}, {"purpose": "castle"},
                 {"parent": "Status"}, {"revenue": "invoice"}, {}):
        got = await put(p, p.org_a, "Invoices", body)
        assert got.status_code == 422, (body, got.text)
    unknown = await put(p, p.org_a, "Not_A_Module", {"purpose": "asset"})
    assert unknown.status_code == 404, unknown.text
    extra = await put(p, p.org_a, "Invoices", {"colour": "red"})
    assert extra.status_code == 422
    assert versions(p, p.org_a) == before


async def test_the_org_item_takes_revenue_only(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    got = await put(p, p.org_a, "@org", {"revenue": "payment_in"})
    assert got.status_code == 200, got.text
    assert got.json()["revenue"] == {"value": "payment_in", "by": "admin"}
    assert (await put(p, p.org_a, "@org", {"amount": "X"})).status_code == 422
    got = await put(p, p.org_a, "@org", {"revenue": "@auto"})
    assert got.json()["revenue"] == {"value": None, "by": None}
    assert item_of(p, p.org_a, "@org") is None


async def test_a_member_gets_403_native_gets_409_and_outside_the_list_404(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings

    p = q_env
    seed_a(p)
    member = await put(p, p.org_a, "Invoices", {"amount": "Billed_Value"}, admin=False)
    assert member.status_code == 403, member.text
    mirror(p, p.org_b, [], mode="native")
    native = await put(p, p.org_b, "Invoices", {"amount": "Billed_Value"})
    assert native.status_code == 409 and native.json()["detail"]["code"] == "not_mirror"
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    off = await put(p, p.org_a, "Invoices", {"amount": "Billed_Value"})
    assert off.status_code == 404 and off.json()["detail"]["code"] == "questions_off"
    assert item_of(p, p.org_a, "Invoices")["amount"] == "Net_Value"


async def test_an_admin_of_org_b_cannot_change_the_catalogue_of_org_a(
    q_env, app_engine,
) -> None:
    p = q_env
    seed_a(p)
    seed_b(p)
    before = item_of(p, p.org_a, "Invoices")
    got = await put(p, p.org_b, "Invoices", {"amount": "Billed_Value"})
    assert got.status_code == 404, got.text
    got = await put(p, p.org_b, "Fee_Receipts", {"amount": "Part_A"})
    assert got.status_code == 404, got.text
    assert item_of(p, p.org_a, "Invoices") == before
    assert item_of(p, p.org_a, "Fee_Receipts") is None


def test_the_put_and_the_answer_route_share_one_writer() -> None:
    """§18.14.6: one writer serves both doors. Neither route calls
    ``apply_catalogue`` but through ``write_answer``, and the batch is the
    one other caller, with no ``clear``."""
    import ast

    for name, allowed in (("catalogue.py", {"write_answer"}),
                          ("questions.py", {"accept_best"})):
        path = REPO / "apps/services/gateway/gateway/routes/crm" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callers = {
            fn.name for fn in ast.walk(tree)
            if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "apply_catalogue"
        }
        assert callers <= allowed, (name, callers)
    text = (REPO / "apps/services/gateway/gateway/routes/crm/questions.py").read_text(
        encoding="utf-8")
    assert "write_answer(" in text
