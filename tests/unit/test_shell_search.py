"""NS-4a — GET /shell/search, the command bar's "Find" (navigation_shell.md §6.3).

The route reuses each app's OWN search function. Those functions have their
own suites, R8 ones included (`test_projects_search_r8.py`,
`test_email_search_scope.py`, `test_people_directory.py`). So this file fences
only what the shell adds, and each case is written so a wrong build fails it:

* **The app's feature gate, again** (one negative case per provider). The gate
  sits on each app's router, so a direct call skips it unless the provider
  checks. A member without the app gets no group, and the app's function is
  never called.
* **The member travels unchanged.** Each function gets THIS request's member,
  so its own visibility rules apply. Never a service principal.
* **One app at a time** (the pool budget), and a failing app is left out.
* **The email call names every parameter the route declares.** Otherwise a new
  `Query(...)` parameter would arrive as the `Query` object itself.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email.transport.search import search_messages as _real_search_messages
from gateway.routes.shell import search as shell


def member(*features: str) -> UserContext:
    return UserContext(
        email="member@customer.example",
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
    )


ALL = ("projects", "email", "people")


class Fakes:
    """Stand-ins for the three app functions. They record who called and when."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        self.active = 0
        self.max_active = 0
        self.fail: dict[str, BaseException] = {}

    async def _enter(self, name: str, user: object, kwargs: dict) -> None:
        self.calls.append((name, user, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        if name in self.fail:
            raise self.fail[name]

    async def tasks(self, **kw):
        await self._enter("tasks", kw["user"], kw)
        return {"rows": [{"id": "t-1", "title": "Ship the extruder", "project_name": "Hardware"}]}

    async def email(self, **kw):
        await self._enter("email", kw["user"], kw)
        return {"emails": [{"id": "m-1", "subject": "Invoice March",
                            "from_address": {"name": "Priya", "email": "p@x.test"}}], "total": 1}

    async def people(self, **kw):
        await self._enter("people", kw["user"], kw)
        return SimpleNamespace(rows=[{"id": "p-1", "name": "Priya Rao", "title": "Firmware lead"}])


@pytest.fixture()
def fakes(monkeypatch) -> Fakes:
    f = Fakes()
    import gateway.routes.email.transport.search as email_search
    import gateway.routes.people.directory as directory
    import gateway.routes.projects.search as projects_search

    monkeypatch.setattr(projects_search, "search_tasks", f.tasks)
    monkeypatch.setattr(email_search, "search_messages", f.email)
    monkeypatch.setattr(directory, "list_directory", f.people)
    return f


def run(q: str, user: UserContext, scope: str | None = None) -> dict:
    return asyncio.run(shell.shell_search(q=q, scope=scope, user=user))


class TestTheFeatureGate:
    @pytest.mark.parametrize(
        ("missing", "app"),
        [("projects", "tasks"), ("email", "email"), ("people", "people")],
    )
    def test_a_member_without_the_app_gets_no_group_and_no_call(self, fakes, missing, app):
        user = member(*[f for f in ALL if f != missing])
        answer = run("priya invoice", user)
        assert app not in [g["app"] for g in answer["groups"]]
        assert app not in [name for name, _, _ in fakes.calls]

    def test_a_member_with_no_app_gets_nothing(self, fakes):
        assert run("priya", member())["groups"] == []
        assert fakes.calls == []

    def test_a_signed_out_caller_is_refused(self, fakes):
        anon = UserContext(email=None, role=UserRole.EMPLOYEE)
        with pytest.raises(HTTPException) as e:
            run("priya", anon)
        assert e.value.status_code == 401


class TestTheMemberTravelsUnchanged:
    def test_each_app_function_gets_this_requests_member(self, fakes):
        user = member(*ALL)
        run("priya", user)
        assert len(fakes.calls) == 3
        assert all(u is user for _, u, _ in fakes.calls)


class TestOneAppAtATime:
    def test_the_providers_never_overlap(self, fakes):
        run("priya", member(*ALL))
        assert fakes.max_active == 1

    @pytest.mark.parametrize("error", [HTTPException(status_code=422, detail="too short"),
                                       RuntimeError("db down")])
    def test_a_failing_app_is_left_out_and_the_rest_answer(self, fakes, error):
        fakes.fail["email"] = error
        groups = [g["app"] for g in run("priya", member(*ALL))["groups"]]
        assert groups == ["tasks", "people"]

    def test_one_deadline_for_all_apps_not_one_each(self, fakes, monkeypatch):
        # Each fake takes 10 ms. A total budget of 15 ms leaves room for one
        # app, maybe two, and never all three.
        monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 0.065)

        async def slow(self_name, user, kwargs, _orig=fakes._enter):
            await asyncio.sleep(0.04)
            await _orig(self_name, user, kwargs)

        monkeypatch.setattr(fakes, "_enter", lambda n, u, k: slow(n, u, k))
        run("priya", member(*ALL))
        assert len(fakes.calls) < 3

    def test_a_slow_app_is_left_out(self, fakes, monkeypatch):
        monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 0.001)
        assert run("priya", member(*ALL))["groups"] == []


