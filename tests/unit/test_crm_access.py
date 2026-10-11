"""Who may open the CRM, and the change to Native mode (WS-53 CRM-T5).

Spec: ``project-docs/specs/crm_platform.md`` §3.1, §4.2, §10, §13.3 (the
CRM-T5 row) and §16 Q3.

Three things are under test:

* ``routes/crm/access.py::require_crm_access``, the third router dependency.
  An admin always passes. ``everyone`` passes each ``feature:crm`` holder.
  ``groups`` passes a member of a listed group. No row reads as ``admins``.
* ``routes/crm/admin.py``: the six writes on stages and lost reasons need
  ``admin:access:manage`` (CR-10).
* ``routes/crm/settings.py``: ``GET`` and ``PATCH /crm/settings``. Only the
  change from no mode to ``native`` is open, and it seeds the pipeline.

Hermetic: the real ``/crm`` router on a bare FastAPI app, the DB seam is
``tests/unit/_crm_fakes.py``, and ``get_current_user`` is overridden with a
``UserContext`` that the test builds. ``tests/unit/test_crm_access_r8.py``
proves the same rules against a real Postgres (R8).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from gateway.routes import crm as crm_package
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import deal_contacts as crm_deal_contacts
from gateway.routes.crm import flags as crm_flags
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records
from gateway.routes.crm import seed as crm_seed
from gateway.routes.crm import settings as crm_settings

from tests.unit._crm_fakes import (
    CRM_TEST_ORG,
    FakeCrmDB,
    bind_db,
    crm_switch_on,
)
from tests.unit._routes import served_routes

ORG_A = CRM_TEST_ORG
ORG_B = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002"

ADMIN = "admin@crm-t5.test"
SALES = "sales@crm-t5.test"
OTHER = "other@crm-t5.test"

#: Done-when 2: the four entity lists.
LISTS = ("/crm/leads", "/crm/deals", "/crm/contacts", "/crm/organizations")

#: The six writes of CR-10, each with a body it would accept from an admin.
WRITES: list[tuple[str, str, dict | None]] = [
    ("POST", "/crm/statuses/deal", {"name": "Pilot"}),
    ("PATCH", "/crm/statuses/deal/{deal_status}", {"color": "blue"}),
    ("DELETE", "/crm/statuses/lead/{lead_status}", None),
    ("POST", "/crm/lost-reasons", {"label": "Timing"}),
    ("PATCH", "/crm/lost-reasons/{reason}", {"label": "Cost"}),
    ("DELETE", "/crm/lost-reasons/{reason}", None),
]


# ── Principals ───────────────────────────────────────────────────────────────

def principal(
    email: str, *perms: str, organization_id: str | None = ORG_A,
) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(list(perms)), organization_id=organization_id,
    )


def admin(org: str = ORG_A) -> UserContext:
    return principal(ADMIN, "feature:crm", "admin:access:manage", organization_id=org)


def member(email: str = SALES, org: str = ORG_A) -> UserContext:
    return principal(email, "feature:crm", organization_id=org)


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(
        monkeypatch, fake,
        (crm_core, crm_records, crm_pipeline, crm_activities, crm_admin,
         crm_deal_contacts, crm_access, crm_settings),
    )
    crm_switch_on(monkeypatch, "*")
    return fake


def seed_people(db: FakeCrmDB) -> None:
    """``sales@`` is in the group ``sales``. ``other@`` is in ``support``."""
    sales = db.seed("app_user", email=SALES.upper(), status="active")
    other = db.seed("app_user", email=OTHER, status="active")
    g_sales = db.seed("org_group", slug="sales")
    g_support = db.seed("org_group", slug="support")
    db.seed("org_group_member", group_id=g_sales.id, user_id=sales.id)
    db.seed("org_group_member", group_id=g_support.id, user_id=other.id)


def set_access(
    db: FakeCrmDB, access: str, groups: list[str] | None = None,
    org: str = ORG_A,
) -> None:
    db.seed(
        "crm_settings", organization_id=org, mode=None, access=access,
        access_groups=list(groups or []), updated_by=None, updated_at=None,
    )


def client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def list_codes(user: UserContext) -> dict[str, int]:
    c = client(user)
    return {path: c.get(path).status_code for path in LISTS}


def counts(db: FakeCrmDB, org: str) -> tuple[int, int, int]:
    def n(table: str) -> int:
        return sum(1 for r in db.rows(table) if r.get("organization_id") == org)
    return (n("crm_deal_statuses"), n("crm_lead_statuses"), n("crm_lost_reasons"))


# ── Done-when 1 · the six writes need admin:access:manage (CR-10) ───────────

def _send(c: TestClient, method: str, path: str, body: dict | None) -> int:
    return c.request(method, path, json=body).status_code


def _fill(db: FakeCrmDB) -> dict[str, str]:
    return {
        "deal_status": db.seed(
            "crm_deal_statuses", name="Qualification", type="open",
            probability=10,
        ).id,
        "lead_status": db.seed("crm_lead_statuses", name="Spare", type="open").id,
        "reason": db.seed("crm_lost_reasons", label="Price").id,
    }


def test_a_member_gets_403_on_post_statuses_deal_and_an_admin_201(
    db: FakeCrmDB,
) -> None:
    set_access(db, "everyone")
    assert client(member()).post(
        "/crm/statuses/deal", json={"name": "Pilot"},
    ).status_code == 403
    assert client(admin()).post(
        "/crm/statuses/deal", json={"name": "Pilot"},
    ).status_code == 201


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_each_of_the_six_writes_refuses_a_member(
    db: FakeCrmDB, method: str, path: str, body: dict | None,
) -> None:
    """``access = everyone`` opens the CRM to the member, and still not the
    writes that manage the pipeline itself."""
    set_access(db, "everyone")
    ids = _fill(db)
    before = {t: list(db.rows(t)) for t in (
        "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons")}
    response = client(member()).request(method, path.format(**ids), json=body)
    assert response.status_code == 403, response.text
    assert "admin:access:manage" in response.text
    after = {t: list(db.rows(t)) for t in before}
    assert after == before


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_each_of_the_six_writes_serves_an_admin(
    db: FakeCrmDB, method: str, path: str, body: dict | None,
) -> None:
    ids = _fill(db)
    code = _send(client(admin()), method, path.format(**ids), body)
    assert code in (200, 201), code


def test_a_member_still_reads_the_stages_and_lost_reasons(db: FakeCrmDB) -> None:
    """The board and the record sheet read both lists, so the reads stay
    open to each member that the access rule admits."""
    set_access(db, "everyone")
    _fill(db)
    c = client(member())
    assert c.get("/crm/statuses/deal").status_code == 200
    assert c.get("/crm/statuses/lead").status_code == 200
    assert c.get("/crm/lost-reasons").status_code == 200


# ── Done-when 2 · access = admins, or no row ────────────────────────────────

def test_with_no_row_a_member_gets_403_on_each_list(db: FakeCrmDB) -> None:
    assert list_codes(member()) == {path: 403 for path in LISTS}


def test_with_access_admins_a_member_gets_403_on_each_list(db: FakeCrmDB) -> None:
    set_access(db, "admins")
    assert list_codes(member()) == {path: 403 for path in LISTS}


@pytest.mark.parametrize("access", [None, "admins", "groups", "everyone"])
def test_an_admin_gets_200_on_each_list_whatever_the_rule(
    db: FakeCrmDB, access: str | None,
) -> None:
    if access is not None:
        set_access(db, access, ["group:nobody"] if access == "groups" else [])
    assert list_codes(admin()) == {path: 200 for path in LISTS}


def test_an_admin_passes_without_a_database_read(db: FakeCrmDB) -> None:
    """The admin check is first. No settings read, no session."""
    list_codes(admin())
    assert not db.statements_touching("crm_settings")


def test_another_orgs_rule_does_not_open_this_org(db: FakeCrmDB) -> None:
    set_access(db, "everyone", org=ORG_B)
    assert list_codes(member()) == {path: 403 for path in LISTS}


# ── Done-when 3 · access = groups ───────────────────────────────────────────

def test_with_groups_a_member_of_sales_gets_200(db: FakeCrmDB) -> None:
    seed_people(db)
    set_access(db, "groups", ["group:sales"])
    assert list_codes(member(SALES)) == {path: 200 for path in LISTS}


def test_with_groups_a_member_outside_sales_gets_403(db: FakeCrmDB) -> None:
    seed_people(db)
    set_access(db, "groups", ["group:sales"])
    assert list_codes(member(OTHER)) == {path: 403 for path in LISTS}


def test_with_groups_an_inactive_member_of_sales_gets_403(db: FakeCrmDB) -> None:
    seed_people(db)
    for row in db.rows("app_user"):
        row["status"] = "suspended"
    set_access(db, "groups", ["group:sales"])
    assert list_codes(member(SALES)) == {path: 403 for path in LISTS}


def test_with_groups_and_an_empty_list_a_member_gets_403(db: FakeCrmDB) -> None:
    seed_people(db)
    set_access(db, "groups", [])
    assert list_codes(member(SALES)) == {path: 403 for path in LISTS}


def test_the_group_read_is_the_projects_read_model() -> None:
    """D12: one answer to "which groups is this member in". Imported, never
    copied."""
    from gateway.routes.projects import core as projects_core

    assert crm_access._MY_GROUPS_SQL is projects_core._MY_GROUPS_SQL
    source = inspect.getsource(crm_access)
    assert "org_group_member" not in source.split('"""', 2)[2]


