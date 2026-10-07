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

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException

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


# Read at import time, before any fixture swaps the function.
from gateway.routes.email.transport.search import search_messages as _real_search_messages  # noqa: E402

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
        from gateway.main import app

        # ⚠️ Not `app.routes`. Since FastAPI 0.137 a router's routes are not
        # copied into its parent, so a mounted router's paths are not there.
        # `iter_route_contexts` is how `acb_auth.deps` reads them too.
        from fastapi.routing import iter_route_contexts

        paths = {getattr(c, "path", None) for c in iter_route_contexts(app.routes)}
        assert "/shell/search" in paths
        assert "/projects/search" in paths  # non-vacuity: the walk sees mounted routes