class TestTheAnswer:
    def test_plain_words_and_links(self, fakes):
        groups = {g["app"]: g for g in run("priya", member(*ALL))["groups"]}
        assert groups["tasks"]["items"] == [
            {"kind": "task", "title": "Ship the extruder", "hint": "Task · Hardware",
             "href": "/projects?task=t-1"}]
        assert groups["email"]["items"][0]["hint"] == "Email · from Priya"
        assert groups["email"]["items"][0]["href"] == "/email?email=m-1"
        assert groups["people"]["items"][0] == {
            "kind": "person", "title": "Priya Rao", "hint": "Person · Firmware lead",
            "href": "/people/p-1"}

    def test_the_app_the_member_is_in_comes_first(self, fakes):
        order = [g["app"] for g in run("priya", member(*ALL), scope="/email")["groups"]]
        assert order[0] == "email"

    def test_one_letter_asks_no_app(self, fakes):
        assert run(" p ", member(*ALL))["groups"] == []
        assert fakes.calls == []

    def test_the_words_are_trimmed_and_capped(self, fakes):
        answer = run("  priya    rao  " + "x" * 500, member(*ALL))
        assert answer["query"].startswith("priya rao ")
        assert len(answer["query"]) == 200


# ── The CRM provider (WS-53 CRM-X2, crm_platform.md §19.6) ──────────────────
#
# The CRM router has THREE gates: the kill switch, `feature:crm` and the
# access rule. The loop tests the feature. `_crm` must apply the other two,
# in the order of the router, before `search_records` runs.

CRM_ORG = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"


def crm_member(*features: str, org: str | None = CRM_ORG) -> UserContext:
    return UserContext(
        email="rep@customer.example",
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
        organization_id=org,
    )


class CrmFakes:
    """`search_records` and the access rule, recorded."""

    def __init__(self) -> None:
        self.searched: list[tuple[object, str, int]] = []
        self.access_checked: list[object] = []
        self.refuse = False
        self.fail: BaseException | None = None
        self.rows: list[tuple[str, dict]] = [
            ("deal", {"id": "d-1", "name": "Extruder order", "organization_name": "Zephyr Labs"}),
            ("lead", {"id": "l-1", "lead_name": "Ravi Kumar", "organization_name": "Bosch",
                      "email": "ravi@bosch.test"}),
            ("lead", {"id": "l-2", "lead_name": "Anu", "organization_name": None,
                      "email": "anu@x.test"}),
            ("contact", {"id": "c-1", "first_name": "Priya", "last_name": "Rao",
                         "email": "priya@zephyr.test"}),
            ("company", {"id": "o-1", "name": "Zephyr Labs", "industry": "Robotics"}),
        ]

    async def search_records(self, user, q, limit):
        self.searched.append((user, q, limit))
        if self.fail is not None:
            raise self.fail
        return list(self.rows)

    async def require_crm_access(self, user):
        self.access_checked.append(user)
        if self.refuse:
            raise HTTPException(status_code=403, detail="no access")


@pytest.fixture()
def crm(fakes, monkeypatch) -> CrmFakes:
    from acb_common import get_settings
    from gateway.routes.crm import access, records

    f = CrmFakes()
    monkeypatch.setattr(records, "search_records", f.search_records, raising=False)
    monkeypatch.setattr(access, "require_crm_access", f.require_crm_access)
    settings = get_settings()
    monkeypatch.setattr(settings, "crm_enabled", True, raising=False)
    monkeypatch.setattr(settings, "crm_orgs", CRM_ORG, raising=False)
    return f


