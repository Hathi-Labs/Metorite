"""R8: the chat tool of the pin, end to end (WS-CO CO-11b, task 3563).

Spec: ``project-docs/specs/chat_organization.md`` §18. Module:
``packages/acb_skills/acb_skills/chat_pin_tools.py``. The hermetic half is
``test_chat_pin_tool.py``. CO-11's own R8 suite is ``test_chat_pin_r8.py``,
and this file reuses its world, its chats and its read of the rows.

The tool sends its request through an ASGI transport to the REAL pin routes
of ``gateway/routes/chat.py``, which write through the REAL store to a real
Postgres, as the NON-privileged role of the phase-4 rehearsal, with two
organizations. The identity of each request comes from the headers that the
tool sent. An override of ``get_current_user`` checks the internal bearer,
takes ``X-User-Email`` as sent, and reads the org of that address from
``app_user``, as branch 1a of ``acb_auth.deps`` binds it. The real resolver
needs the identity tables of IDENTITY_CUTOVER, which this world does not
seed. A fixed user would only repeat CO-11's suite.

What this proves:

a. A member pins her own chat in a room. Only her row is written, and a
   colleague in the same room reads no pin.
b. An unpin clears her pin and keeps the pin of the colleague.
c. A run whose chat is a private chat of a colleague gets the 404 sentence,
   and no row is written.
d. An archived chat gets the 409 sentence, and no row is written.
e. A member of org B, in a run whose chat is an id of org A, gets the 404
   sentence, and no org gets a row.
f. A guest of a room (a viewer) pins only their own row.
g. An address in mixed case goes as the run holds it, and the row keys it in
   lower case.

The room cases (a, b and f) bind the memory id ``room:<thread>``, as a shared
room does.

Mutations run on 2026-11-17, each red here:

* lower the case of the header: case g gets 404,
* send the memory id in place of the member: cases a, b and f.
"""

from __future__ import annotations

from typing import Annotated

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

import httpx
from fastapi import Header  # at module level: FastAPI reads the string annotations here
from sqlalchemy import text

from tests.unit.test_chat_pin_r8 import (  # noqa: F401 (fixtures)
    _ALICE,
    _BOB,
    _CAROL,
    _DANA,
    _chat,
    _client,
    _listed,
    _rows,
    _user,
    db,
    world,
)
from tests.unit.test_chat_pin_tool import TOKEN, _async, _run
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401 (fixtures)
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ERIN = "erin.guest@co11.test"  # org A, a viewer of a room


@pytest.fixture
def tool(db, monkeypatch):  # noqa: F811 (a fixture)
    """The tool, wired to the real routes through an ASGI transport. Yields
    the list of the ``X-User-Email`` values that the routes received."""
    from acb_auth import get_current_user
    from acb_common import get_settings
    from acb_skills import chat_pin_tools as cpt
    from acb_skills import egress
    from fastapi import FastAPI
    from gateway.routes.chat import pin_session, unpin_session

    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": _ERIN, "o": db.org_a})

    seen: list[str] = []

    def identity(
        x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ):
        seen.append(str(x_user_email))
        if authorization != f"Bearer {TOKEN}" or not x_user_email:
            return _user("", None)
        with db.admin_engine.connect() as c:
            org = c.execute(text(
                "SELECT organization_id::text FROM app_user WHERE lower(email) = lower(:e)"),
                {"e": x_user_email}).scalar()
        return _user(x_user_email, org)

    app = FastAPI()
    app.put("/chat/sessions/{session_id}/pin")(pin_session)
    app.delete("/chat/sessions/{session_id}/pin")(unpin_session)
    app.dependency_overrides[get_current_user] = identity
    monkeypatch.setattr(cpt, "_transport", lambda: httpx.ASGITransport(app=app))
    monkeypatch.setattr(get_settings(), "gateway_internal_token", TOKEN, raising=False)
    monkeypatch.setattr(egress, "write_must_show_card", _async(False))
    db.seen = seen
    yield db
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": _ERIN})


async def _pin(member: str, sid: str, pinned: bool = True, *, room: bool = False) -> str:
    """Call the tool in a run of *member* in the chat *sid*. A room binds the
    memory id ``room:<thread>``, as the executor does for a shared room."""
    from acb_skills.chat_pin_tools import pin_chat

    with _run(user=member, session=sid, memory=f"room:{sid}" if room else None):
        return await pin_chat(pinned)


