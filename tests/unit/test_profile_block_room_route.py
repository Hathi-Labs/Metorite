"""The web chat route gives the run the room bit of the server's room (WS-63 PZ-2).

Spec: ``project-docs/specs/personalization.md`` §5.1 and §7 (D-PZ-3). The
member's profile block goes only into a solo room. A room of one that other
members can read (visibility ``org`` or ``people``) is not solo, so the route
gives the run ``solo_room=False`` there. R7 fence ``profile-block-solo-plumbing``,
the behaviour half. The AST half and the helper are in
``tests/unit/test_executor_profile_block.py``.

The route is the REAL ``run_agent_stream_endpoint``, driven through FastAPI
with the edges of ``test_steer_follow_up.py`` faked. Hermetic: no SQL.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.test_steer_follow_up import _ALICE, _body, _post, world  # noqa: F401


def _room(visibility: str, members: list[str]) -> Any:
    from gateway.rooms import RoomAccess

    return RoomAccess(
        session_id="t-room", email=_ALICE, role="owner", can_read=True,
        can_send=True, can_cancel=True, can_invite=True, can_manage=True,
        is_shared=len(members) > 1, members=members, visibility=visibility,
    )


@pytest.mark.parametrize(("visibility", "members", "solo"), [
    ("private", [_ALICE], True),
    ("org", [_ALICE], False),
    ("people", [_ALICE], False),
    ("private", [_ALICE, "bo@steer.test"], False),
])
def test_the_route_gives_the_run_the_room_bit(
    world, monkeypatch, visibility, members, solo,  # noqa: F811
) -> None:
    import orchestrator.executor as executor
    from gateway.routes import agent

    seen: list[Any] = []

    async def _gen():
        yield "unused"

    def _run_agent_stream(agent_name: str, payload: dict[str, Any], **kw: Any):
        seen.append(kw.get("solo_room", "absent"))
        return _gen()

    room = _room(visibility, members)
    monkeypatch.setattr(agent, "_resolve_room", lambda *_a, **_k: room)
    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    code, _ = _post(_body("t-room", "Hello"))
    assert code == 200
    assert seen == [solo]