def crm_group(answer: dict) -> dict | None:
    return next((g for g in answer["groups"] if g["app"] == "crm"), None)


class TestTheCrmProvider:
    def test_a_member_with_the_crm_gets_a_crm_group(self, crm):
        user = crm_member("crm")
        group = crm_group(run("zephyr", user))
        assert group is not None and group["label"] == "CRM"
        # The member travels unchanged, with the words and the bar's handful.
        assert crm.searched == [(user, "zephyr", shell.PER_APP)]

    def test_without_feature_crm_no_group_and_no_search(self, crm):
        answer = run("zephyr", crm_member("projects"))
        assert crm_group(answer) is None
        assert crm.searched == [] and crm.access_checked == []

    def test_an_org_outside_crm_orgs_gets_no_group(self, crm):
        answer = run("zephyr", crm_member("crm", org="0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002"))
        assert crm_group(answer) is None
        # The kill switch answers first, as on the router: no access read.
        assert crm.searched == [] and crm.access_checked == []

    def test_with_the_kill_switch_off_no_group(self, crm, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
        assert crm_group(run("zephyr", crm_member("crm"))) is None
        assert crm.searched == [] and crm.access_checked == []

    def test_a_member_with_no_organization_gets_no_group(self, crm, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "crm_orgs", "*", raising=False)
        assert crm_group(run("zephyr", crm_member("crm", org=None))) is None
        assert crm.searched == []

    def test_a_member_the_access_rule_refuses_gets_no_group(self, crm):
        crm.refuse = True
        user = crm_member("crm", "projects")
        answer = run("zephyr", user)
        assert crm_group(answer) is None
        assert crm.access_checked == [user]
        assert crm.searched == []
        # The other apps still answer.
        assert [g["app"] for g in answer["groups"]] == ["tasks"]

    def test_inside_the_crm_the_crm_group_comes_first(self, crm):
        order = [g["app"] for g in run("zephyr", crm_member("crm", *ALL), scope="/crm")["groups"]]
        assert order[0] == "crm"
        # Elsewhere it follows the three providers of before.
        order = [g["app"] for g in run("zephyr", crm_member("crm", *ALL))["groups"]]
        assert order == ["tasks", "email", "people", "crm"]

    @pytest.mark.parametrize("error", [RuntimeError("db down"), TimeoutError()])
    def test_a_failing_crm_leaves_the_other_groups(self, crm, error):
        crm.fail = error
        groups = [g["app"] for g in run("zephyr", crm_member("crm", *ALL))["groups"]]
        assert groups == ["tasks", "email", "people"]

    def test_a_slow_crm_is_left_out(self, crm, monkeypatch):
        from gateway.routes.crm import records

        async def slow(user, q, limit):
            await asyncio.sleep(0.2)
            return list(crm.rows)

        monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 0.05)
        monkeypatch.setattr(records, "search_records", slow, raising=False)
        groups = [g["app"] for g in run("zephyr", crm_member("crm", "projects"))["groups"]]
        assert groups == ["tasks"]

    def test_plain_words_and_links(self, crm):
        items = crm_group(run("zephyr", crm_member("crm")))["items"]
        assert items == [
            {"kind": "deal", "title": "Extruder order", "hint": "Deal · Zephyr Labs",
             "href": "/crm?tab=deals&deal=d-1"},
            {"kind": "lead", "title": "Ravi Kumar", "hint": "Lead · Bosch",
             "href": "/crm?tab=leads&lead=l-1"},
            # No company: the email says who it is.
            {"kind": "lead", "title": "Anu", "hint": "Lead · anu@x.test",
             "href": "/crm?tab=leads&lead=l-2"},
            {"kind": "contact", "title": "Priya Rao", "hint": "Contact · priya@zephyr.test",
             "href": "/crm?tab=contacts&contact=c-1"},
            {"kind": "company", "title": "Zephyr Labs", "hint": "Company · Robotics",
             "href": "/crm?tab=organizations&organization=o-1"},
        ]

    def test_a_record_with_nothing_to_add_says_only_its_kind(self, crm):
        crm.rows = [
            ("deal", {"id": "d-2", "name": "Bare", "organization_name": None}),
            ("contact", {"id": "c-2", "first_name": "Solo", "last_name": None, "email": None}),
            ("company", {"id": "o-2", "name": "Plain", "industry": ""}),
        ]
        items = crm_group(run("bare", crm_member("crm")))["items"]
        assert [i["hint"] for i in items] == ["Deal", "Contact", "Company"]
        assert [i["title"] for i in items] == ["Bare", "Solo", "Plain"]

    def test_no_id_reaches_the_eye(self, crm):
        for item in crm_group(run("zephyr", crm_member("crm")))["items"]:
            for word in ("d-1", "l-1", "l-2", "c-1", "o-1"):
                assert word not in item["title"] and word not in item["hint"]


