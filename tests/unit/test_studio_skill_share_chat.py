"""WS-STU J2d (WS-TOF TOF-4b) — the chat tool, the owner's read and the pack
of a skill share, with no database.

Spec: ``project-docs/specs/ai_studio_rows/J2d.md``. Code:
``acb_skills.studio_tools.studio_skill_share`` (the one tool of the pack
``studio-share``) over the J2c routes of ``gateway/routes/studio/skills.py``,
and the owner's read ``GET /studio/skills/{id}/share`` (``get_skill_share``,
``skills_store.get_share``). The R8 twin is
``tests/unit/test_studio_skill_share_chat_r8.py``. The Needs row is in
``tests/unit/test_shell_needs.py`` (``TestTheStudioApprovalSource``).

**The harness.** The tool's HTTP calls reach the REAL FastAPI router of
Studio (``actions.router``) through ``httpx.ASGITransport``. The real
handlers run, and so do the real store functions. Only the session is fake:
one skill row of Owen, answered by the text of each store statement. So a
path, a verb, a status or a refusal text that the routes do not serve fails
here. The member of a request is the ``X-User-Email`` that the tool sends.

The checks:

1. The owner's ask shows ONE card and writes nothing before ``approved``.
   After ``approved`` the share reads ``pending``, and the result says that a
   holder of ``studio:publish`` must approve it.
2. Another member and an org outside ``STUDIO_ORGS`` get the 404 text, and no
   card shows.
3. A holder approves an ask of another member on a card with the whole text.
   The owner's approve is refused with the 403 text, and no card shows. A cut
   card cannot approve, and a reject still works on it.
4. The owner unshares on a card. A change while the card is open writes
   nothing.
5. The copies of the store texts and rules in the tool equal the store.
6. The pack: one tool, no surface, not in ``FOLLOWS``, and its words load it.
   The tool takes no org and no member, and its description is one sentence.

**R7.** M1: drop the card of ``_share_ask`` -> check 1 goes red. M2: drop the
owner test of ``_share_decide`` -> check 3 goes red (the store refuses too, so
the test reads that no card showed). M3: drop the ``is_cut`` refusal -> the
cut approve writes -> red. M4: add ``"studio-share": "studio"`` to ``FOLLOWS``
-> check 6 goes red.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient as _RealClient

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_skills import ask_tools, egress
from acb_skills import skill_families as sf
from acb_skills import studio_tools as st
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from fastapi import FastAPI, Request
from gateway.routes.studio import actions, skills
from gateway.studio import skills_store as store

from tests.unit._native_maf_harness import load_agent_module
from tests.unit.test_studio_builder_tools import ROWS

ROOT = Path(__file__).resolve().parents[2]
STUDIO_DIR = "apps/agents/agent-studio-builder"
ORG = str(uuid.uuid4())
OWEN = "owen@j2d.test"
ADA = "ada@j2d.test"
MEL = "mel@j2d.test"
STUDIO = ["feature:studio"]
PUBLISHER = ["feature:studio", "studio:publish"]
#: Owen holds the right too, so the refusal of his own approve is the owner
#: rule of the store and not a missing right.
GRANTS = {OWEN: PUBLISHER, ADA: PUBLISHER, MEL: STUDIO}
SID = str(uuid.uuid4())
WHEN = datetime(2026, 11, 19, tzinfo=UTC)
BODY = {"description": "Use this for quotes.",
        "instructions": "Read the price list, then fill the quote template.",
        "actions": ["tasks.list"]}


# ── the fake session: one skill row of Owen ────────────────────────────────


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _Db:
    """One skill of Owen in the bound org. It answers each store statement by
    its text, and counts the writes. A statement that it does not know fails
    the test."""

    def __init__(self, versions: int = 2) -> None:
        self.owner = OWEN
        self.name = "Quotes"
        self.bodies = {n: {**BODY, "description": f"{BODY['description']} Round {n}."}
                       for n in range(1, versions + 1)}
        self.share: dict[str, Any] = {
            "visibility": "private", "shared": None, "state": None, "version": None,
            "req_by": None, "req_at": None, "dec_by": None, "dec_at": None}
        self.writes: list[str] = []

    @property
    def current(self) -> int:
        return max(self.bodies)

    def _share_row(self) -> tuple[Any, ...]:
        s = self.share
        return (SID, self.owner, s["visibility"], s["shared"], s["state"], s["version"],
                s["req_by"], s["req_at"], s["dec_by"], s["dec_at"])

    def _skill_row(self) -> tuple[Any, ...]:
        return (SID, self.name, "quotes", "", self.owner, self.share["visibility"],
                "published", self.current, WHEN, WHEN)

    def _version_row(self, n: int) -> tuple[Any, ...]:
        body = self.bodies[n]
        return (SID, n, body, store.content_hash(body), self.owner, WHEN)

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        s = self.share
        if sql == store._PENDING_SHARES_SQL:
            if s["state"] != "pending":
                return _Result([])
            n = s["version"]
            return _Result([(SID, self.name, "quotes", "", self.owner, n, s["req_by"],
                             s["req_at"], self.bodies[n], store.content_hash(self.bodies[n]))])
        if params.get("id") != SID:
            return _Result([])
        mine = params.get("m") == self.owner
        if sql == store._GET_SQL:
            return _Result([self._skill_row()] if mine else [])
        if sql == store._GET_VERSION_SQL:
            n = params["v"] or self.current
            return _Result([self._version_row(n)] if mine and n in self.bodies else [])
        if sql in (store._SHARE_READ_SQL, store._SHARE_OWNER_SQL):
            return _Result([self._share_row()] if mine else [])
        if sql == store._SHARE_ANY_SQL:
            return _Result([self._share_row()])
        if sql == store._VERSION_EXISTS_SQL:
            return _Result([(1,)] if params["v"] in self.bodies else [])
        if sql == store._SHARE_ASK_SQL:
            s.update(state="pending", version=params["v"], req_by=params["m"], req_at=WHEN,
                     dec_by=None, dec_at=None)
        elif sql in (store._SHARE_APPROVE_SQL, store._SHARE_REJECT_SQL):
            if s["state"] != "pending" or s["version"] != params["v"]:
                return _Result([])
            if sql == store._SHARE_APPROVE_SQL:
                s.update(state="approved", visibility="org", shared=s["version"])
            else:
                s.update(state="rejected")
            s.update(dec_by=params["a"], dec_at=WHEN)
        elif sql == store._UNSHARE_SQL:
            s.update(visibility="private", shared=None,
                     state="rejected" if s["state"] == "rejected" else None)
        else:  # pragma: no cover - a statement that this fake does not know
            raise AssertionError(sql)
        self.writes.append(sql.split()[0])
        return _Result([self._share_row()])


# ── the card and the world ─────────────────────────────────────────────────


class Card:
    """A stand-in for ``request_confirmation``. It records each card and
    answers. ``between`` runs while the card is open."""

    def __init__(self) -> None:
        self.answer: Any = True
        self.open = True
        self.shown: list[dict[str, Any]] = []
        self.between: Any = None

    async def __call__(self, title: str, detail: str = "", context: str = "", **kw: Any) -> Any:
        self.shown.append({"title": title, "detail": detail, "context": context, **kw})
        if self.between:
            self.between()
        return self.answer


class World:
    def __init__(self) -> None:
        self.db = _Db()
        self.card = Card()
        self.member = OWEN
        self.sessions = 0
        self.requests: list[tuple[str, str]] = []

    def act(self, member: str, **kw: Any) -> dict[str, Any]:
        """Call the tool as *member*."""
        self.member = member
        return asyncio.run(st.studio_skill_share(**{"skill_id": SID, **kw}))

    def wrote(self) -> list[str]:
        return list(self.db.writes)


def _app(world: World) -> FastAPI:
    app = FastAPI()
    app.include_router(actions.router)

    def who(request: Request) -> UserContext:
        email = request.headers.get("x-user-email", "")
        world.requests.append((request.method, request.url.path))
        return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                           access=build_access(GRANTS.get(email, [])))

    app.dependency_overrides[get_current_user] = who
    return app


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:
    w = World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield w.db

    async def catalog(*, app: str = "") -> list[dict[str, Any]]:
        return list(ROWS)

    async def not_held(ctx: Any = None) -> bool:
        return False

    real = httpx.AsyncClient
    transport = httpx.ASGITransport(app=_app(w))
    monkeypatch.setattr(skills, "_tenant_session", session)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(st.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: w.member)
    monkeypatch.setattr(st, "_read_catalog", catalog)
    monkeypatch.setattr(egress, "write_must_show_card", not_held)
    monkeypatch.setattr(egress, "chat_is_held", not_held)
    monkeypatch.setattr(ask_tools, "request_confirmation", w.card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: w.card.open)
    with artifact_context_scope():
        bind_artifact_context(no_egress=False)
        yield w


def _pending(world: World, version: int = 2) -> None:
    world.db.share.update(state="pending", version=version, req_by=OWEN, req_at=WHEN)


def _shared(world: World, version: int = 1) -> None:
    world.db.share.update(visibility="org", shared=version, state="approved", version=version,
                          req_by=OWEN, req_at=WHEN, dec_by=ADA, dec_at=WHEN)


def _message(out: dict[str, Any]) -> str:
    return " ".join(e["message"] for e in out.get("errors") or [])


# ── 1. The owner's read and the ask ────────────────────────────────────────


def test_the_owner_reads_the_share_in_words_and_nothing_is_written(world: World) -> None:
    _shared(world, 1)
    out = world.act(OWEN)
    assert out["ok"] is True and out["visibility"] == "org" and out["shared_version"] == 1
    assert out["text"] == "Each member of the organization loads version 1."
    assert world.card.shown == [] and world.wrote() == []
    assert ("GET", f"/studio/skills/{SID}/share") in world.requests


def test_the_owner_asks_on_one_card_and_nothing_is_written_before_approved(
    world: World,
) -> None:
    world.card.answer = False
    out = world.act(OWEN, act="ask")
    assert out["ok"] is False and out["decision"] == "stopped"
    assert world.wrote() == [] and world.db.share["state"] is None
    [card] = world.card.shown
    assert card["title"] == "Share «Quotes» with the organization?"
    assert card["detail"] == "Version 2. A holder of studio:publish must approve it."
    assert card["context"].startswith(st._SHARE_ASK_NOTE)
    assert "Round 2." in card["context"]  # the text of the asked version
    assert card["non_interactive_default"] == "deny" and card["responder"] == OWEN


def test_an_approved_ask_writes_pending_and_says_a_holder_must_approve(world: World) -> None:
    out = world.act(OWEN, act="ask", version=1)
    assert out["ok"] is True and out["decision"] == "approved"
    assert out["share_state"] == "pending" and out["share_version"] == 1
    assert out["visibility"] == "private"
    assert out["note"] == st._SHARE_PENDING
    assert "holder of studio:publish" in out["note"] and "cannot approve" in out["note"]
    assert world.wrote() == ["UPDATE"]
    assert "Round 1." in world.card.shown[0]["context"]


def test_a_second_ask_of_the_same_version_shows_no_card(world: World) -> None:
    _pending(world, 2)
    out = world.act(OWEN, act="ask")
    assert out["ok"] is True and out["note"] == st._SHARE_ALREADY
    assert world.card.shown == [] and world.wrote() == []


@pytest.mark.parametrize(("state", "asked", "version"), [
    ("pending", 1, 2), ("approved", 2, 2), ("approved", 2, 1), ("rejected", 2, 2)])
def test_an_ask_that_the_route_refuses_shows_no_card(
    world: World, state: str, asked: int, version: int,
) -> None:
    world.db.share.update(state=state, version=asked)
    if state == "approved":
        world.db.share.update(visibility="org", shared=asked)
    out = world.act(OWEN, act="ask", version=version)
    assert out["ok"] is False and out["status"] == 409
    assert world.card.shown == [] and world.wrote() == []


# ── 2. Another member and an org outside STUDIO_ORGS ───────────────────────


@pytest.mark.parametrize("act", ["status", "ask", "unshare"])
def test_another_member_gets_the_404_text_and_no_card(world: World, act: str) -> None:
    _shared(world, 1)
    out = world.act(MEL, act=act)
    assert out["ok"] is False and _message(out) == st._NOT_YOURS
    assert world.card.shown == [] and world.wrote() == []


@pytest.mark.parametrize("act", ["status", "ask", "unshare", "approve", "reject"])
def test_an_org_outside_studio_orgs_gets_404_and_no_session(
    world: World, monkeypatch: pytest.MonkeyPatch, act: str,
) -> None:
    _pending(world, 2)
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    out = world.act(ADA if act in ("approve", "reject") else OWEN, act=act)
    assert out["ok"] is False and out.get("status") == 404
    assert world.sessions == 0 and world.card.shown == [] and world.wrote() == []


# ── 3. The holder's approve and reject ─────────────────────────────────────


def test_a_holder_approves_on_a_card_with_the_whole_text(world: World) -> None:
    _pending(world, 2)
    out = world.act(ADA, act="approve")
    assert out["ok"] is True and out["visibility"] == "org" and out["shared_version"] == 2
    assert out["note"] == st._SHARE_DONE["approve"].format(v=2)
    [card] = world.card.shown
    assert card["title"] == "Approve the share of «Quotes»?"
    assert f"Owner: «{OWEN}»" in card["context"]
    assert f"Hash: {store.content_hash(world.db.bodies[2])}" in card["context"]
    assert world.db.bodies[2]["instructions"] in card["context"]
    assert world.db.share["dec_by"] == ADA and world.wrote() == ["UPDATE"]


def test_the_owner_cannot_approve_and_sees_no_card(world: World) -> None:
    _pending(world, 2)
    out = world.act(OWEN, act="approve")
    assert out["ok"] is False and out["status"] == 403
    assert _message(out) == store.SELF_SHARE
    assert world.card.shown == [] and world.wrote() == []
    assert world.db.share["state"] == "pending"


def test_a_member_without_the_right_gets_the_route_text(world: World) -> None:
    _pending(world, 2)
    out = world.act(MEL, act="approve")
    assert out["ok"] is False and out["status"] == 403
    assert _message(out) == skills.NO_SHARE_LIST_RIGHT
    assert world.card.shown == [] and world.wrote() == []


def test_no_ask_of_the_skill_waits(world: World) -> None:
    out = world.act(ADA, act="approve")
    assert out["ok"] is False and _message(out) == st._SHARE_NONE
    assert world.card.shown == []


def test_a_holder_rejects_and_the_visibility_stays(world: World) -> None:
    _pending(world, 2)
    out = world.act(ADA, act="reject", version=2)
    assert out["ok"] is True and out["share_state"] == "rejected"
    assert out["visibility"] == "private"
    assert world.card.shown[0]["context"].startswith(st._SHARE_REJECT_NOTE)


def test_a_cut_card_cannot_approve_and_a_reject_still_works(world: World) -> None:
    world.db.bodies[2] = {**BODY, "instructions": "Read the list. " * 400}
    _pending(world, 2)
    out = world.act(ADA, act="approve")
    assert out["ok"] is False and _message(out) == st._SHARE_WHOLE
    assert world.card.shown == [] and world.wrote() == []
    out = world.act(ADA, act="reject")
    assert out["ok"] is True and out["share_state"] == "rejected"
    assert world.card.shown[0]["context"].rstrip().endswith(st.CUT_MARK)


def test_a_holder_who_declines_the_card_leaves_the_ask_waiting(world: World) -> None:
    _pending(world, 2)
    world.card.answer = False
    out = world.act(ADA, act="approve")
    assert out["ok"] is False and out["decision"] == "stopped"
    assert world.db.share["state"] == "pending" and world.wrote() == []


def test_an_ask_that_moved_while_the_card_was_open_writes_nothing(world: World) -> None:
    _pending(world, 2)

    def another_holder_rejects() -> None:
        world.db.share.update(state="rejected", dec_by="zoe@j2d.test")

    world.card.between = another_holder_rejects
    out = world.act(ADA, act="approve")
    assert out["ok"] is False and _message(out) == st._CHANGED_UNDER_CARD
    assert world.wrote() == [] and world.db.share["visibility"] == "private"


# ── 4. The owner's unshare ─────────────────────────────────────────────────


def test_the_owner_unshares_on_one_card(world: World) -> None:
    _shared(world, 1)
    out = world.act(OWEN, act="unshare")
    assert out["ok"] is True and out["visibility"] == "private"
    assert out["shared_version"] is None and out["note"] == st._SHARE_DONE["unshare"]
    [card] = world.card.shown
    assert card["title"] == "End the share of «Quotes»?"
    assert "Now: Each member of the organization loads version 1." in card["context"]
    assert world.wrote() == ["UPDATE"]


def test_an_unshare_of_a_private_skill_with_no_ask_shows_no_card(world: World) -> None:
    out = world.act(OWEN, act="unshare")
    assert out["ok"] is True and out["note"] == st._SHARE_NOTHING
    assert world.card.shown == [] and world.wrote() == []


def test_a_share_that_moved_while_the_unshare_card_was_open_writes_nothing(
    world: World,
) -> None:
    _pending(world, 2)
    world.card.between = lambda: world.db.share.update(state="approved", visibility="org",
                                                       shared=2)
    out = world.act(OWEN, act="unshare")
    assert out["ok"] is False and _message(out) == st._CHANGED_UNDER_CARD
    assert world.wrote() == []


@pytest.mark.parametrize(("kw", "text"), [
    ({"act": "share"}, st._SHARE_ACT_BAD), ({"act": "ask", "version": -1}, st._SHARE_VERSION_BAD),
    ({"skill_id": "nope"}, st._NO_ID)])
def test_a_bad_argument_is_refused_before_any_request(
    world: World, kw: dict[str, Any], text: str,
) -> None:
    out = world.act(OWEN, **kw)
    assert out["ok"] is False and _message(out) == text
    assert world.requests == [] and world.card.shown == []


# ── 5. The copies of the store ─────────────────────────────────────────────


class _OneRow:
    """The two reads of ``request_share``, for one share state."""

    def __init__(self, state: str | None, asked: int | None) -> None:
        self.row = (SID, OWEN, "org" if state == "approved" else "private",
                    asked if state == "approved" else None, state, asked,
                    None, None, None, None)

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        sql = str(stmt)
        if sql == store._SHARE_OWNER_SQL:
            return _Result([self.row])
        if sql == store._VERSION_EXISTS_SQL:
            return _Result([(1,)])
        return _Result([self.row])  # the ask itself


@pytest.mark.parametrize(("state", "asked"), [
    (None, None), ("pending", 1), ("pending", 2), ("approved", 1), ("approved", 2),
    ("rejected", 1), ("rejected", 2)])
@pytest.mark.parametrize("version", [1, 2, 3])
def test_the_ask_check_of_the_tool_is_the_rule_of_the_store(
    state: str | None, asked: int | None, version: int,
) -> None:
    """The tool refuses before a card only what ``request_share`` refuses, with
    its text. "" is the same pending version: the store writes nothing."""
    share = {"share_state": state, "share_version": asked}
    mine = st._ask_problem(share, version)
    try:
        asyncio.run(store.request_share(_OneRow(state, asked), OWEN, SID, version))
        theirs: str | None = None
    except store.ShareConflict as exc:
        theirs = str(exc)
    if mine == "":
        assert theirs is None and state == "pending" and asked == version
    else:
        assert mine == theirs


def test_the_copied_refusal_of_an_owner_approve_is_the_store_text() -> None:
    assert st._SHARE_SELF == store.SELF_SHARE


# ── 6. The tool and its pack ───────────────────────────────────────────────


def test_the_tool_takes_no_org_or_member_and_its_description_is_one_sentence() -> None:
    sig = inspect.signature(st.studio_skill_share)
    assert list(sig.parameters) == ["skill_id", "act", "version"]
    doc = inspect.getdoc(st.studio_skill_share) or ""
    assert "\n" not in doc and doc.endswith(".") and doc.count(". ") == 0
    assert len(doc.split()) <= 25
    schema = sf.tool_json_schema(st.studio_skill_share)["function"]
    assert schema["parameters"]["required"] == ["skill_id"]


def test_the_pack_holds_the_one_tool_and_stays_dark() -> None:
    """No surface and not in FOLLOWS, so a plain Studio turn holds no new tool,
    and the pack is off until the owner names it in TOOL_PACKS."""
    pack = tp.PACKS["studio-share"]
    assert pack.tools == ("studio_skill_share",) and pack.owner == "studio-builder"
    assert pack.surface_slugs == () and "studio-share" not in tp.FOLLOWS
    assert tp.with_followers({"studio", "studio-more"}) == {"studio", "studio-more"}
    assert pack.delegation_refusal == tp.PACKS["studio"].delegation_refusal
    named = set(re.findall(r"[a-z]+(?:_[a-z]+)+", pack.rules))
    assert named == {"studio_skill_share"}
    assert list(tp.PACKS)[-1] == "studio-share"


@pytest.mark.parametrize("words", [
    "Share my skill Quotes with the team", "Show me the skill share that waits for me",
    "unshare the skill"])
def test_the_words_load_the_pack_and_no_other_studio_pack(words: str) -> None:
    assert tp.matched_layer("studio-share", words) == "words"
    assert tp.matched_layer("studio", words) is None
    assert tp.matched_layer("studio-more", words) is None


def test_the_owner_builds_the_pack_from_its_module() -> None:
    built = tp.owner_tools("studio-share", load_agent_module(STUDIO_DIR))
    assert tuple(tool_name(fn) for fn in built) == ("studio_skill_share",)
    assert all(inspect.iscoroutinefunction(fn) for fn in built)
    cfg = json.loads((ROOT / STUDIO_DIR / "config.json").read_text(encoding="utf-8"))
    assert "studio_skill_share" in cfg["own_tool_scope"]


# ── 7. The owner's read route ──────────────────────────────────────────────


def test_the_owners_read_route_is_404_for_another_member(world: World) -> None:
    async def read(member: str) -> httpx.Response:
        async with _RealClient(transport=httpx.ASGITransport(app=_app(world)),
                               base_url="http://gw") as client:
            return await client.get(f"/studio/skills/{SID}/share",
                                    headers={"X-User-Email": member})

    _pending(world, 2)
    mine = asyncio.run(read(OWEN))
    assert mine.status_code == 200 and mine.json()["share_state"] == "pending"
    theirs = asyncio.run(read(ADA))
    assert (theirs.status_code, theirs.json()) == (404, {"detail": "Not found"})
    assert world.wrote() == []
