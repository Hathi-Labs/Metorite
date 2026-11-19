"""R8 — verified queries on a real Postgres (WS-SEM SEM-8, task 3738).

Spec: ``project-docs/specs/semantic_model.md`` §6.3 and §8.

As the non-privileged role of the phase-4 rehearsal, with the seed of
``test_crm_questions_r8.py``:

1. Org A: an admin saves a verified question. The plan keeps no value. A
   member matches it and runs it through ``GET /crm/aggregate``, and the
   total counts only the records that the filters name.
2. A field leaves the catalogue. The question is stale at read time, never
   lowered, and listed to the admin. A restore of the version that held it
   keeps it. A new item on the gone field is refused. One repair becomes a
   draft, and a second one waits for it.
3. Org B never sees and never runs a question of org A, and its records
   never reach the total of org A.
4. A member that the access rule of the CRM does not admit gets no row.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    call,
    defs,
    q_env,
    seed_a,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

#: The words that a save turns into parameters. None may reach the plan.
_CUSTOMER = "Asha Rao"
_SAVE = {
    "question": "Net value for Asha Rao in October 2026",
    "module": "Invoices", "aggregate": "sum", "fields": ["Net_Value"],
    "where": [{"field": "Buyer", "op": "eq", "literal": _CUSTOMER, "name": "customer"},
              {"field": "Invoice_Date", "op": "on", "literal": "October 2026",
               "name": "month", "kind": "month"}],
}
_KEY = "net_value__customer_month"
_VALUES = {"customer": _CUSTOMER, "month": "2026-10"}


def _invoice(name: str, buyer: str, net: int, day: str, billed: int = 0) -> dict:
    return {"Name": name, "Net_Value": net, "Billed_Value": billed, "Invoice_Date": day,
            "Buyer": {"id": f"b-{buyer[:4]}", "name": buyer}}


def vq_records(p: Any, org: str, module: str, rows: list[dict]) -> None:
    """Records with ext ids of their own (``VQ-<i>``), so they never meet
    the ``<Module>-<i>`` ids of ``seed_a`` on ``crm_module_records_ext_key``.
    The ``q_env`` teardown deletes each row of both orgs."""
    with p.admin_engine.begin() as c:
        for i, fields in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :n, "
                "CAST(:f AS jsonb))"),
                {"o": org, "m": module, "e": f"VQ-{i}", "n": fields["Name"],
                 "f": json.dumps(fields)})


def seed_invoices(p: Any, org: str) -> None:
    vq_records(p, org, "Invoices", [
        _invoice("V-1", _CUSTOMER, 100, "2026-10-05", billed=10),
        _invoice("V-2", _CUSTOMER, 50, "2026-11-02", billed=20),
        _invoice("V-3", "Ravi Kumar", 70, "2026-10-09", billed=30),
    ])


@pytest.fixture
def sem_env(q_env, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "semantic_model_orgs", "*", raising=False)
    yield q_env


async def as_user(p: Any, user: Any, fn: Any) -> Any:
    """*fn()* with the tenant of *user* bound, as the app role."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    scope = clear_tenant()
    token = bind_tenant(user.organization_id)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await fn()
    finally:
        release_tenant(token)
        release_tenant(scope)


def _approved(p: Any, org: str) -> Any:
    with p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT version, rules FROM crm_mapping_plans WHERE organization_id = "
            "CAST(:o AS uuid) AND state = 'approved'"), {"o": org}).first()


def _drafts(p: Any, org: str) -> list[Any]:
    with p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT version, rules, created_by FROM crm_mapping_plans WHERE organization_id "
            "= CAST(:o AS uuid) AND state = 'draft' ORDER BY version"), {"o": org}).fetchall()


def _queries(rules: Any) -> list[dict]:
    return [o for o in rules if isinstance(o, dict) and o.get("kind") == "query"]


async def _save(p: Any, user: Any, body: dict) -> Any:
    from gateway.routes.crm import verified_queries as vq

    return await as_user(p, user, lambda: vq.save(user, vq.SaveIn(**body)))


async def _lower(p: Any, user: Any, values: dict, key: str = _KEY) -> Any:
    from urllib.parse import urlencode

    query = urlencode({f"param.{k}": v for k, v in values.items()})
    return await call(p, user, "GET", f"/crm/verified-queries/{key}?{query}")


async def _total(p: Any, user: Any, lowered: dict) -> Any:
    from urllib.parse import urlencode

    got = await call(p, user, "GET", f"/crm/aggregate?{urlencode(lowered['aggregate'])}")
    assert got.status_code == 200, got.text
    return got.json()["total"]