class TestSearchRecordsStaysOffTheRouteContract:
    """§19.6 ⚠️ and §19.10: `search_records` builds no `ListParams` (its
    `Query(...)` defaults are the trap of the email call), and it reads by
    `last_activity_at` with no next activity (the 1.5 s budget)."""

    def test_it_builds_no_list_params(self):
        from gateway.routes.crm import records

        tree = ast.parse(textwrap.dedent(inspect.getsource(records.search_records)))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        # The docstring may name the trap. The code may not use it.
        assert "ListParams" not in names
        assert "_list" not in names
        assert "list_contract" in names  # non-vacuity: the walk sees the calls

    def test_each_read_sorts_by_last_activity_and_skips_the_next_activity(self, monkeypatch):
        from contextlib import asynccontextmanager

        from gateway.routes.crm import core, records

        contracts: list[dict] = []
        lists: list[dict] = []
        real_contract = core.list_contract

        def spy_contract(entity, **kw):
            contracts.append({"entity": entity.slug, **kw})
            return real_contract(entity, **kw)

        async def spy_run_list(db, entity, query, **kw):
            lists.append({"entity": entity.slug, **kw})
            return core.ListResponse(rows=[], total=0)

        @asynccontextmanager
        async def session():
            yield object()

        monkeypatch.setattr(records, "list_contract", spy_contract)
        monkeypatch.setattr(records, "run_list", spy_run_list)
        monkeypatch.setattr(records, "_tenant_session", session)
        asyncio.run(records.search_records(crm_member("crm"), "zephyr", 5))
        assert sorted(c["entity"] for c in contracts) == [
            "contacts", "deals", "leads", "organizations"]
        assert all(c["sort"] == "last_activity_at" and c["page_size"] == 5 for c in contracts)
        # The converted-lead rule of the Leads list.
        leads = next(c for c in contracts if c["entity"] == "leads")
        assert core.OPEN_LEAD_SQL in leads["extra_where"]
        assert len(lists) == 4 and all(entry.get("rows_only") is True for entry in lists)


# The REAL route signature, read at import time, before any fixture swaps it.

REAL_EMAIL_PARAMS = frozenset(inspect.signature(_real_search_messages).parameters)


class TestTheEmailCallStaysInStep:
    def test_every_parameter_of_the_route_is_named(self, fakes):
        run("invoice", member("email"))
        passed = set(next(kw for name, _, kw in fakes.calls if name == "email"))
        assert passed == REAL_EMAIL_PARAMS, (
            f"the route has {sorted(REAL_EMAIL_PARAMS - passed)} that the shell does not pass"
        )


class TestTheRouteIsMounted:
    def test_the_gateway_serves_shell_search(self, monkeypatch):
        # `main.py` mounts every router inside `try/except: pass`, so an import
        # error would hide the route. This finds it.
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
        # ⚠️ Not `app.routes`. Since FastAPI 0.137 a router's routes are not
        # copied into its parent, so a mounted router's paths are not there.
        # `iter_route_contexts` is how `acb_auth.deps` reads them too.
        from fastapi.routing import iter_route_contexts
        from gateway.main import app

        paths = {getattr(c, "path", None) for c in iter_route_contexts(app.routes)}
        assert "/shell/search" in paths
        assert "/projects/search" in paths  # non-vacuity: the walk sees mounted routes