# ── a and b. A member and a colleague in one room ───────────────────────────


async def test_a_the_member_pins_her_own_row_and_the_colleague_reads_none(tool) -> None:
    from acb_skills.chat_pin_tools import PINNED

    sid = _chat(tool, org=tool.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    assert await _pin(_ALICE, sid, room=True) == PINNED
    assert tool.seen == [_ALICE]
    assert _rows(tool, sid) == [(tool.org_a, _ALICE, True)]
    assert _listed(_client(_user(_ALICE, tool.org_a)), sid)["pinned"] is True
    bob = _listed(_client(_user(_BOB, tool.org_a)), sid)
    assert (bob["pinned"], bob["pinnedAt"]) == (False, None)


async def test_b_an_unpin_keeps_the_pin_of_the_colleague(tool) -> None:
    from acb_skills.chat_pin_tools import PINNED, UNPINNED

    sid = _chat(tool, org=tool.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    assert _client(_user(_BOB, tool.org_a)).put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert await _pin(_ALICE, sid, room=True) == PINNED
    assert await _pin(_ALICE, sid, False, room=True) == UNPINNED
    assert _rows(tool, sid) == [(tool.org_a, _ALICE, False), (tool.org_a, _BOB, True)]
    assert _listed(_client(_user(_BOB, tool.org_a)), sid)["pinned"] is True


# ── c. A chat that the member cannot read ───────────────────────────────────


async def test_c_a_private_chat_of_a_colleague_is_404_and_writes_no_row(tool) -> None:
    from acb_skills.chat_pin_tools import NOT_FOUND

    sid = _chat(tool, org=tool.org_a, owner=_BOB)
    assert await _pin(_ALICE, sid) == NOT_FOUND
    assert await _pin(_ALICE, sid, False) == NOT_FOUND
    assert _rows(tool, sid) == []


# ── d. An archived chat ─────────────────────────────────────────────────────


async def test_d_an_archived_chat_is_409_and_writes_no_row(tool) -> None:
    sid = _chat(tool, org=tool.org_a, owner=_ALICE)
    with tool.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET archived_at = now() WHERE id = :s"), {"s": sid})
    out = await _pin(_ALICE, sid)
    assert out == "Nothing changed. This chat is archived. Restore it, then pin it."
    assert _rows(tool, sid) == []


# ── e. Another org ──────────────────────────────────────────────────────────


async def test_e_org_b_bound_to_an_id_of_org_a_is_404_in_both_orgs(tool) -> None:
    from acb_skills.chat_pin_tools import NOT_FOUND

    sid = _chat(tool, org=tool.org_a, owner=_ALICE)
    assert await _pin(_CAROL, sid) == NOT_FOUND
    assert tool.seen == [_CAROL]
    assert _rows(tool, sid) == []
    with tool.admin_engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM chat_session_member "
                           "WHERE member_email = :m"), {"m": _CAROL}).scalar()
    assert n == 0


# ── f. A guest of a room ────────────────────────────────────────────────────


async def test_f_a_guest_of_a_room_pins_only_their_own_row(tool) -> None:
    from acb_skills.chat_pin_tools import PINNED

    sid = _chat(tool, org=tool.org_a, owner=_ALICE, visibility="people")
    with tool.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :e, 'viewer', CAST(:o AS uuid))"),
            {"s": sid, "e": _ERIN, "o": tool.org_a})
    assert await _pin(_ERIN, sid, room=True) == PINNED
    assert _rows(tool, sid) == [(tool.org_a, _ERIN, True)]
    assert _listed(_client(_user(_ALICE, tool.org_a)), sid)["pinned"] is False


# ── g. An address in mixed case ─────────────────────────────────────────────


async def test_g_an_address_in_mixed_case_goes_as_held_and_keys_lower(tool) -> None:
    from acb_skills.chat_pin_tools import PINNED

    sid = _chat(tool, org=tool.org_a, owner=_DANA)
    assert await _pin(_DANA, sid) == PINNED
    assert tool.seen == [_DANA]
    assert _rows(tool, sid) == [(tool.org_a, _DANA.lower(), True)]
