"""R8: the chat-files tools, end to end (PAR-11c, task 4014).

Spec: ``project-docs/specs/chat_parity.md``, wave P-7. Module:
``packages/acb_skills/acb_skills/chat_workspace_tools.py``. The hermetic half
is ``test_chat_workspace_tools.py``. H-201's suite
``test_h201_readers_under_rls.py`` holds the routes alone, and this file
reuses its world: a scratch disk, one app folder for each org, and a chat of
Alice whose workspace is the app folder of org A.

Each tool sends its request through an ASGI transport to the REAL workspace
router, which reads ``chat_session`` and the room in a real Postgres, as the
NON-privileged role of the phase-4 rehearsal, with two organizations. The
blob-store mirror is the only stub, as in H-201's suite. An override of
``get_current_user`` checks the internal bearer, takes ``X-User-Email`` as the
tool sent it, and reads the org of that address from ``app_user``, the idiom
of ``test_chat_pin_tool_r8.py``.

What this proves:

a. Alice lists the files of her own chat, and then saves, moves and deletes
   them, each after one card. The disk changes as the card said.
b. Bob, of the same org, in a run bound to the id of Alice's chat, lists no
   file, gets no card for a write, and the disk does not change.
c. Carol, of org B, gets the same answer as Bob.

Mutation to run with a database: send the memory id in place of the member.
Cases a, b and c then send the wrong address.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

import httpx
from fastapi import Header  # at module level: FastAPI reads the string annotations here
from sqlalchemy import text

from tests.unit.test_chat_workspace_tools import TOKEN, _Card, _run
from tests.unit.test_chat_write_under_rls import (  # noqa: F401 (fixtures)
    _ALICE,
    _BOB,
    _CAROL,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401 (fixtures)
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_readers_under_rls import _seed_session, roots  # noqa: F401

pytestmark = _DB_GATE


@pytest.fixture
def world(graph_as_app, roots, monkeypatch):  # noqa: F811 (fixtures)
    """The tools, wired to the real workspace router. Alice's chat holds one
    output and one upload."""
    from acb_auth import get_current_user
    from acb_common import get_settings
    from acb_skills import chat_workspace_tools as cwt
    from fastapi import FastAPI
    from gateway.routes import workspace

    async def _no_store(*_a: Any, **_k: Any) -> bool:
        return False

    for name in ("_mirror_gateway_write", "_mirror_gateway_delete", "_faultin_from_store"):
        monkeypatch.setattr(workspace, name, _no_store)

    seen: list[str] = []
    admin = graph_as_app.admin_engine

    def identity(
        x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ):
        seen.append(str(x_user_email))
        if authorization != f"Bearer {TOKEN}" or not x_user_email:
            return _user("", None)
        with admin.connect() as c:
            org = c.execute(text(
                "SELECT organization_id::text FROM app_user WHERE lower(email) = lower(:e)"),
                {"e": x_user_email}).scalar()
        return _user(x_user_email, org)

    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = identity
    monkeypatch.setattr(cwt, "_transport", lambda: httpx.ASGITransport(app=app))
    monkeypatch.setattr(get_settings(), "gateway_internal_token", TOKEN, raising=False)

    (roots.app_a / "outputs").mkdir()
    (roots.app_a / "outputs" / "note.md").write_text("# Alice", encoding="utf-8")
    (roots.app_a / "inputs").mkdir()
    (roots.app_a / "inputs" / "spec.txt").write_text("spec", encoding="utf-8")
    sid = _seed_session(graph_as_app, graph_as_app.org_a, _ALICE, str(roots.app_a))
    graph_as_app.sid, graph_as_app.seen, graph_as_app.roots = sid, seen, roots
    return graph_as_app


def _approve(monkeypatch: pytest.MonkeyPatch) -> _Card:
    from acb_skills import ask_tools

    card = _Card(True)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    return card


# ── a. Alice, in her own chat ───────────────────────────────────────────────


async def test_a_the_member_lists_saves_moves_and_deletes_her_own_files(
    world, monkeypatch,
) -> None:
    from acb_skills import chat_workspace_tools as cwt

    app = world.roots.app_a
    card = _approve(monkeypatch)
    with _run(user=_ALICE, session=world.sid):
        listed = await cwt.list_chat_files()
        assert listed["ok"] is True, listed
        assert "outputs/note.md" in listed["files"] and "inputs/spec.txt" in listed["files"]
        assert str(app) not in listed["files"]

        saved = await cwt.edit_chat_file("outputs/note.md", "# Alice, new")
        assert saved["ok"] is True, saved
        assert (app / "outputs" / "note.md").read_text(encoding="utf-8") == "# Alice, new"

        moved = await cwt.promote_chat_file("inputs/spec.txt")
        assert moved["ok"] is True, moved
        assert not (app / "inputs" / "spec.txt").exists()
        assert (app / "agent-data" / "spec.txt").read_text(encoding="utf-8") == "spec"

        gone = await cwt.delete_chat_file("outputs/note.md")
        assert gone["ok"] is True, gone
        assert not (app / "outputs" / "note.md").exists()
    assert len(card.calls) == 3
    assert {k["responder"] for _a, k in card.calls} == {_ALICE}
    assert set(world.seen) == {_ALICE}


# ── b and c. Another member, in the same org and in another org ─────────────


@pytest.mark.parametrize("who", [_BOB, _CAROL])
async def test_bc_another_member_bound_to_her_chat_reaches_no_file(
    world, monkeypatch, who: str,
) -> None:
    from acb_skills import chat_workspace_tools as cwt

    app = world.roots.app_a
    card = _approve(monkeypatch)
    with _run(user=who, session=world.sid):
        listed = await cwt.list_chat_files()
        assert listed == {"ok": True, "total": 0, "note": cwt._NO_FILES}, listed
        for out in (await cwt.edit_chat_file("outputs/note.md", "forged"),
                    await cwt.promote_chat_file("inputs/spec.txt"),
                    await cwt.delete_chat_file("outputs/note.md")):
            assert out == {"ok": False, "error": cwt._NOT_IN_LIST}, out
    assert card.calls == []
    assert set(world.seen) == {who}
    assert (app / "outputs" / "note.md").read_text(encoding="utf-8") == "# Alice"
    assert (app / "inputs" / "spec.txt").is_file()
    assert not (app / "agent-data" / "spec.txt").exists()
