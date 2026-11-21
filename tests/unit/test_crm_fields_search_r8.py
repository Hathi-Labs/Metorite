"""R8 — the field search against a real Postgres (WS-53 CRM-Q34-6, task 3699).

Through the real ``/crm`` router, as the non-privileged role of the phase-4
rehearsal (no superuser, no BYPASSRLS). The seed goes in as the superuser,
so each read below is the first thing the app role does. A hermetic fake
agrees with any SQL that it gets, so none of these is proved without the
database.

1. Another org's fields never show, also with both orgs on the list.
2. The search finds a field by the words of its label.
3. The search finds a field by the camel-case words of its key, where the
   label holds neither word.
4. With no module, it reads each mirrored module, in the profile order, and
   never a module that the profile does not mirror or a retired field.
5. The fill of a hit, and of the plain module list, on ``CRM_FILL_RANK_ORGS``.
6. With ``CRM_MODULE_FIND_ORGS`` empty, the route answers 404
   ``module_find_off``, and the chat tool, run against the real router,
   answers a ``query`` byte for byte as the same call with no ``query``.

The hermetic rules are in ``test_crm_fields_search.py``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_aggregate_fields_r8 import (  # noqa: F401
    _admin,
    _call,
    _mirror_org,
    _module,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

#: ``(key, label, type, retired)`` of each module field of org A.
_A_PRODUCTS = [
    ("Unit_Price", "Unit Price", "currency", False),
    ("cf_9001", "Warranty Expiry", "date", False),
    ("InstallDate", "Commissioned On", "date", False),
    ("Total_Weight", "Total Weight", "number", False),
    ("Old_Code", "Old Code", "text", True),
]
_A_QUOTES = [("Grand_Total", "Grand Total", "currency", False)]
#: A module of the profile that the org does not mirror.
_A_VENDORS = [("Vendor_Total", "Vendor Total", "number", False)]
#: Org B: the same module name, with a field that org A does not have.
_B_PRODUCTS = [
    ("Unit_Price", "Unit Price", "currency", False),
    ("Secret_Margin", "Secret Margin", "number", False),
]


def _defs(p: Any, org: str, entity: str, rows: list[tuple]) -> None:
    with p.admin_engine.begin() as c:
        for position, (key, label, ftype, retired) in enumerate(rows):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
                "type, source, provider_api_name, position, retired_at) VALUES "
                "(CAST(:o AS uuid), :e, :k, :l, :t, 'zoho', :k, :pos, "
                "CASE WHEN :ret THEN now() END)"),
                {"o": org, "e": entity, "k": key, "l": label, "t": ftype,
                 "pos": position, "ret": retired})


def _fill(p: Any, org: str, entity: str, key: str, filled: int, total: int) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_fill (organization_id, entity, key, filled, total, "
            "distinct_count) VALUES (CAST(:o AS uuid), :e, :k, :f, :t, 2)"),
            {"o": org, "e": entity, "k": key, "f": filled, "t": total})


def _set(monkeypatch: Any, name: str, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), name, value, raising=False)


@pytest.fixture
def finds(ns, monkeypatch):
    """Org A mirrors Products and Quotes, and not Vendors. Org B mirrors
    Products. Both orgs are on ``CRM_MODULE_FIND_ORGS``. Only org A is on
    ``CRM_FILL_RANK_ORGS``."""
    a, b = ns.org_a, ns.org_b
    _mirror_org(ns, a, [_module("Products"), _module("Quotes"),
                        _module("Vendors", mirrored=False)])
    _mirror_org(ns, b, [_module("Products")])
    _defs(ns, a, "module:Products", _A_PRODUCTS)
    _defs(ns, a, "module:Quotes", _A_QUOTES)
    _defs(ns, a, "module:Vendors", _A_VENDORS)
    _defs(ns, b, "module:Products", _B_PRODUCTS)
    _fill(ns, a, "module:Products", "Unit_Price", 3, 4)
    _fill(ns, b, "module:Products", "Secret_Margin", 1, 1)
    _set(monkeypatch, "crm_module_find_orgs", f"{a},{b}")
    _set(monkeypatch, "crm_fill_rank_orgs", a)
    yield ns
    with ns.admin_engine.begin() as c:
        for org in (a, b):
            c.execute(text("DELETE FROM crm_field_fill WHERE organization_id = "
                           "CAST(:o AS uuid)"), {"o": org})


async def _search(p: Any, org: str, **query: Any) -> Any:
    return await _call(p, _admin(org), "GET", "/crm/fields-search?" + urlencode(query))


async def _hits(p: Any, org: str, **query: Any) -> list[tuple[str, str]]:
    res = await _search(p, org, **query)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == len(body["hits"])
    return [(h["module"], h["key"]) for h in body["hits"]]


# ── 1. Another org ───────────────────────────────────────────────────────────


async def test_another_orgs_fields_never_show(finds, app_engine) -> None:
    p = finds
    assert await _hits(p, p.org_a, q="secret margin") == []
    assert await _hits(p, p.org_a, q="margin", module="Products") == []
    assert await _hits(p, p.org_b, q="secret margin") == [("Products", "Secret_Margin")]
    # The same module name in two orgs: each org reads only its own row.
    assert await _hits(p, p.org_a, q="unit price") == [("Products", "Unit_Price")]
    assert await _hits(p, p.org_b, q="unit price") == [("Products", "Unit_Price")]
    # A module that only org A mirrors is unknown to org B.
    res = await _search(p, p.org_b, q="grand total", module="Quotes")
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "unknown_module"


# ── 2. The label words ───────────────────────────────────────────────────────


async def test_the_search_finds_a_field_by_its_label_words(finds, app_engine) -> None:
    p = finds
    for q in ("warranty expiry", "expiry warranty", "Warranty", "warr"):
        assert await _hits(p, p.org_a, q=q) == [("Products", "cf_9001")], q
    res = await _search(p, p.org_a, q="warranty", module="Products")
    assert res.json()["hits"] == [{
        "module": "Products", "module_label": "Products", "key": "cf_9001",
        "label": "Warranty Expiry", "type": "date", "fill": None,
    }]
    assert await _hits(p, p.org_a, q="warranty start") == []


# ── 3. The camel-case words of a key ─────────────────────────────────────────


async def test_the_search_finds_a_field_by_its_camel_case_key_words(finds, app_engine) -> None:
    p = finds
    # The label "Commissioned On" holds neither word. Only the key does.
    assert await _hits(p, p.org_a, q="install date") == [("Products", "InstallDate")]
    assert await _hits(p, p.org_a, q="commissioned") == [("Products", "InstallDate")]


# ── 4. Each mirrored module ──────────────────────────────────────────────────


async def test_each_mirrored_module_in_profile_order(finds, app_engine) -> None:
    p = finds
    # Vendors is in the profile, but the org does not mirror it.
    assert await _hits(p, p.org_a, q="total") == [
        ("Products", "Total_Weight"), ("Quotes", "Grand_Total"),
    ]
    assert await _hits(p, p.org_a, q="total", module="Quotes") == [("Quotes", "Grand_Total")]
    # A retired field is no hit.
    assert await _hits(p, p.org_a, q="old code") == []
    res = await _search(p, p.org_a, q="vendor total", module="Vendors")
    assert res.status_code == 404 and res.json()["detail"]["code"] == "unknown_module"


async def test_a_query_with_no_word_is_a_422(finds, app_engine) -> None:
    p = finds
    for q in ("", "&&", "x" * 201):
        assert (await _search(p, p.org_a, q=q)).status_code == 422, q


# ── 5. The fill ──────────────────────────────────────────────────────────────


async def test_the_fill_of_a_hit_and_of_the_module_list(finds, app_engine) -> None:
    p = finds
    res = await _search(p, p.org_a, q="unit price")
    assert res.json()["hits"][0]["fill"] == 0.75
    # Org B is not on CRM_FILL_RANK_ORGS, so it reads no number.
    res = await _search(p, p.org_b, q="secret")
    assert res.json()["hits"][0]["fill"] is None
    # The plain list of a module passes the org too, so the fill shows there.
    fields_a = await _call(p, _admin(p.org_a), "GET", "/crm/fields/module:Products")
    by_key = {f["key"]: f for f in fields_a.json()}
    assert (by_key["Unit_Price"]["fill"], by_key["Unit_Price"]["distinct"]) == (0.75, 2)
    assert by_key["cf_9001"]["fill"] is None
    fields_b = await _call(p, _admin(p.org_b), "GET", "/crm/fields/module:Products")
    assert {f["fill"] for f in fields_b.json()} == {None}


# ── 6. The empty list ────────────────────────────────────────────────────────


async def _tool(p: Any, org: str, monkeypatch: Any, **kwargs: Any) -> str:
    """``crm_list_fields`` as the chat calls it, with its HTTP sent to the
    real router of this test, as the admin of *org*."""
    import acb_skills.crm_tools as T
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._crm_app import crm_app

    user = _admin(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = crm_app()
    app.dependency_overrides[get_current_user] = _caller
    monkeypatch.setattr(T, "_current_user_email", lambda: user.email)
    monkeypatch.setattr(T, "httpx", SimpleNamespace(
        AsyncClient=lambda **kw: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), **kw)))
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await T.crm_list_fields(**kwargs)
    finally:
        release_tenant(scope)


async def test_an_empty_list_answers_as_before(finds, app_engine, monkeypatch) -> None:
    p = finds
    _set(monkeypatch, "crm_module_find_orgs", "")
    res = await _search(p, p.org_a, q="unit price")
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "module_find_off"
    # The tool, through the real router: a query changes no byte of the answer.
    plain = await _tool(p, p.org_a, monkeypatch, entity="module:Products")
    found = await _tool(p, p.org_a, monkeypatch, entity="module:Products", query="unit price")
    assert found == plain
    # Five: the plain list of a module reads `modules._DEFS_SQL`, which keeps
    # a retired field. The search leaves it out (test 4).
    assert plain.startswith("Products fields (5):")
    assert "fill" not in plain and "75%" not in plain
    nowhere = await _tool(p, p.org_a, monkeypatch, entity="")
    assert await _tool(p, p.org_a, monkeypatch, query="unit price") == nowhere
    assert nowhere.startswith("Refused:")


async def test_a_listed_org_gets_the_hits_through_the_tool(finds, app_engine, monkeypatch) -> None:
    p = finds
    out = await _tool(p, p.org_a, monkeypatch, query="total")
    assert out.splitlines()[:3] == [
        "Fields of the mirrored modules that match 'total' (2):",
        "- Products: Total Weight (key Total_Weight, number) [measure: sum, avg, min, "
        "max] filter: one exact number",
        "- Quotes: Grand Total (key Grand_Total, currency) [measure: sum, avg, min, "
        "max] filter: one exact number",
    ]
    out = await _tool(p, p.org_a, monkeypatch, entity="module:Products", query="unit price")
    assert "- Products: Unit Price (key Unit_Price, currency)" in out
    assert out.splitlines()[1].endswith("; fill 75%")
