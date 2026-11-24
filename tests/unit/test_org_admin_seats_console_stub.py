"""PAR-4d (task 3936): the seat tools of ``org-admin`` against a stub Console.

Spec: ``project-docs/specs/chat_parity.md`` section 6.3, rung 3. The owner
decided on 2026-11-24 (answer ca808c74 on task 3107) that the seats prove
their isolation by a stub Customer Console and the named gate, not by R8. The
Console owns the seat org, so no tenant database holds it.

**What runs for real.** The chat tools (``read_seats``, ``set_seat``), the REAL
gateway seat router (``gateway/routes/seats.py``) mounted as ``gateway/main.py``
mounts it, and the ONE Console client of ``acb_auth.console_resolve`` with its
real ``is_wired`` and its real deployment-key hop. The tool reaches the router
through ``httpx.ASGITransport``.

**What is a stub.** The Console (``_StubConsole``), behind an
``httpx.MockTransport``, and ``GET /auth/me`` of the tenant plane. The stub
Console decides the org the way the real one does (``customer_console/main.py``
``_admin_scheme_for_deployment``): the deployment key names the box, and the
org is the one org on that box where the ``actor_email`` is a member. No org
is ever read from the body, and a body that names one is a 400. The actor must
be an active ``owner`` or ``admin`` of that org (403), and the target of a
write must be a member of it (404).

**Acceptance.**

a. Ada (admin of org A) reads the seats of org A only. The Console call
   carries the deployment key, ``actor_email`` = Ada, and no org.
b. Ada gives Mel a seat after ONE card that names the plan and the cap. The
   body of the tool names no org and no actor. The Console sees Ada as the
   actor, and org B does not change.
c. Zed (admin of org B) cannot reach a seat of org A: the tool gives plain
   words and no card, and the route call gets the Console's 404. Org A does
   not change.
d. Mel, who holds ``billing:purchase`` in the tenant plane but is no admin of
   the subscription, gets the Console's 403 as plain words, and no card.
e. A Console 409 at the cap and a 404 after the card come back as plain
   words, and nothing changes. Nothing buys.
f. A body that names an org gets the 400 of the gateway, and the Console sees
   no call (R11).
g. A box with no Console answers 503, and the tools say that seats need the
   Customer Console. The Console sees no call.

No Postgres: nothing here opens a session. Run::

    uv run pytest tests/unit/test_org_admin_seats_console_stub.py -q -p no:randomly
"""
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gateway.routes.seats", reason="gateway not installed")

import httpx
from acb_auth import get_current_user
from acb_auth.deps import require_authenticated
from acb_auth.roles import UserContext, UserRole
from acb_skills import ask_tools
from acb_skills import org_admin_more_tools as om
from acb_skills import org_admin_tools as oa
from fastapi import FastAPI, Request
from gateway.routes import billing as billing_routes
from gateway.routes import seats as seat_routes

CONSOLE_URL = "https://console.invalid"
#: The key of this box. Fake: no real Console ever sees it.
BOX_KEY = "cc_depl_stub_box_one_notarealsecret"

ADA = "ada@org-a.example.test"
MEL = "mel@org-a.example.test"
KIM = "kim@org-a.example.test"
ZED = "zed@org-b.example.test"
BO = "bo@org-b.example.test"
#: A member of both orgs on this box. The Console will not guess between them.
DUO = "duo@both.example.test"

_REAL_CLIENT = httpx.AsyncClient


