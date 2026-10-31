"""A run door takes the member from the session, never from the body. H-337.

R7 fence ``identity-claims-stripped`` (SAFETY, ``tests/fences.json``).

The executor binds the run's memory id and the acting user of the
gateway-calling tools from ``user_email`` or ``user_id`` in the payload
(``executor._bind_run_identity``). Before this fix, a member who sent
``user_email=<another member>`` bound the run to that member, in a solo chat
too. In a shared room, the claim also replaced the ``room:<thread>`` id that
the route sets. ``routes/agent.py::_drop_identity_claims`` removes the claim
at each run door that holds a member's session.

The stream route is the REAL ``run_agent_stream_endpoint``, driven through
FastAPI with the edges of ``test_steer_follow_up.py`` faked. The memory id
check calls the REAL ``_bind_run_identity`` in the route's own context.

Mutations this file catches:

* the helper is a no-op: tests 1, 2, 3 and 5 fail;
* the stream route does not call it: tests 1 to 3 and the AST guard fail;
* a sync or async route does not call it: test 5 and the AST guard fail;
* the stream route keeps an own claim (``keep_own=True``): test 2 (own)
  and the AST guard fail;
* the helper removes a service caller's claim: test 4 fails;
* a kept claim keeps the body's padded or other-case text: test 5 fails;
* the executor reads a person key that ``_IDENTITY_CLAIMS`` lacks: the
  source scan fails.

Hermetic: no SQL, so no database (R8 does not apply).
"""
from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_steer_follow_up import (  # noqa: F401
    _ALICE,
    _ORG,
    _body,
    _post,
    world,
)

_BOB = "bob@steer.test"
_SERVICE = "system:internal"
_AGENT_PY = (
    Path(__file__).resolve().parents[2]
    / "apps/services/gateway/gateway/routes/agent.py"
)


def _room(members: list[str]) -> Any:
    from gateway.rooms import RoomAccess

    return RoomAccess(
        session_id="t-room", email=_ALICE, role="owner", can_read=True,
        can_send=True, can_cancel=True, can_invite=True, can_manage=True,
        is_shared=len(members) > 1, members=members, visibility="private",
    )


def _user(email: str | None) -> Any:
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=_ORG)


@pytest.fixture
def bound(world, monkeypatch):  # noqa: F811
    """An executor stub that binds the run's identity as the real one does.

    It records the raw payload, the memory id that the route set, and the
    memory id after the REAL ``_bind_run_identity`` runs in the same context.
    """
    import orchestrator.executor as executor

    real_bind = executor._bind_run_identity
    real_unbind = executor._unbind_run_identity
    seen: list[dict[str, Any]] = []

    async def _gen():
        yield "unused"

    def _run_agent_stream(agent_name: str, payload: dict[str, Any], **_k: Any):
        from acb_skills.memory_tools import _get_memory_user_id

        route_id = _get_memory_user_id()
        binding = real_bind(payload, agent_name)
        try:
            run_id = _get_memory_user_id()
        finally:
            real_unbind(binding)
        seen.append({
            "payload": copy.deepcopy(payload),
            "route_id": route_id,
            "run_id": run_id,
        })
        return _gen()

    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    return seen


def _with_claim(thread: str, **claim: str) -> dict[str, Any]:
    body = _body(thread, "Hello")
    body["payload"].update(claim)
    return body


# ── 1. The stream route ─────────────────────────────────────────────────────

def test_a_chat_body_cannot_carry_an_identity_claim_to_the_executor(world):  # noqa: F811
    code, _ = _post(_with_claim("t-solo", user_email=_BOB, user_id=_BOB))
    assert code == 200
    assert len(world.payloads) == 1
    assert "user_email" not in world.payloads[0]
    assert "user_id" not in world.payloads[0]


@pytest.mark.parametrize("claim", [
    {"user_email": _ALICE},
    {"user_email": _BOB},
    {"user_id": _BOB},
], ids=["own", "other", "other-as-user-id"])
def test_a_room_run_keeps_the_room_memory_id_when_the_body_names_a_member(
    bound, monkeypatch, claim,
):
    from gateway.routes import agent

    monkeypatch.setattr(agent, "_resolve_room", lambda *_a, **_k: _room([_ALICE, _BOB]))
    code, _ = _post(_with_claim("t-room", **claim))
    assert code == 200
    assert len(bound) == 1
    # The own address goes too. A room run that bound it would read and
    # write the typer's private memory in a room that others read.
    assert not set(claim) & set(bound[0]["payload"])
    assert bound[0]["route_id"] == "room:t-room"
    assert bound[0]["run_id"] == "room:t-room"