async def _drop_net_value(p: Any, org: str) -> None:
    """The source renames the field: ``Net_Value`` leaves, ``Net_Amount``
    comes."""
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM crm_field_defs WHERE organization_id = CAST(:o AS uuid) "
                       "AND entity = 'module:Invoices' AND key = 'Net_Value'"), {"o": org})
    defs(p, org, "Invoices", [("Net_Amount", "currency", None)])


async def _remove_query(p: Any, org: str) -> Any:
    """Approve a version with no verified question, through ``commit``."""
    from gateway.routes.crm import mapping_plans as mpl
    from gateway.routes.crm.core import _tenant_session

    async def run() -> Any:
        async with _tenant_session() as db:
            approved = await mpl.lock_plan(db, "r8")
            fields, others = mpl.split_rules(mpl._rules_of(approved))
            kept = [o for o in others if o.get("kind") != "query"]
            return await mpl.commit(db, org=org, fields=fields, others=kept, by="r8",
                                    based_on=int(approved.version))

    return await as_user(p, _user(org), run)


async def test_save_match_run_stale_restore_and_repair_for_one_org(
    sem_env, app_engine,
) -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import verified_queries as vq

    p = sem_env
    seed_a(p)
    seed_invoices(p, p.org_a)
    admin, member = _user(p.org_a), _user(p.org_a, admin=False)

    # 1. Save: a new approved version, with parameter names and no value.
    saved = await _save(p, admin, _SAVE)
    row = _approved(p, p.org_a)
    assert int(row.version) == int(saved.version) == 2
    [item] = _queries(row.rules)
    assert item["question"] == "Net value for {customer} in {month}"
    # The version that the save committed.
    assert item["model_version"] == 2
    stored = json.dumps(row.rules)
    for word in ("Asha", "Rao", "October", "2026", "total"):
        assert word not in stored, word
    # A member is no admin, so a member never saves.
    with pytest.raises(HTTPException) as exc:
        await _save(p, member, _SAVE)
    assert exc.value.status_code == 403

    # 2. Match and run, as a member.
    got = await call(p, member, "POST", "/crm/verified-queries/match",
                     {"q": "What was the net value for Asha Rao in October 2026?"})
    assert got.status_code == 200, got.text
    assert [m["key"] for m in got.json()["matches"]] == [_KEY]
    assert got.json()["stale"] == []
    lowered = await _lower(p, member, _VALUES)
    assert lowered.status_code == 200, lowered.text
    body = lowered.json()
    assert body["verified"] is True and body["entity"] == "module:Invoices"
    # Only V-1: the other customer and the other month stay out.
    assert await _total(p, member, body) == 100
    answer = await as_user(p, member, lambda: vq.run_query(member, _KEY, _VALUES))
    assert answer["verified"] is True and answer["answer"].total == 100

    # 3. A version with no verified question, then the field leaves.
    await _remove_query(p, p.org_a)
    assert _queries(_approved(p, p.org_a).rules) == []
    await _drop_net_value(p, p.org_a)

    # 4. Restore the version that held it: kept, and stale at read time.
    restored = await call(p, admin, "POST", "/crm/mapping/plans/2/restore")
    assert restored.status_code == 201, restored.text
    assert _queries(_approved(p, p.org_a).rules) == [item]
    got = await call(p, admin, "POST", "/crm/verified-queries/match",
                     {"q": "net value for Asha Rao in October 2026"})
    assert got.json()["matches"] == []
    assert got.json()["stale"] == [{"key": _KEY, "question": item["question"],
                                    "stale_reason": "field:Net_Value"}]
    as_member = await call(p, member, "POST", "/crm/verified-queries/match", {"q": ""})
    assert as_member.json()["stale"] == []
    stale = await _lower(p, member, _VALUES)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_query"

    # 5. A NEW item on the gone field is refused.
    with pytest.raises(HTTPException) as exc:
        await _save(p, admin, {**_SAVE, "key": "net_again"})
    assert exc.value.detail["code"] == "stale_query"

    # 6. No field clearly stands for Net_Value: Net_Amount and Billed_Value
    #    each share one word. So no repair, and no draft.
    with pytest.raises(HTTPException) as exc:
        await as_user(p, admin, lambda: vq.propose_repair(admin, _KEY))
    assert exc.value.detail["code"] == "no_repair" and _drafts(p, p.org_a) == []

    # 7. The source brings the field back under a new key, with the label
    #    "Net Value". One repair, as a draft, onto that field.
    defs(p, p.org_a, "Invoices", [("NetValue", "currency", None)])
    draft = await as_user(p, admin, lambda: vq.propose_repair(admin, _KEY,
                                                              {"customer": _CUSTOMER}))
    [held] = _drafts(p, p.org_a)
    assert int(held.version) == int(draft.version) and held.created_by == "suggestion"
    [fixed] = _queries(held.rules)
    assert fixed["query"]["fields"] == ["NetValue"]
    assert fixed["params"] == item["params"] and fixed["question"] == item["question"]
    # The approved version still holds the stale item: no approve in silence.
    assert _queries(_approved(p, p.org_a).rules) == [item]
    with pytest.raises(HTTPException) as exc:
        await as_user(p, admin, lambda: vq.propose_repair(admin, _KEY))
    assert exc.value.detail["code"] == "repair_pending"
    assert len(_drafts(p, p.org_a)) == 1


