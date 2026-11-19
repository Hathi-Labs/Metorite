"""WS-STU J2d (WS-TOF TOF-4b) — the chat tool, the Needs row and the purge
rule of a skill share, on a real database (R8).

Spec: ``project-docs/specs/ai_studio_rows/J2d.md``. Code: the chat tool
``acb_skills.studio_tools.studio_skill_share``, the owner's read
``GET /studio/skills/{id}/share``, the needs source ``studio``
(``routes/shell/needs.py::_studio_approvals``), and the purge clauses of
``studio_skills`` (``routes/admin/members.py``). The hermetic twin is
``tests/unit/test_studio_skill_share_chat.py``.

The tool's HTTP calls reach the REAL router of Studio through
``httpx.ASGITransport``. The real handlers and the real store run on a
phase-4 promoted scratch database, as the non-privileged app role, so FORCE
RLS binds each statement. A small ASGI wrapper binds the org of the call, as
the auth layer does. Org A has Owen (the owner of each skill, a holder too),
Ada (a holder of ``studio:publish``) and Mel (``feature:studio`` only). Org B
has Zed, a holder, and the same address as Owen.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_skill_share_chat_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2d).**

a. Owen asks from the chat. A card shows, and the row does not change before
   Approve. After Approve, the share reads ``pending``.
b. Mel, Zed of org B, and Owen's address in org B get 404 from the tool and
   the route. Nothing changes.
c. Ada sees one Needs row for the ask of Owen. Owen never sees his own ask,
   and Zed sees none.
d. Owen's approve from the chat is refused with no card. Ada approves from
   the chat, and Mel then loads the skill.
e. Owen unshares from the chat, and Mel loads nothing at the next read.
f. With ``STUDIO_ORGS`` empty, the tool gets 404, and Ada's needs answer is the
   same byte for byte with an ask or with none.
g. The member purge keeps Owen's shared skill with its versions. It deletes
   his private skill and his skills with only a pending or a rejected ask. The
   skills of Mel and of org B stay. It runs as the app role and as a superuser.
h. Ada, a holder who is not the owner, lists the shared skills from the chat
   and ends the share of Owen's skill on one card. The share columns clear as
   the owner's unshare clears them, and Mel loads nothing at the next read.
   Mel cannot end it, and Zed of org B never sees it. A declined card writes
   nothing.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_skills import ask_tools, egress
from acb_skills import studio_tools as st
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from fastapi import FastAPI, Request
from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS, _purge_params
from gateway.routes.shell import needs as shell
from gateway.routes.studio import actions
from gateway.studio import skills_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_studio_builder_tools import ROWS

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j2d.test"
ADA = f"ada.{TAG}@j2d.test"
MEL = f"mel.{TAG}@j2d.test"
ZED = f"zed.{TAG}@j2d.test"
_LIKE = f"%.{TAG}@j2d.test"
_COUNTER = iter(range(10_000))
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
GRANTS = {OWEN: PUBLISHER, ADA: PUBLISHER, MEL: STUDIO, ZED: PUBLISHER}
_REAL_CLIENT = httpx.AsyncClient


class _Card:
    def __init__(self) -> None:
        self.answer: Any = True
        self.shown: list[dict[str, Any]] = []

    async def __call__(self, title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        self.shown.append({"title": title, "detail": detail, "context": context})
        return self.answer


class _World:
    def __init__(self, promoted: Any) -> None:  # noqa: F811
        self.p = promoted
        self.card = _Card()
        self.member = OWEN
        self.org = promoted.org_a

    @property
    def url(self) -> str:
        return self.p.app_url.render_as_string(hide_password=False)

    def run(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        async def go() -> Any:
            async with tenant_engine_scope(self.url):
                return await fn()
        return asyncio.run(go())

    async def act(self, member: str, org: str, **kw: Any) -> dict[str, Any]:
        """The chat tool, as *member* of *org*."""
        self.member, self.org = member, org
        return await st.studio_skill_share(**kw)


def _app(w: _World) -> Any:
    app = FastAPI()
    app.include_router(actions.router)

    def who(request: Request) -> UserContext:
        email = request.headers.get("x-user-email", "")
        return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=w.org,
                           access=build_access(GRANTS.get(email, [])))

    app.dependency_overrides[get_current_user] = who

    async def bound(scope: Any, receive: Any, send: Any) -> None:
        # The auth layer binds the org of the session. Here: the org of the call.
        token = bind_tenant(w.org)
        try:
            await app(scope, receive, send)
        finally:
            release_tenant(token)

    return bound


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    w = _World(promoted)

    async def catalog(*, app: str = "") -> list[dict[str, Any]]:
        return list(ROWS)

    async def not_held(ctx: Any = None) -> bool:
        return False

    transport = httpx.ASGITransport(app=_app(w))
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    monkeypatch.setattr(st.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: w.member)
    monkeypatch.setattr(st, "_read_catalog", catalog)
    monkeypatch.setattr(egress, "write_must_show_card", not_held)
    monkeypatch.setattr(egress, "chat_is_held", not_held)
    monkeypatch.setattr(ask_tools, "request_confirmation", w.card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    with artifact_context_scope():
        bind_artifact_context(no_egress=False)
        yield w
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_skills WHERE owner_email LIKE :t"), {"t": _LIKE})


def _body(n: int) -> dict[str, Any]:
    return {"description": f"Use this for quotes, round {n}.",
            "instructions": f"Rules of round {n}: read the price list, then fill the template.",
            "actions": ["knowledge.get_page"]}


async def _published(org: str, email: str = OWEN, versions: int = 1) -> str:
    async with tenant_session(org) as db:
        skill = await store.create_skill(
            db, email, name="Quotes", slug=f"quotes-{TAG}-{next(_COUNTER)}")
        for n in range(versions):
            await store.add_version(db, email, skill.id, _body(n + 1))
        await store.set_state(db, email, skill.id, "published")
    return skill.id


async def _loads(org: str, member: str, sid: str) -> list[tuple[str, int]]:
    async with tenant_session(org) as db:
        return [(s.id, v.version) for s, v in await store.loadable_versions(db, member, [sid])]


async def _needs(org: str, email: str) -> dict[str, Any]:
    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(GRANTS[email]))
    token = bind_tenant(org)
    try:
        return await shell.shell_needs(limit=50, user=user)
    finally:
        release_tenant(token)


def _studio_rows(answer: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in answer["items"] if i["app"] == "studio"]


def _row(world: _World, sid: str) -> dict[str, Any]:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT visibility, shared_version, share_state, share_version, "
            "share_requested_by, share_decided_by FROM studio_skills "
            "WHERE id = CAST(:i AS uuid)"), {"i": sid}).mappings().first()
    return dict(row) if row else {}


# ── a. The ask from the chat ───────────────────────────────────────────────


def test_r8_the_owner_asks_from_the_chat_and_nothing_is_written_before_approve(
    world: _World,
) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a, versions=2)
        world.card.answer = False
        stopped = await world.act(OWEN, a, skill_id=sid, act="ask")
        before = _row(world, sid)
        world.card.answer = True
        asked = await world.act(OWEN, a, skill_id=sid, act="ask")
        status = await world.act(OWEN, a, skill_id=sid)
        return {"sid": sid, "stopped": stopped, "before": before, "asked": asked,
                "status": status}

    got = world.run(go)
    assert got["stopped"]["decision"] == "stopped" and got["stopped"]["ok"] is False
    assert (got["before"]["share_state"], got["before"]["share_version"]) == (None, None)
    assert len(world.card.shown) == 2
    assert got["asked"]["ok"] is True and got["asked"]["share_state"] == "pending"
    assert got["asked"]["share_version"] == 2
    assert "holder of studio:publish" in got["asked"]["note"]
    row = _row(world, got["sid"])
    assert (row["visibility"], row["share_state"], row["share_version"]) == (
        "private", "pending", 2)
    assert row["share_requested_by"] == OWEN
    assert "Version 2 waits for a holder" in got["status"]["text"]


# ── b. Another member and org B ────────────────────────────────────────────


def test_r8_another_member_and_org_b_get_404_from_the_tool_and_the_route(
    world: _World,
) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def route(member: str, org: str, sid: str) -> int:
        world.member, world.org = member, org
        async with st.httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"http://gw/studio/skills/{sid}/share",
                                 headers={"X-User-Email": member})
        return r.status_code

    async def go() -> dict[str, Any]:
        sid = await _published(a)
        await world.act(OWEN, a, skill_id=sid, act="ask")
        shown = len(world.card.shown)
        out = {
            "mel": await world.act(MEL, a, skill_id=sid),
            "mel_ask": await world.act(MEL, a, skill_id=sid, act="ask"),
            "mel_unshare": await world.act(MEL, a, skill_id=sid, act="unshare"),
            "zed": await world.act(ZED, b, skill_id=sid),
            "zed_approve": await world.act(ZED, b, skill_id=sid, act="approve"),
            "owen_b": await world.act(OWEN, b, skill_id=sid),
            "routes": [await route(MEL, a, sid), await route(ZED, b, sid),
                       await route(OWEN, b, sid), await route(OWEN, a, sid)],
        }
        out["cards"] = len(world.card.shown) - shown
        out["sid"] = sid
        return out

    got = world.run(go)
    for key in ("mel", "mel_ask", "mel_unshare", "zed", "owen_b"):
        assert got[key]["ok"] is False and got[key]["status"] == 404, (key, got[key])
    # Zed holds the right in org B, and no ask of org A shows there.
    assert got["zed_approve"]["ok"] is False
    assert got["zed_approve"]["errors"][0]["message"] == st._SHARE_NONE
    # Non-vacuity: Owen in org A reads his own share.
    assert got["routes"] == [404, 404, 404, 200]
    assert got["cards"] == 0
    assert _row(world, got["sid"])["share_state"] == "pending"


# ── c. The Needs row ───────────────────────────────────────────────────────


def test_r8_the_holder_sees_the_needs_row_and_the_owner_never_his_own(world: _World) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        sid = await _published(a, versions=2)
        await world.act(OWEN, a, skill_id=sid, act="ask", version=1)
        return {"sid": sid, "ada": await _needs(a, ADA), "owen": await _needs(a, OWEN),
                "zed": await _needs(b, ZED)}

    got = world.run(go)
    sid = got["sid"]
    # PACKAGE_HOST_ORGS is empty here: a skill share needs no install (J2d).
    assert got["ada"]["sources"]["studio"] == "ok"
    [row] = _studio_rows(got["ada"])
    assert row["id"] == f"studio-skill-share:{sid}:1"
    assert row["kind"] == "approval" and row["act"] is None
    assert row["href"] == shell.skill_share_href(sid, 1)
    assert "Rules of round" not in json.dumps(row)
    assert got["owen"]["sources"]["studio"] == "ok" and _studio_rows(got["owen"]) == []
    assert _studio_rows(got["zed"]) == []


# ── d. The approve from the chat ───────────────────────────────────────────


def test_r8_the_holder_approves_from_the_chat_and_the_owner_cannot(world: _World) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a)
        await world.act(OWEN, a, skill_id=sid, act="ask")
        cards = len(world.card.shown)
        own = await world.act(OWEN, a, skill_id=sid, act="approve")
        own_cards = len(world.card.shown) - cards
        before = await _loads(a, MEL, sid)
        ada = await world.act(ADA, a, skill_id=sid, act="approve")
        return {"sid": sid, "own": own, "own_cards": own_cards, "before": before,
                "ada": ada, "after": await _loads(a, MEL, sid),
                "card": world.card.shown[-1]}

    got = world.run(go)
    sid = got["sid"]
    assert got["own"]["ok"] is False and got["own"]["status"] == 403
    assert got["own"]["errors"][0]["message"] == store.SELF_SHARE
    assert got["own_cards"] == 0 and got["before"] == []
    assert got["ada"]["ok"] is True and got["ada"]["visibility"] == "org"
    assert got["ada"]["shared_version"] == 1
    assert "Rules of round 1" in got["card"]["context"]
    assert got["after"] == [(sid, 1)]
    row = _row(world, sid)
    assert (row["visibility"], row["shared_version"], row["share_decided_by"]) == (
        "org", 1, ADA)


# ── e. The unshare from the chat ───────────────────────────────────────────


def test_r8_the_owner_unshares_from_the_chat(world: _World) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        sid = await _published(a)
        await world.act(OWEN, a, skill_id=sid, act="ask")
        await world.act(ADA, a, skill_id=sid, act="approve")
        loaded = await _loads(a, MEL, sid)
        gone = await world.act(OWEN, a, skill_id=sid, act="unshare")
        return {"sid": sid, "loaded": loaded, "gone": gone,
                "after": await _loads(a, MEL, sid), "owen": await _loads(a, OWEN, sid)}

    got = world.run(go)
    sid = got["sid"]
    assert got["loaded"] == [(sid, 1)]
    assert got["gone"]["ok"] is True and got["gone"]["visibility"] == "private"
    assert got["after"] == [] and got["owen"] == [(sid, 1)]
    row = _row(world, sid)
    assert (row["visibility"], row["shared_version"], row["share_state"]) == (
        "private", None, None)


# ── f. STUDIO_ORGS empty ───────────────────────────────────────────────────


def test_r8_with_studio_orgs_empty_the_tool_is_404_and_the_needs_answer_is_the_same(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        monkeypatch.setattr(get_settings(), "studio_orgs", "")
        quiet = await _needs(a, ADA)
        monkeypatch.setattr(get_settings(), "studio_orgs", "*")
        sid = await _published(a)
        await world.act(OWEN, a, skill_id=sid, act="ask")
        monkeypatch.setattr(get_settings(), "studio_orgs", "")
        cards = len(world.card.shown)
        return {"sid": sid, "quiet": quiet, "asked": await _needs(a, ADA),
                "tool": await world.act(ADA, a, skill_id=sid, act="approve"),
                "owner": await world.act(OWEN, a, skill_id=sid),
                "cards": len(world.card.shown) - cards}

    got = world.run(go)
    assert got["quiet"]["sources"]["studio"] == "absent"
    assert json.dumps(got["asked"], sort_keys=True) == json.dumps(got["quiet"], sort_keys=True)
    assert got["tool"]["ok"] is False and got["tool"]["status"] == 404
    assert got["owner"]["ok"] is False and got["owner"]["status"] == 404
    assert got["cards"] == 0
    assert _row(world, got["sid"])["share_state"] == "pending"


# ── g. The member purge ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_the_purge_keeps_a_shared_skill_and_deletes_each_other_skill(
    world: _World, role: str,
) -> None:
    """The two clauses split Owen's skills of org A on the share term. The
    superuser run bypasses RLS, so only the clauses' own org predicate keeps
    org B's rows safe there."""
    a, b = world.p.org_a, world.p.org_b
    [gone] = [r for r in _PURGE_DELETES if r.table == "studio_skills"]
    [kept] = [r for r in _PURGE_KEEPS if r.table == "studio_skills"]

    async def decided(org: str, approve: bool | None, versions: int = 1) -> str:
        sid = await _published(org, OWEN, versions)
        async with tenant_session(org) as db:
            await store.request_share(db, OWEN, sid, 1)
            if approve is True:
                await store.approve_share(db, ADA, sid, 1, can_publish=True)
            elif approve is False:
                await store.reject_share(db, ADA, sid, 1, can_publish=True)
        return sid

    async def seed() -> dict[str, str]:
        ids = {"private": await _published(a, OWEN, 2),
               "pending": await decided(a, None),
               "rejected": await decided(a, False),
               "shared": await decided(a, True, versions=2),
               "mels": await _published(a, MEL),
               "b_shared": await decided(b, True),
               "b_private": await _published(b, OWEN)}
        # The shared skill also waits on a later version: it stays (J2d limit).
        async with tenant_session(a) as db:
            await store.request_share(db, OWEN, ids["shared"], 2)
        return ids

    ids = world.run(seed)
    engine = world.p.app_url if role == "app" else world.p.admin_engine.url

    async def purge() -> tuple[int, int, int]:
        params = _purge_params(gone, uid=str(uuid.uuid4()), email=OWEN)
        dsn = engine.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn), tenant_session(a) as db:
            n_gone = (await db.execute(text(gone.count_sql), params)).scalar_one()
            n_kept = (await db.execute(text(kept.count_sql), params)).scalar_one()
            deleted = await db.execute(text(gone.delete_sql), params)
            return int(n_gone), int(n_kept), int(deleted.rowcount)

    n_gone, n_kept, deleted = asyncio.run(purge())
    assert (n_gone, n_kept, deleted) == (3, 1, 3)
    with world.p.admin_engine.connect() as c:
        left = set(c.execute(text(
            "SELECT id::text FROM studio_skills WHERE id = ANY(CAST(:i AS uuid[]))"),
            {"i": list(ids.values())}).scalars().all())
        versions = dict(c.execute(text(
            "SELECT skill_id::text, count(*) FROM studio_skill_versions "
            "WHERE skill_id = ANY(CAST(:i AS uuid[])) GROUP BY skill_id"),
            {"i": list(ids.values())}).all())
    assert left == {ids["shared"], ids["mels"], ids["b_shared"], ids["b_private"]}
    assert versions == {ids["shared"]: 2, ids["mels"]: 1, ids["b_shared"]: 1,
                        ids["b_private"]: 1}
    row = _row(world, ids["shared"])
    assert (row["visibility"], row["shared_version"], row["share_state"]) == (
        "org", 1, "pending")