class _StubConsole:
    """The deployment-key ``seat_admin`` door, as the Console decides it."""

    def __init__(self) -> None:
        self.boxes = {BOX_KEY: ("org-a", "org-b")}
        self.members: dict[tuple[str, str], tuple[str, str]] = {
            ("org-a", ADA): ("owner", "active"), ("org-a", MEL): ("member", "active"),
            ("org-a", KIM): ("member", "active"), ("org-a", DUO): ("admin", "active"),
            ("org-b", ZED): ("admin", "active"), ("org-b", BO): ("member", "active"),
            ("org-b", DUO): ("admin", "active"),
        }
        self.bought = {("org-a", "team"): 3, ("org-b", "team"): 2}
        self.held: dict[tuple[str, str], set[str]] = {
            ("org-a", "team"): {ADA}, ("org-b", "team"): {ZED}}
        self.calls: list[dict[str, Any]] = []
        #: A refusal that the Console gives to the next write of a path, after
        #: it derives the org (a cap or a member gone, while the card was open).
        self.refuse: dict[str, tuple[int, Any]] = {}

    def state(self) -> dict[tuple[str, str], frozenset[str]]:
        return {k: frozenset(v) for k, v in self.held.items()}

    def _grid(self, org: str) -> list[dict[str, Any]]:
        out = []
        for (o, plan), bought in sorted(self.bought.items()):
            if o == org:
                used = len(self.held.get((o, plan), set()))
                out.append({"plan_slug": plan, "purchased": bought, "assigned": used,
                            "available": max(bought - used, 0),
                            "oversubscribed": used > bought})
        return out

    def _roster(self, org: str) -> list[dict[str, Any]]:
        return [{"email": e, "role": role, "status": status,
                 "seats": sorted(p for (o, p), who in self.held.items() if o == org and e in who)}
                for (o, e), (role, status) in sorted(self.members.items()) if o == org]

    def _org(self, key: str, body: dict[str, Any],
             admin: bool = True) -> tuple[str | None, httpx.Response | None]:
        """The org of the actor on this box, or the Console's refusal. The
        seat doors need an active owner or admin. The billing read of the
        seat counts needs an active member (``_billing_member_context``)."""
        if "org_slug" in body:
            return None, httpx.Response(400, json={"detail": (
                "a deployment key may not name an organization; the org is derived from "
                "membership and placement")})
        actor = body.get("actor_email")
        if not actor:
            return None, httpx.Response(400, json={
                "detail": "actor_email is required under the deployment-key scheme"})
        orgs = [o for o in self.boxes[key] if (o, actor) in self.members]
        if not orgs:
            return None, httpx.Response(403, json={
                "detail": "the acting member is not an admin on this deployment"})
        if len(orgs) > 1:
            return None, httpx.Response(409, json={"detail": (
                "the acting member belongs to more than one organization on this "
                "deployment; the organization cannot be inferred")})
        role, status = self.members[(orgs[0], actor)]
        if (admin and role not in ("owner", "admin")) or status != "active":
            return None, httpx.Response(403, json={
                "detail": "the acting member is not an active admin of this organization"})
        return orgs[0], None

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append({"path": path, "auth": request.headers.get("authorization"),
                           "body": body})
        key = (request.headers.get("authorization") or "").removeprefix("Bearer ")
        if key not in self.boxes:
            return httpx.Response(401, json={"detail": "invalid deployment key"})
        org, refused = self._org(key, body, admin=path != "/registry/billing/seats")
        if refused is not None or org is None:
            return refused or httpx.Response(500)
        if path == "/registry/billing/seats":
            return httpx.Response(200, json={"plans": self._grid(org)})
        if path == "/registry/seats/overview":
            return httpx.Response(200, json={"plans": self._grid(org),
                                             "members": self._roster(org)})
        if path in self.refuse:
            status, detail = self.refuse.pop(path)
            return httpx.Response(status, json=detail)
        target, plan = body.get("member_email", ""), body.get("plan_slug", "")
        if (org, target) not in self.members:
            return httpx.Response(404, json={"detail": "no such member in this organization"})
        seats = self.held.setdefault((org, plan), set())
        if path == "/registry/seats/release":
            had = target in seats
            seats.discard(target)
            return httpx.Response(200, json={"released": had, "plan_slug": plan})
        if path != "/registry/seats":
            return httpx.Response(404, json={"detail": "Not Found"})
        bought = self.bought.get((org, plan), 0)
        if target not in seats and len(seats) >= bought:
            return httpx.Response(409, json={"detail": {"reason": "at_capacity", "buy_more": {
                "plan_slug": plan, "purchased": bought, "assigned": len(seats),
                "additional_seats_required": 1}}})
        seats.add(target)
        return httpx.Response(200, json={"assigned": True, "plan_slug": plan})