# ── Done-when 4 · access = everyone ─────────────────────────────────────────

def test_with_everyone_each_feature_holder_gets_200(db: FakeCrmDB) -> None:
    set_access(db, "everyone")
    for email in (SALES, OTHER):
        assert list_codes(member(email)) == {path: 200 for path in LISTS}


def test_with_everyone_a_member_without_the_feature_gets_403(db: FakeCrmDB) -> None:
    set_access(db, "everyone")
    outsider = principal(SALES, "feature:email")
    assert list_codes(outsider) == {path: 403 for path in LISTS}


# ── Done-when 5 · PATCH mode native seeds the org once ──────────────────────

def test_native_seeds_6_deal_5_lead_6_lost_for_the_org(db: FakeCrmDB) -> None:
    db.seed("crm_deal_statuses", organization_id=ORG_B, name="Theirs", type="open")
    c = client(admin(ORG_A))

    first = c.patch("/crm/settings", json={"mode": "native"})
    assert first.status_code == 200, first.text
    assert first.json()["mode"] == "native"
    assert counts(db, ORG_A) == (6, 5, 6)

    second = c.patch("/crm/settings", json={"mode": "native"})
    assert second.status_code == 200, second.text
    assert counts(db, ORG_A) == (6, 5, 6)
    assert counts(db, ORG_B) == (1, 0, 0)