async def test_org_b_never_sees_or_runs_a_question_of_org_a(sem_env, app_engine) -> None:
    from fastapi import HTTPException
    from gateway.routes.crm import verified_queries as vq

    from tests.unit.test_crm_questions_r8 import mirror, module_row

    p = sem_env
    seed_a(p)
    seed_invoices(p, p.org_a)
    # Org B holds records of the same module and the same customer.
    mirror(p, p.org_b, [module_row("Invoices")])
    defs(p, p.org_b, "Invoices", [("Net_Value", "currency", None),
                                  ("Invoice_Date", "date", None),
                                  ("Buyer", "lookup", "Buyer_Clients")])
    vq_records(p, p.org_b, "Invoices", [_invoice("B-1", _CUSTOMER, 9000, "2026-10-01")])
    await _save(p, _user(p.org_a), _SAVE)
    b_admin = _user(p.org_b)

    got = await call(p, b_admin, "POST", "/crm/verified-queries/match",
                     {"q": "net value for Asha Rao in October 2026"})
    assert got.status_code == 200 and got.json() == {"matches": [], "stale": []}
    lowered = await _lower(p, b_admin, _VALUES)
    assert lowered.status_code == 404 and lowered.json()["detail"]["code"] == "no_query"
    with pytest.raises(HTTPException) as exc:
        await as_user(p, b_admin, lambda: vq.run_query(b_admin, _KEY, _VALUES))
    assert exc.value.status_code == 404
    assert _approved(p, p.org_b) is None
    # Org A's total never counts org B's record.
    member_a = _user(p.org_a, admin=False)
    body = (await _lower(p, member_a, _VALUES)).json()
    assert await _total(p, member_a, body) == 100


async def test_a_member_that_the_crm_does_not_admit_gets_no_row(
    sem_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings

    p = sem_env
    seed_a(p)
    seed_invoices(p, p.org_a)
    await _save(p, _user(p.org_a), _SAVE)
    member = _user(p.org_a, admin=False)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'admins' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": p.org_a})
    got = await call(p, member, "POST", "/crm/verified-queries/match",
                     {"q": "net value for Asha Rao in October 2026"})
    assert got.status_code == 403
    assert (await _lower(p, member, _VALUES)).status_code == 403
    assert (await call(p, member, "GET", "/crm/aggregate?entity=module:Invoices"
                       "&measure=sum&field=Net_Value")).status_code == 403
    # The flag off: no org reads one, admin or member.
    monkeypatch.setattr(get_settings(), "semantic_model_orgs", "", raising=False)
    off = await _lower(p, _user(p.org_a), _VALUES)
    assert off.status_code == 404 and off.json()["detail"]["code"] == "semantic_off"


async def test_a_sum_of_two_fields_is_one_total(sem_env, app_engine) -> None:
    """P1-A: a verified question that sums two fields answers ONE total of
    both, as the member, through ``GET /crm/aggregate``."""
    from gateway.routes.crm import verified_queries as vq

    p = sem_env
    seed_a(p)
    seed_invoices(p, p.org_a)
    body = {**_SAVE, "question": "Net and billed for Asha Rao in October 2026",
            "fields": ["Net_Value", "Billed_Value"]}
    await _save(p, _user(p.org_a), body)
    member = _user(p.org_a, admin=False)
    key = "net_billed__customer_month"
    lowered = await _lower(p, member, _VALUES, key)
    assert lowered.status_code == 200, lowered.text
    aggregate = lowered.json()["aggregate"]
    assert aggregate["field"] == "Net_Value+Billed_Value" and "fields" not in aggregate
    # V-1 only: 100 net and 10 billed.
    assert await _total(p, member, lowered.json()) == 110
    answer = await as_user(p, member, lambda: vq.run_query(member, key, _VALUES))
    assert answer["answer"].total == 110