class _Card:
    def __init__(self) -> None:
        self.answer: Any = True
        self.shown: list[dict[str, Any]] = []

    async def __call__(self, title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        self.shown.append({"title": title, "detail": detail, "context": context, **kw})
        return self.answer


class _World:
    """The run member, the tenant plane's rights of each person, and the
    requests that the tool sent to the gateway."""

    def __init__(self, console: _StubConsole) -> None:
        self.console = console
        self.member = ADA
        self.rights = {ADA: [om.SEATS_RIGHT], ZED: [om.SEATS_RIGHT], MEL: [om.SEATS_RIGHT],
                       DUO: [om.SEATS_RIGHT], BO: []}
        self.sent: list[tuple[str, str, str | None, Any]] = []
        self.card = _Card()

    def app(self) -> FastAPI:
        """The real seat and billing routers, mounted as ``gateway/main.py``
        mounts them, and a stub of ``GET /auth/me``. The caller is the person
        of ``X-User-Email``."""
        app = FastAPI(dependencies=[require_authenticated(public=())])
        app.include_router(seat_routes.router)
        app.include_router(billing_routes.router)

        @app.get("/auth/me")
        async def me(request: Request) -> dict[str, Any]:
            return {"capabilities": self.rights.get(request.headers.get("x-user-email", ""), [])}

        def who(request: Request) -> UserContext:
            return UserContext(email=request.headers.get("x-user-email") or None,
                               role=UserRole.EMPLOYEE)

        app.dependency_overrides[get_current_user] = who
        return app

    async def route(self, who: str, path: str, body: Any) -> httpx.Response:
        """The gateway seat route directly, as *who*: the route and the Console alone."""
        async with _REAL_CLIENT(transport=httpx.ASGITransport(app=self.app()),
                                base_url="http://gateway.test") as client:
            return await client.post(path, json=body, headers={"X-User-Email": who})


def _settings(monkeypatch, wired: bool) -> None:
    from acb_common.settings import get_settings

    if wired:
        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
        monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", BOX_KEY)
    else:
        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    get_settings.cache_clear()


def _build(monkeypatch, wired: bool) -> _World:
    console = _StubConsole()
    w = _World(console)
    _settings(monkeypatch, wired)
    monkeypatch.setattr(
        "acb_auth.console_resolve._new_http_client",
        lambda timeout=None: _REAL_CLIENT(transport=httpx.MockTransport(console.handle)))
    gateway = httpx.ASGITransport(app=w.app())

    async def record(request: httpx.Request) -> None:
        body = json.loads(request.content) if request.content else None
        w.sent.append((request.method, request.url.path, request.headers.get("x-user-email"),
                       body))

    monkeypatch.setattr(oa.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(
        transport=gateway, event_hooks={"request": [record]}, **kw))
    monkeypatch.setattr(oa, "_current_user_email", lambda: w.member)
    monkeypatch.setattr(ask_tools, "request_confirmation", w.card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return w


@pytest.fixture
def world(monkeypatch):
    w = _build(monkeypatch, wired=True)
    yield w
    from acb_common.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def unwired(monkeypatch):
    w = _build(monkeypatch, wired=False)
    yield w
    from acb_common.settings import get_settings

    get_settings.cache_clear()


def _bodies(w: _World) -> list[dict[str, Any]]:
    return [c["body"] for c in w.console.calls]


# ── a. The read: org A only, the deployment key, the run's member ────────────


async def test_an_admin_reads_the_seats_of_her_own_org_only(world) -> None:
    """Mutation: the stub derives the org from a body field, or the client
    sends ``org_slug`` -> the call is refused or the roster mixes -> red."""
    out = await om.read_seats()
    assert out["ok"] is True and out["caps"] == ["team: 1 of 3 seats in use"], out
    assert ADA in out["rows"] and MEL in out["rows"] and KIM in out["rows"]
    assert ZED not in out["rows"] and BO not in out["rows"]
    (call,) = world.console.calls
    assert call["path"] == "/registry/seats/overview"
    assert call["auth"] == f"Bearer {BOX_KEY}" and call["body"] == {"actor_email": ADA}
    assert world.sent == [("GET", om.SEATS_OVERVIEW_PATH, ADA, None)]
    assert world.card.shown == []


async def test_an_admin_of_org_b_reads_org_b_only(world) -> None:
    world.member = ZED
    out = await om.read_seats()
    assert out["ok"] is True and out["caps"] == ["team: 1 of 2 seats in use"]
    assert ZED in out["rows"] and BO in out["rows"] and ADA not in out["rows"]
    assert world.console.calls[0]["body"] == {"actor_email": ZED}


# ── b. The assign: one card, no org and no actor in the body ─────────────────


async def test_an_admin_gives_a_seat_after_one_card_and_names_no_org(world) -> None:
    """Mutation: the tool puts ``actor_email`` or ``org_slug`` in the body ->
    the gateway answers 400 -> red. Mutation: send before the card -> red."""
    before_b = {k: v for k, v in world.console.state().items() if k[0] == "org-b"}
    out = await om.set_seat(MEL, "assign")
    assert out["ok"] is True and out["plan"] == "team", out
    (shown,) = world.card.shown
    assert shown["title"] == f"Give {MEL} a seat of team?"
    assert "After this: uses 2 of 3 seats of the plan team. Now: 1 of 3." in shown["context"]
    (write,) = [s for s in world.sent if s[0] == "POST"]
    assert write == ("POST", om.SEAT_ASSIGN_PATH, ADA,
                     {"member_email": MEL, "plan_slug": "team", "source": "alacarte"})
    (assign,) = [c for c in world.console.calls if c["path"] == "/registry/seats"]
    assert assign["body"] == {"member_email": MEL, "plan_slug": "team", "actor_email": ADA,
                              "source": "alacarte"}
    assert assign["auth"] == f"Bearer {BOX_KEY}"
    assert world.console.held[("org-a", "team")] == {ADA, MEL}
    assert {k: v for k, v in world.console.state().items() if k[0] == "org-b"} == before_b


async def test_an_admin_frees_a_seat_after_one_card(world) -> None:
    world.console.held[("org-a", "team")].add(KIM)
    out = await om.set_seat(KIM, "release")
    assert out["ok"] is True and "is free" in out["note"], out
    assert len(world.card.shown) == 1
    assert "uses 1 of 3 seats of the plan team" in world.card.shown[0]["context"]
    (release,) = [c for c in world.console.calls if c["path"] == "/registry/seats/release"]
    assert release["body"] == {"member_email": KIM, "plan_slug": "team", "actor_email": ADA}
    assert world.console.held[("org-a", "team")] == {ADA}


async def test_every_console_call_names_the_runs_member_and_no_org(world) -> None:
    """The actor is the member of the run, never an argument. Mutation: the
    route takes the actor from the body -> red."""
    await om.read_seats()
    await om.set_seat(MEL, "assign")
    world.member = ZED
    await om.set_seat(BO, "assign")
    assert world.console.calls
    for call in world.console.calls:
        assert "org_slug" not in call["body"] and "org" not in call["body"]
    actors = [c["body"]["actor_email"] for c in world.console.calls]
    assert actors == [ADA, ADA, ADA, ADA, ZED, ZED, ZED]
    for method, _path, who, body in world.sent:
        assert who in (ADA, ZED)
        assert not {"org", "org_slug", "actor_email", "email"} & set(body or {}), method


# ── c. Org B cannot reach a seat of org A ────────────────────────────────────


async def test_an_admin_of_org_b_cannot_give_a_seat_of_org_a(world) -> None:
    """Mutation: the stub reads the org from the target -> Mel gets a seat -> red."""
    before = world.console.state()
    world.member = ZED
    out = await om.set_seat(MEL, "assign")
    assert out["ok"] is False and out["status"] == 404, out
    assert f"has no member {MEL}" in out["error"] and "Nothing changed" in out["error"]
    assert world.card.shown == [] and world.console.state() == before
    assert [c["path"] for c in world.console.calls] == ["/registry/seats/overview"]


async def test_the_route_gives_org_b_the_consoles_refusal_for_a_seat_of_org_a(world) -> None:
    """The check of the route and the Console alone, with no tool before it."""
    before = world.console.state()
    for path in (om.SEAT_ASSIGN_PATH, om.SEAT_RELEASE_PATH):
        resp = await world.route(ZED, path, {"member_email": ADA, "plan_slug": "team"})
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "no such member in this organization"
    assert world.console.state() == before
    assert {c["body"]["actor_email"] for c in world.console.calls} == {ZED}


# ── d. A member who is no admin of the subscription ──────────────────────────


@pytest.mark.parametrize("act", ["assign", "release"])
async def test_a_member_who_is_no_seat_admin_gets_the_consoles_403_as_words(world,
                                                                         act) -> None:
    before = world.console.state()
    world.member = MEL
    out = await om.set_seat(KIM, act)
    assert out["ok"] is False and out["status"] == 403, out
    assert "does not know you as an active owner or admin" in out["error"]
    assert "nothing changed" in out["error"]
    assert world.card.shown == [] and world.console.state() == before


async def test_a_member_who_is_no_seat_admin_reads_the_counts_and_no_roster(world) -> None:
    """The 403 of the overview. The counts come from the real billing route
    (``GET /billing/seats``), for org A only, and name nobody."""
    world.member = MEL
    out = await om.read_seats()
    assert out["ok"] is True and out["caps"] == ["team: 1 of 3 seats in use"], out
    assert "rows" not in out and ADA not in json.dumps(out)
    assert [s[1] for s in world.sent] == [om.SEATS_OVERVIEW_PATH, om.SEAT_COUNTS_PATH]
    assert [c["path"] for c in world.console.calls] == ["/registry/seats/overview",
                                                         "/registry/billing/seats"]
    assert [c["body"] for c in world.console.calls] == [{"actor_email": MEL}] * 2


async def test_a_member_without_the_tenant_right_reaches_no_console(world) -> None:
    world.member = BO
    out = await om.set_seat(BO, "assign")
    assert out["ok"] is False and out["status"] == 403 and om.SEATS_RIGHT in out["error"]
    assert world.console.calls == [] and world.card.shown == []


async def test_a_member_of_two_orgs_gets_plain_words(world) -> None:
    world.member = DUO
    out = await om.set_seat(MEL, "assign")
    assert out["ok"] is False and out["status"] == 409 and "more than one" in out["error"]
    assert world.card.shown == []


# ── e. A Console refusal after the card ──────────────────────────────────────


async def test_the_cap_before_the_card_names_the_billing_page(world) -> None:
    world.console.held[("org-a", "team")] |= {KIM, "x@org-a.example.test"}
    before = world.console.state()
    out = await om.set_seat(MEL, "assign")
    assert out["ok"] is False and out["status"] == 409 and "Billing page" in out["error"]
    assert "3 of 3 seats are in use" in out["error"]
    assert world.card.shown == [] and world.console.state() == before


@pytest.mark.parametrize(("status", "detail", "words"), [
    (409, {"detail": {"reason": "at_capacity", "buy_more": {
        "plan_slug": "team", "purchased": 3, "assigned": 3, "additional_seats_required": 1}}},
     "The plan team is at its cap: 3 of 3 seats are in use. Buy 1 more seat on the Billing page"),
    (404, {"detail": "no such member in this organization"},
     "The Customer Console has no member"),
    (403, {"detail": "organization is suspended; seats are locked"},
     "The Customer Console refused: organization is suspended; seats are locked."),
], ids=["cap", "member-gone", "locked"])
async def test_a_console_refusal_after_the_card_is_words_and_changes_nothing(
        world, status, detail, words) -> None:
    world.console.refuse["/registry/seats"] = (status, detail)
    before = world.console.state()
    out = await om.set_seat(MEL, "assign")
    assert out["ok"] is False and out["status"] == status and words in out["error"], out
    assert out["decision"] == "approved" and len(world.card.shown) == 1
    assert world.console.state() == before
    assert not [s for s in world.sent if "order" in s[1]]


# ── f. R11 at the gateway ────────────────────────────────────────────────────


@pytest.mark.parametrize("claim", [{"org_slug": "org-b"}, {"actor_email": ZED},
                                   {"org": "org-b"}, {"email": ZED}])
async def test_a_body_that_names_an_org_or_an_actor_never_reaches_the_console(world,
                                                                            claim) -> None:
    resp = await world.route(ADA, om.SEAT_ASSIGN_PATH,
                             {"member_email": MEL, "plan_slug": "team", **claim})
    assert resp.status_code == 400 and "R11" in resp.json()["detail"]
    assert world.console.calls == []


# ── g. A box with no Console ─────────────────────────────────────────────────


async def test_with_no_console_the_tools_say_so_and_call_nothing(unwired) -> None:
    out = await om.read_seats()
    assert out["ok"] is False and out["status"] == 503
    assert out["error"].startswith("Seats need the Customer Console, which is not connected here")
    out = await om.set_seat(MEL, "assign")
    assert out["ok"] is False and out["status"] == 503 and "nothing changed" in out["error"]
    assert unwired.console.calls == [] and unwired.card.shown == []