def test_a_solo_run_keeps_the_session_member_when_the_body_names_another(
    bound, monkeypatch,
):
    from gateway.routes import agent

    monkeypatch.setattr(agent, "_resolve_room", lambda *_a, **_k: _room([_ALICE]))
    code, _ = _post(_with_claim("t-solo", user_email=_BOB, user_id=_BOB))
    assert code == 200
    assert len(bound) == 1
    assert bound[0]["route_id"] == _ALICE
    assert bound[0]["run_id"] == _ALICE


def test_a_service_caller_keeps_its_claim(world):  # noqa: F811
    from gateway.routes.agent import _drop_identity_claims

    code, _ = _post(
        _with_claim("t-svc", user_email=_ALICE, user_id=_ALICE), email=_SERVICE,
    )
    assert code == 200
    assert world.payloads[0]["user_email"] == _ALICE
    assert world.payloads[0]["user_id"] == _ALICE

    # A caller with no email at all is a service caller too.
    for email in (None, "", _SERVICE):
        payload = {"user_email": _ALICE, "user_id": _BOB}
        _drop_identity_claims(payload, _user(email))
        assert payload == {"user_email": _ALICE, "user_id": _BOB}


def test_the_web_chat_payload_shape_is_untouched(world):  # noqa: F811
    from gateway.routes.agent import _drop_identity_claims

    sent = {
        "mode": "chat", "message": "Hello",
        "messages": [{"role": "user", "content": "Earlier"}],
        "think_mode": "auto",
    }
    body = _body("t-web", "Hello")
    body["payload"] = copy.deepcopy(sent)
    code, _ = _post(body)
    assert code == 200
    for key, value in sent.items():
        assert world.payloads[0][key] == value

    payload = copy.deepcopy(sent)
    _drop_identity_claims(payload, _user(_ALICE))
    assert payload == sent


# ── 2. The sync and async routes ────────────────────────────────────────────

@pytest.fixture
def run_doors(monkeypatch) -> list[dict[str, Any]]:
    """``/agent/run`` and ``/agent/run/async`` with the session checks stubbed."""
    import orchestrator.executor as executor
    from gateway.routes import agent

    seen: list[dict[str, Any]] = []

    async def _allowed(*_a: Any, **_k: Any) -> None:
        return None

    async def _run_agent(_agent: str, payload: dict[str, Any], **_k: Any):
        seen.append(copy.deepcopy(payload))
        return {"result": "ok"}

    monkeypatch.setattr(agent, "_resolve_agent_for_run", lambda *_a, **_k: "orchestrator")
    monkeypatch.setattr(agent, "assert_can_run_agent_in_session", _allowed)
    monkeypatch.setattr(agent, "_guard_run_thread", _allowed)
    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _allowed)
    monkeypatch.setattr(executor, "run_agent", _run_agent)
    return seen


def _call(route: str, payload: dict[str, Any]) -> None:
    """Call a run route the way FastAPI does, as Alice."""
    from fastapi import BackgroundTasks
    from gateway.routes import agent

    req = agent.AgentRunRequest(agent="orchestrator", thread_id="t-api", payload=payload)
    user = _user(_ALICE)

    async def _go() -> None:
        if route == "sync":
            await agent.run_agent_sync(req, user=user)
            return
        tasks = BackgroundTasks()
        await agent.run_agent_async(req, tasks, user=user)
        await tasks()

    asyncio.run(_go())


