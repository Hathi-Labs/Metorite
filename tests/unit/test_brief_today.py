"""WS-BRIEF BR-2c-1 — the read route and the stored chat turn, with no
database.

Spec: ``project-docs/specs/core_brief.md`` §5 items 2 and 3, §10 row 5.
Code: ``gateway/routes/brief_today.py``, ``gateway/routes/chat_brief.py``
(``stored_parts``), ``gateway/brief/flags.py`` (``in_app``) and
``gateway/brief/store.py`` (the ``ready`` to ``delivered`` move). The
database half is ``tests/unit/test_brief_today_r8.py``.

What this holds:

* ``BRIEF_IN_APP`` is OFF by default, the ``BRIEF_`` prefix keeps it off
  the tenant env writes, and ``flags.in_app`` needs the switch AND the org.
* The route is 404 with the switch off, for any ``kind``. With it on, an
  unknown ``kind`` is 422.
* A served ``ready`` run moves to ``delivered`` once, through the guarded
  move. A lost guard still serves. A ``delivered`` run makes no move.
* The route never calls ``queue.enqueue``, and names no queue at all.
* ``stored_parts`` gives the pills in position order, and the title and the
  "more" count of the template lead.
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
                           "moves": [], "moved": True, "fail": False}

    class _Db:
        pass

    class _Session:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *exc):
            return False

    async def read_run(db, member, *, kind, local_date):
        box["read"] = (member, kind, local_date)
        return box["run"]

    async def read_claims(db, member, run_id):
        return list(box["claims"])

    async def set_state(db, member, run_id, state, *, now, error=None, from_state=None):
        if box["fail"]:
            raise RuntimeError("db down")
        box["moves"].append((member, run_id, state, from_state, now))
        return box["moved"]

    async def local_date(org, user, now):
        return D

    async def refuse(*a, **kw):
        raise AssertionError("the read route must never enqueue")

    import gateway.db as gdb

    monkeypatch.setattr(gdb, "tenant_session", lambda *a, **kw: _Session())
    monkeypatch.setattr(store, "read_run", read_run)
    monkeypatch.setattr(store, "read_claims", read_claims)
    monkeypatch.setattr(store, "set_state", set_state)
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
    assert "read" not in fake and fake["moves"] == []


def test_an_unknown_kind_is_422(fake) -> None:
    res = _client().get("/brief/today?kind=weekly")
    assert res.status_code == 422
    assert "read" not in fake


def test_a_ready_run_is_served_in_order_and_delivered_once(fake) -> None:
    res = _client().get("/brief/today")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "kind": "brief", "local_date": "2099-03-10", "state": "delivered",
        "lead": "Your day, Tue 10 Mar. 2 things need you.", "lead_source": "template",
        "ready_at": READY_AT.isoformat(),
        "claims": [
            {"position": 0, "section": "needs_you", "text": "a", "source_app": "email",
             "source_kind": "needs_reply", "href": "/email?thread=t0"},
            {"position": 1, "section": "needs_you", "text": "b", "source_app": "email",
             "source_kind": "needs_reply", "href": "/email?thread=t1"},
        ],
    }
    # The member is the session's, and the date is the route's own.
    assert fake["read"] == ("ana@x.test", "brief", D)
    assert fake["moves"] == [("ana@x.test", "run-1", "delivered", "ready", NOW)]


def test_a_lost_guard_still_serves(fake) -> None:
    fake["moved"] = False
    res = _client().get("/brief/today?kind=brief")
    assert res.status_code == 200 and res.json()["state"] == "delivered"
    assert len(fake["moves"]) == 1


def test_a_failed_stamp_still_serves_and_says_ready(fake) -> None:
    fake["fail"] = True
    res = _client().get("/brief/today")
    assert res.status_code == 200 and res.json()["state"] == "ready"


def test_a_delivered_run_is_served_with_no_move(fake) -> None:
    fake["run"] = _run("delivered", delivered_at=READY_AT)
    res = _client().get("/brief/today")
    assert res.status_code == 200 and res.json()["state"] == "delivered"
    assert fake["moves"] == []


@pytest.mark.parametrize("state", ["planned", "gathered", "phrasing", "empty", "failed"])
def test_a_run_that_is_not_ready_is_404(fake, state) -> None:
    fake["run"] = _run(state)
    res = _client().get("/brief/today")
    assert res.status_code == 404 and res.json()["detail"] == brief_today.NO_BRIEF
    assert fake["moves"] == []


def test_no_run_is_404(fake) -> None:
    fake["run"] = None
    res = _client().get("/brief/today")
    assert res.status_code == 404 and res.json()["detail"] == brief_today.NO_BRIEF


def test_the_request_cannot_name_a_member_an_org_or_a_date(fake) -> None:
    res = _client().get("/brief/today?member=ben@x.test&organization_id=x&local_date=2099-01-01")
    assert res.status_code == 200
    assert fake["read"] == ("ana@x.test", "brief", D)
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
    assert "_stored_brief(org, user, email, now) if brief_flags.in_app(org) else None" in src
    assert src.count("_stored_brief(") == 1 and "brief_store." not in src
