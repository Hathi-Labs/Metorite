"""WS-55 SW-7 — one key for each MCP session (spec §12.5).

Database-free, through the real edge app, with the fake token store and the
fake gateway of ``test_mcp_edge_writes.py``. The gateway half (branch 0 reads
the key only from the signed header) is in ``test_mcp_edge_signing.py``, and
the gate and the R8 half are in ``test_agent_run_exclusive.py``.

Run::

    uv run pytest tests/unit/test_mcp_edge_session.py -v -rs

**The mutations (R7).** ``S`` is ``packages/acb_auth/acb_auth/agent_signing.py``
and ``M`` is ``apps/services/agent_edge/agent_edge/mcp.py``.

* V1. ``S`` ``_session_mac``: leave the connection out of the message.
  ``test_v1_a_session_id_binds_its_connection``.
* V2. ``S`` ``read_mcp_session``: return the key part with no HMAC check.
  ``test_v2_a_changed_or_made_up_id_does_not_verify``.
* V3. ``M`` ``_session_of``: answer a bad id with no session instead of 404.
  ``test_v3_an_id_that_does_not_verify_is_404_with_no_gateway_call``.
* V4. ``M`` ``_dispatch``: build the tool ``Context`` with no session.
  ``test_v4_a_call_in_a_session_signs_its_key_into_the_gateway_call``.
* V5. ``M`` ``_dispatch``: no ``Mcp-Session-Id`` on ``initialize``.
  ``test_v5_initialize_starts_a_new_session_each_time``.
* F1. ``M`` ``_dispatch``: mint the id with no check of the flag.
  ``test_f1_with_the_flag_off_initialize_mints_no_id``.
* F2. ``M`` ``_session_of``: read the id with no check of the flag.
  ``test_f2_with_the_flag_off_an_id_is_no_session_and_never_a_404``.

**Dark (spec §12.5).** The sessions ship behind ``agent_mcp_sessions_enabled``,
default off. :class:`TestTheTransport` turns it on. :class:`TestTheFlagIsOff`
proves that, off, the edge behaves exactly as before SW-7.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_signing
from acb_common import get_settings
from agent_edge import app as edge_app_module
from agent_edge import mcp

from tests.unit.test_mcp_edge_writes import (  # noqa: F401 — fixtures
    CONN,
    GOOD,
    ISSUER,
    KEY,
    ORG,
    TASK,
    VIA,
    _call,
    _rpc,
    edge,
)

OTHER_CONN = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
OTHER_KEY = "another-signing-key-" + "o" * 24


async def _send(message: dict[str, Any], *, session: str | list[str] | None = None,
                token: str = GOOD) -> httpx.Response:
    headers = [("authorization", f"Bearer {token}"), ("content-type", "application/json"),
               ("mcp-protocol-version", "2025-11-25")]
    for value in ([session] if isinstance(session, str) else session or []):
        headers.append((mcp.SESSION_HEADER, value))
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=ISSUER) as client:
        return await client.post("/mcp", content=json.dumps(message), headers=headers)


async def _initialize() -> str:
    r = await _send(_rpc("initialize", {"protocolVersion": "2025-11-25"}))
    assert r.status_code == 200, r.text
    return r.headers[mcp.SESSION_HEADER]


class TestTheSessionId:

    def test_v1_a_session_id_binds_its_connection(self) -> None:
        sid = agent_signing.mint_mcp_session(KEY, CONN)
        key = agent_signing.read_mcp_session(sid, CONN, [KEY])
        assert key is not None and agent_signing.session_key_is_well_formed(key)
        assert sid.split(".")[1] == key
        assert agent_signing.read_mcp_session(sid, OTHER_CONN, [KEY]) is None

    def test_v2_a_changed_or_made_up_id_does_not_verify(self) -> None:
        sid = agent_signing.mint_mcp_session(KEY, CONN)
        version, key, mac = sid.split(".")
        other_key = ("B" if key[0] != "B" else "C") + key[1:]
        for bad in (f"{version}.{other_key}.{mac}", f"{version}.{key}.{'0' * 64}",
                    f"s2.{key}.{mac}", f"{version}.{key}", "", "s1..", sid + ".x"):
            assert agent_signing.read_mcp_session(bad, CONN, [KEY]) is None, bad
        assert agent_signing.read_mcp_session(sid, CONN, [OTHER_KEY]) is None

    def test_a_rotation_keeps_the_ids_of_the_previous_key(self) -> None:
        sid = agent_signing.mint_mcp_session(OTHER_KEY, CONN)
        assert agent_signing.read_mcp_session(sid, CONN, [KEY, OTHER_KEY]) is not None

    def test_two_ids_never_share_a_key(self) -> None:
        keys = {agent_signing.mint_mcp_session(KEY, CONN).split(".")[1] for _ in range(50)}
        assert len(keys) == 50


@pytest.fixture
def sessions_on(edge, monkeypatch):  # noqa: F811 — a fixture
    monkeypatch.setattr(get_settings(), "agent_mcp_sessions_enabled", True)
    return edge


class TestTheFlagIsOff:
    """The default. The edge mints no id, reads no id and signs no session,
    so every client sees the transport of before SW-7."""

    def test_the_flag_defaults_off(self) -> None:
        from acb_common.settings import Settings

        assert Settings.model_fields["agent_mcp_sessions_enabled"].default is False
        assert agent_oauth.mcp_sessions_enabled() is False

    async def test_f1_with_the_flag_off_initialize_mints_no_id(self, edge):  # noqa: F811
        r = await _send(_rpc("initialize", {"protocolVersion": "2025-11-25"}))
        assert r.status_code == 200, r.text
        assert mcp.SESSION_HEADER.lower() not in {k.lower() for k in r.headers}

    async def test_f2_with_the_flag_off_an_id_is_no_session_and_never_a_404(
            self, edge):  # noqa: F811
        """A client may hold an id from a time the flag was on. It gets the
        answer of before SW-7, never a 404, and no session is signed."""
        good = agent_signing.mint_mcp_session(KEY, CONN)
        for sent in (good, agent_signing.mint_mcp_session(OTHER_KEY, CONN),
                     "s1.x.y", [good, good]):
            r = await _send(_call("get_task", {"task_id": TASK}), session=sent)
            assert r.status_code == 200, (sent, r.text)
        assert edge.gateway.requests
        assert all("x-agent-session" not in q.headers for q in edge.gateway.requests)


class TestTheTransport:

    async def test_v5_initialize_starts_a_new_session_each_time(self, edge, sessions_on):  # noqa: F811
        first, second = await _initialize(), await _initialize()
        assert first != second
        for sid in (first, second):
            assert agent_signing.read_mcp_session(sid, CONN, [KEY]) is not None
        # An id sent on `initialize` is not read: the answer starts a new one.
        r = await _send(_rpc("initialize", {"protocolVersion": "2025-11-25"}),
                        session="not-an-id")
        assert r.status_code == 200 and r.headers[mcp.SESSION_HEADER] not in (first, second)

    async def test_v4_a_call_in_a_session_signs_its_key_into_the_gateway_call(
            self, edge, sessions_on):  # noqa: F811
        sid = await _initialize()
        key = agent_signing.read_mcp_session(sid, CONN, [KEY])
        r = await _send(_call("get_task", {"task_id": TASK}), session=sid)
        assert r.status_code == 200, r.text
        sent = [q for q in edge.gateway.requests if q.path == f"/projects/tasks/{TASK}"]
        assert sent, edge.gateway.requests
        headers = sent[0].headers
        assert headers["x-agent-session"] == key
        canonical = agent_signing.canonical_string(
            timestamp=headers["x-agent-timestamp"], organization_id=ORG,
            connection_id=CONN, actor_via=VIA, method="GET", path=sent[0].path,
            query=sent[0].query, session=key)
        assert agent_signing.verify(headers["x-agent-signature"], canonical,
                                    {agent_signing.key_id(KEY): KEY})

    async def test_a_call_with_no_id_sends_no_session(self, edge, sessions_on):  # noqa: F811
        r = await _send(_call("get_task", {"task_id": TASK}))
        assert r.status_code == 200, r.text
        assert all("x-agent-session" not in q.headers for q in edge.gateway.requests)

    async def test_v3_an_id_that_does_not_verify_is_404_with_no_gateway_call(
            self, edge, sessions_on):  # noqa: F811
        for bad in (agent_signing.mint_mcp_session(KEY, OTHER_CONN),
                    agent_signing.mint_mcp_session(OTHER_KEY, CONN), "s1.x.y"):
            r = await _send(_call("get_task", {"task_id": TASK}), session=bad)
            assert r.status_code == 404, (bad, r.text)
            assert r.json()["error"]["code"] == -32600
        assert edge.gateway.requests == []

    async def test_two_ids_are_400(self, edge, sessions_on):  # noqa: F811
        sid = await _initialize()
        r = await _send(_call("get_task", {"task_id": TASK}), session=[sid, sid])
        assert r.status_code == 400
        assert edge.gateway.requests == []