@pytest.mark.parametrize("route", ["sync", "async"])
@pytest.mark.parametrize(("claim", "kept"), [
    ({"user_email": _BOB, "user_id": _BOB}, {}),
    ({"user_email": _ALICE, "user_id": _BOB}, {"user_email": _ALICE}),
    ({"user_email": _ALICE.upper()}, {"user_email": _ALICE}),
    ({"user_email": f"  {_ALICE.upper()} "}, {"user_email": _ALICE}),
    ({"user_id": f" {_ALICE.upper()}"}, {"user_id": _ALICE}),
    ({"user_id": _ALICE}, {"user_id": _ALICE}),
    ({}, {}),
], ids=[
    "other", "own-and-other", "own-in-another-case", "own-padded-upper-case",
    "own-padded-as-user-id", "own-as-user-id", "none",
])
def test_sync_and_async_routes_drop_a_claim_naming_another_member(
    run_doors, route, claim, kept,
):
    _call(route, {"message": "hi", **claim})
    # An own claim stays under its key, and no claim gets no claim: these
    # routes set no memory id, so the claim is an API caller's only acting
    # user. A kept claim holds EXACTLY the session's address, so a padded or
    # other-case copy never becomes the memory id or the instance key.
    assert run_doors == [{"message": "hi", **kept}]


# ── 3. The AST guard ────────────────────────────────────────────────────────

def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _calls(fn: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _callee(n) == name]


@pytest.mark.parametrize(("door", "later", "keep_own"), [
    ("run_agent_stream_endpoint",
     ("run_agent_stream", "prompt_to_save", "_route_incoming_turn"), False),
    ("run_agent_sync", ("run_agent",), True),
    ("run_agent_async", ("run_agent",), True),
])
def test_each_run_door_drops_the_claims_before_the_executor_call(door, later, keep_own):
    tree = ast.parse(_AGENT_PY.read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == door
    )
    drops = _calls(fn, "_drop_identity_claims")
    assert len(drops) == 1, f"{door} must call _drop_identity_claims once"
    drop = drops[0]
    # A statement of the function body, so no branch can skip it.
    assert any(isinstance(s, ast.Expr) and s.value is drop for s in fn.body), (
        f"{door}: the call must not sit inside a branch"
    )
    assert ast.unparse(drop.args[0]) == "req.payload"
    keywords = {k.arg: k.value for k in drop.keywords}
    got = keywords.get("keep_own")
    assert (isinstance(got, ast.Constant) and got.value is True) is keep_own
    for name in later:
        sites = _calls(fn, name)
        assert sites, f"{door} no longer calls {name}"
        assert all(drop.lineno < s.lineno for s in sites), (
            f"{door}: the claim must go before {name}"
        )


# ── 4. The source scan of the executor ──────────────────────────────────────

_EXECUTOR_PY = (
    Path(__file__).resolve().parents[2]
    / "apps/services/orchestrator/orchestrator/executor.py"
)

# `_IDENTITY_CLAIMS` is a hand-kept list. When the executor starts to read a
# new person key from the payload, a body could name a member through that
# key, and no door would remove it. This scan makes that change fail here.
# Its reach is narrow: `executor.py` only, reads through the name
# `event_payload` only, constant keys only, and only the keys in
# `_PERSON_KEYS`. A read in another module, or of a key outside this list,
# passes. Widen the list when a new person key appears anywhere.
_PERSON_KEYS = frozenset({
    "user_email", "user_id", "member", "member_email", "actor", "email",
    "on_behalf_of", "session_user",
})


def _is_payload(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "event_payload"


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _payload_keys_read(source: str) -> set[str]:
    """Each key in ``event_payload.get("k")`` or ``event_payload["k"]``."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and _is_payload(node.func.value)
            and node.args and _is_str(node.args[0])
        ):
            keys.add(node.args[0].value)
        elif isinstance(node, ast.Subscript) and _is_payload(node.value) and _is_str(node.slice):
            keys.add(node.slice.value)
    return keys


def _person_keys_not_dropped(source: str) -> set[str]:
    from gateway.routes.agent import _IDENTITY_CLAIMS

    return (_payload_keys_read(source) & _PERSON_KEYS) - set(_IDENTITY_CLAIMS)


def test_each_person_key_the_executor_reads_is_a_claim_the_doors_drop():
    source = _EXECUTOR_PY.read_text(encoding="utf-8")
    # The scan finds the two claims of today, so it is not empty by accident.
    assert {"user_email", "user_id"} <= _payload_keys_read(source)
    assert _person_keys_not_dropped(source) == set()


def test_the_source_scan_names_a_new_person_key():
    source = _EXECUTOR_PY.read_text(encoding="utf-8") + (
        "\n\ndef _probe(event_payload):\n"
        "    return event_payload.get(\"member_email\"), event_payload[\"actor\"]\n"
    )
    assert _person_keys_not_dropped(source) == {"member_email", "actor"}