def test_an_org_with_its_own_stages_keeps_them(db: FakeCrmDB) -> None:
    """The review P2: the conflict key is the NAME, so seeding over an org's
    own (renamed) pipeline brings the old names back, with a second default
    lane. A table that already has a row for the org gets nothing."""
    db.seed("crm_deal_statuses", organization_id=ORG_A, name="Our lane",
            type="open", is_default=True)
    c = client(admin(ORG_A))

    response = c.patch("/crm/settings", json={"mode": "native"})

    assert response.status_code == 200, response.text
    assert counts(db, ORG_A) == (1, 5, 6)


def test_the_seed_runs_once_on_the_change_and_not_on_a_repeat(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _spy(session: Any, organization_id: str) -> dict[str, int]:
        calls.append(organization_id)
        return {}

    monkeypatch.setattr(crm_settings, "seed_native_pipeline", _spy)
    c = client(admin())
    c.patch("/crm/settings", json={"mode": "native"})
    c.patch("/crm/settings", json={"mode": "native"})
    c.patch("/crm/settings", json={"access": "everyone"})
    assert calls == [ORG_A]


def test_the_seed_rows_are_the_listed_ones(db: FakeCrmDB) -> None:
    client(admin()).patch("/crm/settings", json={"mode": "native"})
    deal = sorted(
        (r["name"], r["position"], r["type"], r["is_default"], r["probability"])
        for r in db.rows("crm_deal_statuses")
    )
    assert deal == sorted(
        (n, p, t, d, pr) for n, _c, p, t, d, pr in crm_seed.DEAL_STATUSES
    )
    assert sorted(r["label"] for r in db.rows("crm_lost_reasons")) == sorted(
        label for label, _ in crm_seed.LOST_REASONS
    )


def test_no_read_seeds_anything(db: FakeCrmDB) -> None:
    """The seed rule: only the write that sets the mode seeds."""
    c = client(admin())
    for path in (*LISTS, "/crm/settings", "/crm/statuses/deal", "/crm/lost-reasons"):
        assert c.get(path).status_code == 200, path
    assert not [s for s in db.statements if s.startswith("INSERT")]


# ── Done-when 6 · every other change of mode is 422 ─────────────────────────

@pytest.mark.parametrize("mode", ["mirror", "zoho", ""])
def test_a_mode_other_than_native_from_none_is_422(db: FakeCrmDB, mode: str) -> None:
    response = client(admin()).patch("/crm/settings", json={"mode": mode})
    assert response.status_code == 422, response.text
    assert counts(db, ORG_A) == (0, 0, 0)
    assert not db.rows("crm_settings")


@pytest.mark.parametrize("mode", ["mirror", None])
def test_native_cannot_change_again_in_t5(db: FakeCrmDB, mode: str | None) -> None:
    c = client(admin())
    assert c.patch("/crm/settings", json={"mode": "native"}).status_code == 200
    assert c.patch("/crm/settings", json={"mode": mode}).status_code == 422
    assert c.get("/crm/settings").json()["mode"] == "native"


# ── The settings routes: admin only, validated, no tenant in the body ───────

def test_get_settings_with_no_row_reads_the_defaults(db: FakeCrmDB) -> None:
    body = client(admin()).get("/crm/settings").json()
    assert body["mode"] is None
    assert body["access"] == "admins"
    assert body["access_groups"] == []
    assert "organization_id" not in body


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_a_member_cannot_read_or_change_the_settings(
    db: FakeCrmDB, method: str,
) -> None:
    set_access(db, "everyone")
    response = client(member()).request(
        method, "/crm/settings", json={"access": "everyone"},
    )
    assert response.status_code == 403
    assert "admin:access:manage" in response.text


def test_access_groups_are_folded_and_deduplicated(db: FakeCrmDB) -> None:
    response = client(admin()).patch("/crm/settings", json={
        "access": "groups",
        "access_groups": ["group:Sales", "group:sales ", "group:ops"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["access_groups"] == ["group:sales", "group:ops"]


@pytest.mark.parametrize(
    "subject", ["org", "sales@crm-t5.test", "sales", "group:", ""],
)
def test_an_access_group_must_be_a_group_subject(
    db: FakeCrmDB, subject: str,
) -> None:
    response = client(admin()).patch(
        "/crm/settings", json={"access": "groups", "access_groups": [subject]},
    )
    assert response.status_code == 422, response.text
    assert not db.rows("crm_settings")


@pytest.mark.parametrize("value", ["all", "members", None])
def test_an_unknown_access_value_is_422(db: FakeCrmDB, value: str | None) -> None:
    response = client(admin()).patch("/crm/settings", json={"access": value})
    assert response.status_code == 422


def test_a_body_organization_id_does_not_pick_the_row(db: FakeCrmDB) -> None:
    response = client(admin(ORG_A)).patch("/crm/settings", json={
        "access": "everyone", "organization_id": ORG_B,
    })
    assert response.status_code == 200
    assert [r["organization_id"] for r in db.rows("crm_settings")] == [ORG_A]


def test_the_groups_validator_is_the_projects_one() -> None:
    from gateway.routes.projects import core as projects_core

    assert crm_settings.validate_grant_subject is projects_core.validate_grant_subject


def test_an_admin_who_picks_groups_keeps_the_settings_page(db: FakeCrmDB) -> None:
    c = client(admin())
    c.patch("/crm/settings", json={
        "access": "groups", "access_groups": ["group:nobody"],
    })
    assert c.get("/crm/settings").status_code == 200
    assert list_codes(admin()) == {path: 200 for path in LISTS}


# ── Wiring fences (user_management_contract.md R4, R7) ──────────────────────

def test_the_access_rule_runs_third() -> None:
    deps = [getattr(d, "dependency", None) for d in crm_core.router.dependencies]
    assert deps[0] is crm_flags.require_crm_enabled
    assert deps[2] is crm_access.require_crm_access
    assert len(deps) == 3


def test_the_dependency_takes_only_the_user() -> None:
    params = inspect.signature(crm_access.require_crm_access).parameters
    assert list(params) == ["user"]


def _calls_in(dependant: Any) -> set[Any]:
    found: set[Any] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        if getattr(node, "call", None) is not None:
            found.add(node.call)
        stack.extend(getattr(node, "dependencies", []) or [])
    return found


def _route_calls(route: Any) -> set[Any]:
    from fastapi.dependencies.utils import get_dependant

    calls: set[Any] = set()
    for dep in getattr(route, "dependencies", []) or []:
        fn = getattr(dep, "dependency", None)
        calls.add(fn)
        if fn is not None:
            calls |= _calls_in(get_dependant(path=route.path, call=fn))
    endpoint = getattr(route, "endpoint", None)
    if endpoint is not None:
        calls |= _calls_in(get_dependant(path=route.path, call=endpoint))
    return calls


def test_every_crm_route_of_the_gateway_carries_the_access_rule() -> None:
    import gateway.main as main

    routes = [
        r for r in served_routes(main.app.routes)
        if str(getattr(r, "path", "")).startswith("/crm")
    ]
    assert len(routes) >= 20
    missing = [
        (sorted(r.methods or [])[:1], r.path) for r in routes
        if crm_access.require_crm_access not in _route_calls(r)
    ]
    assert not missing, f"/crm routes without require_crm_access: {missing}"


def _admin_checks(method: str, path: str) -> list[Any]:
    route = next(
        r for r in crm_core.router.routes
        if getattr(r, "path", "") == path and method in (r.methods or set())
    )
    return [
        d.dependency for d in route.dependencies
        if "require_permission" in getattr(d.dependency, "__qualname__", "")
    ]


_FLOORED = [
    ("POST", "/crm/statuses/{kind}"),
    ("PATCH", "/crm/statuses/{kind}/{status_id}"),
    ("DELETE", "/crm/statuses/{kind}/{status_id}"),
    ("POST", "/crm/lost-reasons"),
    ("PATCH", "/crm/lost-reasons/{reason_id}"),
    ("DELETE", "/crm/lost-reasons/{reason_id}"),
    ("GET", "/crm/settings"),
    ("PATCH", "/crm/settings"),
]


@pytest.mark.parametrize(("method", "path"), _FLOORED)
async def test_each_managing_route_declares_the_admin_floor(
    method: str, path: str,
) -> None:
    checks = _admin_checks(method, path)
    assert checks, f"{method} {path} declares no permission floor"
    for check in checks:
        with pytest.raises(Exception) as exc:
            await check(user=member())
        assert getattr(exc.value, "status_code", None) == 403
        assert "admin:access:manage" in str(getattr(exc.value, "detail", ""))
        assert await check(user=admin()) is not None


# ── The agent reaches /crm as the member, so the rule binds it too ──────────

async def test_the_agent_relays_the_403_of_a_blocked_member(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§10 item 8. ``crm-assistant`` calls ``/crm`` with the member's
    ``X-User-Email``. The gateway resolves that header to the member, so a
    member that the access rule blocks gets the 403 through the agent too,
    and the agent shows no row."""
    import httpx

    from tests.unit._crm_agent_fakes import FakeClient, fake_gateway, load_agent_module

    db.seed("crm_leads", lead_name="Ada Lovelace", first_name="Ada", last_name="Lovelace",
            email="ada@acme.test", status_id=None)
    seed_people(db)
    set_access(db, "groups", ["group:sales"])
    people = {SALES: member(SALES), OTHER: member(OTHER)}

    def _resolve(request: Request) -> UserContext:
        # What the gateway does for an internal bearer: the header names the
        # member, and the member's own access decides.
        return people[request.headers["X-User-Email"].lower()]

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _resolve

    class _Gateway(FakeClient):
        async def request(self, method: str, url: str, **kwargs: Any) -> Any:
            self._calls.append({"method": method, "url": url})
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://gateway.test",
            ) as inner:
                path = "/" + url.split("/", 3)[-1]
                return await inner.request(method, path, **kwargs)

    agent = load_agent_module()
    calls = fake_gateway(
        agent, monkeypatch, None, user=OTHER, client_class=_Gateway,
    )
    with pytest.raises(RuntimeError) as exc:
        await agent.search_crm("ada", entity="leads")
    assert "403" in str(exc.value)
    assert "Ada" not in str(exc.value)
    assert len(calls) == 1

    calls = fake_gateway(
        agent, monkeypatch, None, user=SALES, client_class=_Gateway,
    )
    answer = await agent.search_crm("ada", entity="leads")
    assert "Ada" in answer