# ── h. The holder's unshare from the chat (J2d follow-up) ──────────────────


def test_r8_a_holder_lists_and_ends_the_share_of_another_member_from_the_chat(
    world: _World,
) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        sid = await _published(a)
        await world.act(OWEN, a, skill_id=sid, act="ask")
        await world.act(ADA, a, skill_id=sid, act="approve")
        cards = len(world.card.shown)
        out: dict[str, Any] = {"sid": sid, "loaded": await _loads(a, MEL, sid)}
        out["listed"] = await world.act(ADA, a, act="shared")
        out["zed_list"] = await world.act(ZED, b, act="shared")
        out["mel"] = await world.act(MEL, a, skill_id=sid, act="unshare")
        out["zed"] = await world.act(ZED, b, skill_id=sid, act="unshare")
        out["refused_cards"] = len(world.card.shown) - cards
        world.card.answer = False
        out["declined"] = await world.act(ADA, a, skill_id=sid, act="unshare")
        out["after_decline"] = _row(world, sid)
        world.card.answer = True
        out["gone"] = await world.act(ADA, a, skill_id=sid, act="unshare")
        out["card"] = world.card.shown[-1]
        out["after"] = await _loads(a, MEL, sid)
        out["owen"] = await _loads(a, OWEN, sid)
        out["listed_after"] = await world.act(ADA, a, act="shared")
        out["again"] = await world.act(ADA, a, skill_id=sid, act="unshare")
        return out

    got = world.run(go)
    sid = got["sid"]
    assert got["loaded"] == [(sid, 1)]
    assert got["listed"]["ok"] is True
    assert sid in {r["skill_id"] for r in got["listed"]["shares"]}
    assert sid not in {r["skill_id"] for r in got["zed_list"]["shares"]}
    assert got["mel"]["ok"] is False and got["mel"]["status"] == 404
    assert got["zed"]["ok"] is False
    assert got["zed"]["errors"][0]["message"] == st._SHARE_NOT_SHARED
    assert got["refused_cards"] == 0
    assert got["declined"]["ok"] is False and got["declined"]["decision"] == "stopped"
    assert (got["after_decline"]["visibility"], got["after_decline"]["shared_version"]) == (
        "org", 1)
    assert got["gone"]["ok"] is True and got["gone"]["visibility"] == "private"
    assert got["gone"]["note"] == st._SHARE_DONE["end"]
    assert f"Owner: «{OWEN}»" in got["card"]["context"]
    assert "The other members stop loading it." in got["card"]["detail"]
    assert got["after"] == [] and got["owen"] == [(sid, 1)]
    row = _row(world, sid)
    assert (row["visibility"], row["shared_version"], row["share_state"],
            row["share_version"]) == ("private", None, None, 1)
    assert sid not in {r["skill_id"] for r in got["listed_after"]["shares"]}
    assert got["again"]["ok"] is False
    assert got["again"]["errors"][0]["message"] == st._SHARE_NOT_SHARED
