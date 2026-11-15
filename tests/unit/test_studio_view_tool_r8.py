"""STU-AF10b-3 — the saved-view tool on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 AF-10b. The hermetic twin is
``tests/unit/test_studio_view_tool.py``.

The REAL tool ``studio_view_manage`` runs. Its HTTP client is swapped for one
that calls the REAL view handlers (``routes/studio/views.py``) with the org
bound as the auth layer does, on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. The world is the
one of ``test_studio_saved_views_r8.py``: Owen owns an app, and Ana is a
viewer through the ``ops`` group install at version 1. Zed is in org B.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_view_tool_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**R7: red first, then mutate.**

* M1. ``views_store._LIST_SQL``: drop ``(shared OR owner_email = :me)``.
  ``test_r8_the_tool_lists_the_views_the_member_reads_and_no_private_view_of_another``
  goes red.
* M2. ``views.list_views``: return the views of a viewer whole.
  ``test_r8_a_viewer_reads_a_shared_view_through_the_tool_with_dropped`` goes red.
* M3. ``_view_needs_card``: ``return await _must_ask()``.
  ``test_r8_a_share_through_the_tool_shows_one_card_and_writes_the_audit_row``
  goes red.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_skills import ask_tools, egress
from acb_skills import studio_tools as st
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from gateway.routes.studio import views
from gateway.studio import views_store
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_studio_saved_views_r8 import (  # noqa: F401
    ANA,
    BEN,
    CAT,
    OWEN,
    SECRET,
    ZED,
    _bound,
    _Request,
    _run,
    _user,
    world,
)

pytestmark = _DB_GATE

_ID = r"[0-9a-f-]{36}"


class _Dispatch:
    """An ``httpx.AsyncClient`` that calls the real view handlers. The member
    comes from the ``X-User-Email`` header, as the gateway reads it, and the
    org from the table of the world."""

    def __init__(self, scope: Any, orgs: dict[str, str]) -> None:
        self.scope, self.orgs, self.calls = scope, orgs, []

    async def __aenter__(self) -> _Dispatch:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def request(self, method: str, url: str, headers: Any = None, json: Any = None):
        import re
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append((method, path))
        email = headers["X-User-Email"]
        org = self.orgs[email.lower()]
        user = _user(org, email)
        listed = re.fullmatch(rf"/studio/packages/({_ID})/views\?object=([a-z0-9_]+)", path)
        coll = re.fullmatch(rf"/studio/packages/({_ID})/views", path)
        one = re.fullmatch(rf"/studio/packages/({_ID})/views/({_ID})", path)
        if method == "GET" and listed:
            status, got = await _bound(org, lambda: views.list_views(
                listed.group(1), user=user, object_name=listed.group(2)))
        elif method == "POST" and coll:
            status, got = await _bound(org, lambda: views.create_view(
                coll.group(1), request=_Request(json), user=user), ok=201)
        elif method == "PATCH" and one:
            status, got = await _bound(org, lambda: views.update_view(
                one.group(1), one.group(2), request=_Request(json), user=user))
        elif method == "DELETE" and one:
            status, got = await _bound(org, lambda: views.delete_view(
                one.group(1), one.group(2), user=user))
        else:
            raise AssertionError(f"no route for {method} {path}")
        body = got if status < 300 else {"detail": got}
        return httpx.Response(status, json=body, request=httpx.Request(method, "http://x"))


class _Card:
    def __init__(self) -> None:
        self.answer: Any = True
        self.shown: list[dict[str, Any]] = []

    async def __call__(self, title, detail="", context="", **kw):
        self.shown.append({"title": title, "context": context})
        return self.answer


@pytest.fixture
def tool(world, monkeypatch):  # noqa: F811
    """The tool, wired to the world. ``who(email)`` picks the member of the run."""
    orgs = {e.lower(): world.a for e in (OWEN, ANA, BEN, CAT)} | {ZED.lower(): world.b}
    me = {"email": OWEN}
    dispatch = _Dispatch(world, orgs)
    card = _Card()

    async def clear(ctx=None) -> bool:
        return False

    monkeypatch.setattr(egress, "write_must_show_card", clear)
    monkeypatch.setattr(egress, "chat_is_held", clear)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    monkeypatch.setattr(st, "_current_user_email", lambda: me["email"])
    monkeypatch.setattr(st.httpx, "AsyncClient", lambda **_kw: dispatch)

    def who(email: str) -> None:
        me["email"] = email

    with artifact_context_scope():
        bind_artifact_context(no_egress=False)
        yield SimpleNamespace(world=world, who=who, card=card, dispatch=dispatch)


def _view_rows(out: dict[str, Any]) -> list[dict[str, Any]]:
    inner = out["views"].split("\n")[1:-1]
    return json.loads("\n".join(inner))


def _call(w: Any, **kw: Any):
    return st.studio_view_manage(package_id=w.pid, object="visit", **kw)


def test_r8_the_tool_lists_the_views_the_member_reads_and_no_private_view_of_another(tool) -> None:
    w = tool.world

    async def go() -> dict[str, Any]:
        tool.who(ANA)
        made = await _call(w, op="save", name="Ana only")
        tool.who(BEN)
        ben = await _call(w, op="list")
        tool.who(ANA)
        ana = await _call(w, op="list")
        return {"made": made, "ben": ben, "ana": ana}

    got = _run(w, go)
    assert got["made"]["ok"] is True and tool.card.shown == []
    assert sorted(r["name"] for r in _view_rows(got["ana"])) == ["Ana only", "Big visits"]
    assert [r["name"] for r in _view_rows(got["ben"])] == ["Big visits"]


def test_r8_org_a_never_reads_org_b_through_the_tool(tool) -> None:
    w = tool.world

    async def go() -> dict[str, Any]:
        tool.who(ZED)
        return {"list": await _call(w, op="list"),
                "save": await _call(w, op="save", name="Intruder"),
                "delete": await _call(w, op="delete", view_id=w.shared)}

    got = _run(w, go)
    for case in ("list", "save", "delete"):
        assert got[case]["ok"] is False and got[case]["status"] == 404, case
        assert got[case]["errors"][0]["message"] in (st._VIEW_NOT_FOUND, st._VIEW_GONE), case
    with w.p.admin_engine.connect() as c:
        names = [r[0] for r in c.execute(text(
            "SELECT name FROM studio_saved_views WHERE package_id = CAST(:p AS uuid)"),
            {"p": w.pid}).all()]
    assert names == ["Big visits"]


def test_r8_a_viewer_reads_a_shared_view_through_the_tool_with_dropped(tool) -> None:
    w = tool.world

    async def go() -> dict[str, Any]:
        tool.who(ANA)
        return await _call(w, op="list")

    out = _run(w, go)
    [view] = _view_rows(out)
    assert view["shared"] is True and view["mine"] is False and view["dropped"] == 3
    assert out["dropped"] == 3 and "dropped counts" in out["note"]
    assert str(SECRET) not in json.dumps(out) and "cost" not in json.dumps(out)


def test_r8_a_share_through_the_tool_shows_one_card_and_writes_the_audit_row(tool) -> None:
    w = tool.world

    async def go() -> dict[str, Any]:
        tool.who(OWEN)
        made = await _call(w, op="save", name="Team plan")
        vid = _view_rows(await _call(w, op="list"))
        vid = next(r["id"] for r in vid if r["name"] == "Team plan")
        none = len(tool.card.shown)
        shared = await _call(w, op="update", view_id=vid, shared=True)
        tool.who(ANA)
        seen = await _call(w, op="list")
        return {"made": made, "none": none, "shared": shared, "seen": seen}

    got = _run(w, go)
    assert got["made"]["ok"] is True and got["none"] == 0
    assert got["shared"]["decision"] == "approved" and len(tool.card.shown) == 1
    assert tool.card.shown[0]["title"] == "Share the view «Team plan»?"
    assert "Team plan" in [r["name"] for r in _view_rows(got["seen"])]
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
            "AND detail->>'op' LIKE 'view_%' ORDER BY id"), {"p": w.pid}).all()
    ops = [(r[0] if isinstance(r[0], dict) else json.loads(r[0]))["op"] for r in rows]
    assert ops == ["view_create", "view_create", "view_share"]


def test_r8_a_viewer_who_asks_to_share_reads_the_sentence_and_nothing_is_written(tool) -> None:
    w = tool.world

    async def go() -> dict[str, Any]:
        tool.who(ANA)
        return {"save": await _call(w, op="save", name="Team plan", shared=True),
                "change": await _call(w, op="update", view_id=w.shared, name="Mine now")}

    got = _run(w, go)
    sentence = "This app is shared with you to read, so only its owner shares a view."
    assert got["save"]["ok"] is False and got["save"]["errors"][0]["message"] == sentence
    assert got["change"]["errors"][0]["message"] == sentence
    with w.p.admin_engine.connect() as c:
        names = [r[0] for r in c.execute(text(
            "SELECT name FROM studio_saved_views WHERE package_id = CAST(:p AS uuid)"),
            {"p": w.pid}).all()]
    assert names == ["Big visits"]


def test_r8_the_cap_and_the_name_clash_come_back_with_the_route_text(tool) -> None:
    w = tool.world

    async def go() -> dict[str, Any]:
        from acb_common.db import tenant_session
        from gateway.studio import views_store as store

        async with tenant_session(w.a) as db:
            for i in range(store.MAX_PRIVATE):
                await store.create_view(db, ANA, package_id=w.pid, object_name="visit",
                                        name=f"v{i}", shared=False, spec={})
        tool.who(ANA)
        capped = await _call(w, op="save", name="v51")
        tool.who(BEN)
        await _call(w, op="save", name="Dup")
        taken = await _call(w, op="save", name="dup")
        return {"capped": capped, "taken": taken}

    got = _run(w, go)
    assert got["capped"]["ok"] is False and got["capped"]["code"] == "view_cap"
    assert got["capped"]["status"] == 409 and got["capped"]["errors"][0]["message"].strip()
    assert got["taken"]["code"] == "view_name_taken" and got["taken"]["status"] == 409
    assert got["taken"]["errors"][0]["message"].strip()
    assert views_store.MAX_PRIVATE == 50 and uuid.UUID(w.pid)
