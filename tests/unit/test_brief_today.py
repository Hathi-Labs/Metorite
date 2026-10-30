"""WS-BRIEF BR-2c-1 — the read route and the stored chat turn, with no
database.

Spec: ``project-docs/specs/core_brief.md`` §5 items 2 and 3, §10 row 5.
Code: ``gateway/routes/brief_today.py``, ``gateway/routes/chat_brief.py``
(``stored_parts``, ``_stored_or_none``), ``gateway/brief/flags.py``
(``in_app``) and ``gateway/brief/store.py`` (``read_latest_run``). The
database half is ``tests/unit/test_brief_today_r8.py``.

What this holds:

* ``BRIEF_IN_APP`` is OFF by default, the ``BRIEF_`` prefix keeps it off
  the tenant env writes, and ``flags.in_app`` needs the switch AND the org.
* The route is 404 with the switch off, for any ``kind``. With it on, an
  unknown ``kind`` is 422.
* The route is a pure read: it serves a ``ready`` and a ``delivered`` run
  with the stored state, and it calls no write of the store.
* The read asks for the newest run dated today or the day before, whose
  slot is not after the clock.
* The route never calls ``queue.enqueue``, and names no queue at all.
* ``stored_parts`` gives the pills in position order, and the title and the
  "more" count of the template lead.
* A failed stored read of the chat logs ``chat_brief.stored_failed`` and
  answers None, so the chat composes from the feed.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.brief import flags, store
from gateway.jobs import queue
from gateway.reach import plan
from gateway.routes import brief_today, chat_brief

_ROOT = Path(__file__).resolve().parents[2]

ORG = "11111111-1111-1111-1111-111111111111"
D = date(2099, 3, 10)
NOW = datetime(2099, 3, 10, 2, 30, tzinfo=UTC)
READY_AT = datetime(2099, 3, 10, 2, 0, tzinfo=UTC)


def _settings(monkeypatch, **kw: Any) -> None:
    base = {"brief_enabled": True, "brief_orgs": ORG, "brief_in_app": True}
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(**{**base, **kw}))


def _run(state: str = "ready", lead: str | None = "Your day, Tue 10 Mar. 2 things need you.",
         **kw: Any) -> store.Run:
    base: dict[str, Any] = {
        "id": "run-1", "member_email": "ana@x.test", "kind": "brief", "local_date": D,
        "slot_at": READY_AT, "state": state, "lead": lead, "lead_source": "template",
        "lead_claim_ids": (), "model_request_id": None, "unattended_run_id": None,
        "error": None, "created_at": READY_AT, "ready_at": READY_AT, "delivered_at": None}
    return store.Run(**{**base, **kw})


def _claim(position: int, text: str) -> store.Claim:
    return store.Claim(
        id=f"c{position}", run_id="run-1", position=position, section="needs_you",
        text=text, source_app="email", source_kind="needs_reply",
        source_id=f"email:t{position}", source_ids=(), href=f"/email?thread=t{position}",
        value=None, value_path=None, fingerprint=None)


# ── The switch ──────────────────────────────────────────────────────────────


def test_brief_in_app_is_dark_by_default_and_off_the_tenant_env(monkeypatch) -> None:
    from acb_common.env_guard import is_platform_env
    from acb_common.settings import Settings

    assert Settings.model_fields["brief_in_app"].default is False
    assert is_platform_env("BRIEF_IN_APP")
    _settings(monkeypatch, brief_in_app=False)
    assert flags.in_app(ORG) is False


def test_in_app_needs_the_switch_and_the_org(monkeypatch) -> None:
    _settings(monkeypatch)
    assert flags.in_app(ORG) is True
    assert flags.in_app("22222222-2222-2222-2222-222222222222") is False
    assert flags.in_app(None) is False
    _settings(monkeypatch, brief_enabled=False)
    assert flags.in_app(ORG) is False
    _settings(monkeypatch, brief_orgs="")
    assert flags.in_app(ORG) is False
    _settings(monkeypatch, brief_in_app="true")  # a string is not True
    assert flags.in_app(ORG) is False


# ── The route ───────────────────────────────────────────────────────────────


def _user(email: str = "ana@x.test") -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access(["feature:chat"], roles=["member"]))


@pytest.fixture
def fake(monkeypatch):
    """The store, the date and the clock of the route, as fakes. ``box``
    holds the run that the read finds, and records each move."""
    box: dict[str, Any] = {"run": _run(), "claims": [_claim(1, "b"), _claim(0, "a")],
                           "writes": []}

    class _Db:
        pass

    class _Session:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *exc):
            return False

    async def read_latest_run(db, member, *, kind, first_date, last_date, now):
        box["read"] = (member, kind, first_date, last_date, now)
        return box["run"]

    async def read_claims(db, member, run_id):
        return list(box["claims"])

    def writer(name):
        async def write(*a, **kw):
            box["writes"].append(name)
            raise AssertionError(f"GET /brief/today must not call store.{name}")
        return write

    async def local_date(org, user, now):
        return D

    async def refuse(*a, **kw):
        raise AssertionError("the read route must never enqueue")

    import gateway.db as gdb

    monkeypatch.setattr(gdb, "tenant_session", lambda *a, **kw: _Session())
    monkeypatch.setattr(store, "read_latest_run", read_latest_run)
    monkeypatch.setattr(store, "read_claims", read_claims)
    for name in ("set_state", "set_lead", "create_run", "add_claims", "link_run",
                 "discard_planned", "prune_runs"):
        monkeypatch.setattr(store, name, writer(name))
    monkeypatch.setattr(brief_today, "brief_local_date", local_date)
    monkeypatch.setattr(brief_today, "_now", lambda: NOW)
    monkeypatch.setattr(queue, "enqueue", refuse)
    _settings(monkeypatch)
    return box


def _client(user: UserContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(brief_today.router)
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    return TestClient(app)


def test_the_switch_off_is_404_for_any_kind(fake, monkeypatch) -> None:
    _settings(monkeypatch, brief_in_app=False)
    client = _client()
    for kind in ("brief", "closeout", "nonsense"):
        res = client.get(f"/brief/today?kind={kind}")
        assert res.status_code == 404, kind
    assert "read" not in fake and fake["writes"] == []


def test_an_unknown_kind_is_422(fake) -> None:
    res = _client().get("/brief/today?kind=weekly")
    assert res.status_code == 422
    assert "read" not in fake


#: The read's window: the day before to today, and the slot not after now.
_WINDOW = ("ana@x.test", "brief", date(2099, 3, 9), D, NOW)


def test_a_ready_run_is_served_in_order_with_no_write(fake) -> None:
    res = _client().get("/brief/today")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "kind": "brief", "local_date": "2099-03-10", "state": "ready",
        "lead": "Your day, Tue 10 Mar. 2 things need you.", "lead_source": "template",
        "ready_at": READY_AT.isoformat(),
        "claims": [
            {"position": 0, "section": "needs_you", "text": "a", "source_app": "email",
             "source_kind": "needs_reply", "href": "/email?thread=t0"},
            {"position": 1, "section": "needs_you", "text": "b", "source_app": "email",
             "source_kind": "needs_reply", "href": "/email?thread=t1"},
        ],
    }
    # The member is the session's, and the window is the route's own.
    assert fake["read"] == _WINDOW
    assert fake["writes"] == []


def test_a_delivered_run_is_served_with_no_write(fake) -> None:
    fake["run"] = _run("delivered", delivered_at=READY_AT)
    res = _client().get("/brief/today")
    assert res.status_code == 200 and res.json()["state"] == "delivered"
    assert fake["writes"] == []


def test_the_route_names_no_write_of_the_store() -> None:
    src = Path(brief_today.__file__).read_text(encoding="utf-8")
    for word in ("set_state", "set_lead", "create_run", "add_claims", "link_run",
                 "discard_planned", "prune_runs", "_deliver", "UPDATE", "INSERT"):
        assert word not in src, word


@pytest.mark.parametrize("state", ["planned", "gathered", "phrasing", "empty", "failed"])
def test_a_run_that_is_not_ready_is_404(fake, state) -> None:
    fake["run"] = _run(state)
    res = _client().get("/brief/today")
    assert res.status_code == 404 and res.json()["detail"] == brief_today.NO_BRIEF
    assert fake["writes"] == []


def test_no_run_is_404(fake) -> None:
    fake["run"] = None
    res = _client().get("/brief/today")
    assert res.status_code == 404 and res.json()["detail"] == brief_today.NO_BRIEF


def test_the_request_cannot_name_a_member_an_org_or_a_date(fake) -> None:
    res = _client().get("/brief/today?member=ben@x.test&organization_id=x&local_date=2099-01-01")
    assert res.status_code == 200
    assert fake["read"] == _WINDOW
    params = set(inspect.signature(brief_today.get_today).parameters)
    assert params == {"kind", "user"}


def test_the_route_names_no_queue_no_plan_and_no_model() -> None:
    src = Path(brief_today.__file__).read_text(encoding="utf-8")
    for word in ("enqueue(", "gateway.jobs", "snapshot(", "brief_parts(", "acompletion",
                 "litellm"):
        assert word not in src, word


def test_the_router_is_mounted_and_gated_on_chat() -> None:
    main = (Path(brief_today.__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8")
    assert "from gateway.routes.brief_today import router" in main
    assert {r.path for r in brief_today.router.routes} == {"/brief/today"}
    [dep] = brief_today.router.dependencies
    assert dep.dependency.__qualname__.startswith("require_feature_router")
    assert "chat" in repr(inspect.getclosurevars(dep.dependency).nonlocals)


# ── The stored chat turn ────────────────────────────────────────────────────


def test_stored_parts_are_the_claims_in_order_and_the_template_title() -> None:
    got = chat_brief.stored_parts(_run(), [_claim(1, "b"), _claim(0, "a")])
    assert got is not None
    lead, parts = got
    assert lead == "Your day, Tue 10 Mar. 2 things need you."
    assert parts == plan.BriefParts("Your day, Tue 10 Mar", ["a", "b"], 0)
    assert chat_brief.brief_card(parts)["props"]["data"]["pills"] == ["a", "b"]


def test_stored_parts_count_the_rest_from_the_template_lead() -> None:
    run = _run(lead="Your day, Tue 10 Mar. 9 things need you.")
    claims = [_claim(i, f"line {i}") for i in range(plan.BRIEF_LINES)]
    got = chat_brief.stored_parts(run, claims)
    assert got is not None and got[1].rest == 2
    assert chat_brief.brief_card(got[1])["props"]["data"]["note"] == "+2 more on My Day"
    one = chat_brief.stored_parts(_run(lead="Your day, Tue 10 Mar. 1 thing needs you."),
                                  [_claim(0, "a")])
    assert one is not None and one[1].rest == 0 and one[1].title == "Your day, Tue 10 Mar"


def test_stored_parts_of_a_lead_in_another_shape_take_the_date_title() -> None:
    got = chat_brief.stored_parts(_run(lead="Two replies wait, and the deck is due."),
                                  [_claim(0, "a")])
    assert got is not None
    assert got[1] == plan.BriefParts("Your day, Tue 10 Mar", ["a"], 0)


def test_stored_parts_need_a_lead_and_a_claim() -> None:
    assert chat_brief.stored_parts(_run(lead=None), [_claim(0, "a")]) is None
    assert chat_brief.stored_parts(_run(), []) is None


def test_the_chat_reads_the_store_only_behind_the_switch() -> None:
    """The flag-off path of ``post_brief`` names the store nowhere: the one
    read sits behind ``brief_flags.in_app``. The R8 suite runs it."""
    src = inspect.getsource(chat_brief.post_brief)
    assert ("(await _stored_or_none(org, user, email, now, today)\n"
            "              if brief_flags.in_app(org) else None)") in src
    assert src.count("_stored_or_none(") == 1 and "brief_store." not in src
    assert "_stored_brief(" not in src


@pytest.mark.parametrize("where", ["read_day", "org_zone", "store"])
async def test_a_failed_stored_read_logs_and_answers_none(monkeypatch, where) -> None:
    """P2-a: a failure in the stored read (the day read, the org zone, or
    the store) answers None, so the chat composes from the feed. The log
    names the error class only."""
    from zoneinfo import ZoneInfo

    from gateway import my_day_loop
    from gateway.brief import planner
    from gateway.routes.shell import state

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    async def boom(*a, **kw):
        raise RuntimeError("secret detail ana@x.test")

    async def read_day(org, user):
        return state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)

    async def org_zone(db):
        return ZoneInfo("UTC")

    import gateway.db as gdb

    monkeypatch.setattr(gdb, "tenant_session", lambda *a, **kw: _Session())
    monkeypatch.setattr(my_day_loop, "_read_day", boom if where == "read_day" else read_day)
    monkeypatch.setattr(planner, "_org_zone", boom if where == "org_zone" else org_zone)
    monkeypatch.setattr(store, "read_latest_run", boom)
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(chat_brief._log, "warning",
                        lambda event, **kw: logged.append((event, kw)))
    got = await chat_brief._stored_or_none(ORG, _user(), "ana@x.test", NOW, D)
    assert got is None
    assert logged == [("chat_brief.stored_failed", {"error": "RuntimeError"})]
