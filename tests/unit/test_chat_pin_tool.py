"""CO-11b: the chat tool of the pin (WS-CO, task 3563), with no database.

Spec: ``project-docs/specs/chat_organization.md`` §18. Module:
``packages/acb_skills/acb_skills/chat_pin_tools.py``. The R8 half is
``test_chat_pin_tool_r8.py``. CO-11 (task 3510) built the routes and the
store, and ``test_chat_pin.py`` holds them.

Fences (R7):

* ``chat-pin-tool-this-chat``: the tool takes one parameter, ``pinned``. It
  sends PUT or DELETE ``/chat/sessions/<the session of the run>/pin``, with
  no body, and with the bearer, ``X-User-Email`` and the content type only.
* ``chat-pin-tool-member``: ``X-User-Email`` is the verified member of the
  run binding, as the run holds it. It is never the memory id, and never an
  address in another case.
* ``chat-pin-tool-refuses``: with no verified member, with two members that
  differ, with no chat, in a batch run and on WhatsApp, the tool refuses and
  sends nothing.
* ``chat-pin-tool-card``: where ``write_must_show_card`` says so, one card
  bound to the member shows first, and only an approve sends.
* ``chat-pin-tool-dark``: the orchestrator holds the tool only for an org on
  ``CHAT_PIN_TOOL_ORGS`` AND on ``CHAT_PIN_ORGS``, and a fault reads as off.
  The owner gave the tool its own flag on 2026-11-21 (task 3563). So the pin
  of the chat list alone adds no tool and moves no count.
* ``chat-pin-tool-parity``: the two pin actions are ``COVERED`` by the tool,
  and the debt ceiling of the chat went down by two (30 to 28 after CO-2b).

Mutations run on 2026-11-17, each red here:

* read ``_get_memory_user_id`` for the header,
* lower the case of the address,
* skip the card,
* give the build the tool with the flag off.

Mutations run on 2026-11-21, each red here:

* drop the test of ``pin_allowed`` from ``pin_tool_allowed``,
* point ``agents._chat_pin_enabled`` back at ``pin_allowed``.

The other refusals and statuses have a case each, by design: a chat id from
an argument, a dropped batch, WhatsApp or member check, and a success on a
200 of the wrong state.
"""

from __future__ import annotations

import contextlib
import inspect
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from acb_common import assistant_profile as ap
from acb_common import bind_run_context, get_settings, run_context_scope
from acb_skills import ask_tools, egress
from acb_skills import chat_parity as cp
from acb_skills import chat_pin_tools as cpt
from acb_skills.tool_annotations import _H236_UNLISTED, TOOL_ANNOTATIONS, risk_summary_block
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

MEMBER = "Ana.Mixed@co11b.test"
OTHER = "bo@co11b.test"
SID = "co11b/one chat"
PATH = "/chat/sessions/co11b%2Fone%20chat/pin"
TOKEN = "internal-token-co11b"
IN = "aaaaaaaa-1111-4111-8111-111111111111"
OUT = "bbbbbbbb-2222-4222-8222-222222222222"
#: The headers that httpx adds to each request by itself.
HTTPX_OWN = {"host", "accept", "accept-encoding", "connection", "user-agent", "content-length"}
EM_DASH = chr(0x2014)


class _Gateway:
    """A gateway that records each request and answers one status."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body: Any = "auto"
        self.raises: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        if self.body == "auto":
            body: Any = {"ok": True, "pinned": request.method == "PUT", "pinnedAt": None}
        else:
            body = self.body
        if isinstance(body, str):
            return httpx.Response(self.status, text=body)
        return httpx.Response(self.status, json=body)


def _async(value: Any):
    async def _fn(*_a: Any, **_k: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    return _fn


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> _Gateway:
    gw = _Gateway()
    monkeypatch.setattr(cpt, "_transport", lambda: httpx.MockTransport(gw.handler))
    monkeypatch.setattr(get_settings(), "gateway_internal_token", TOKEN, raising=False)
    monkeypatch.setattr(egress, "write_must_show_card", _async(False))
    return gw


@contextlib.contextmanager
def _run(*, user: str = MEMBER, verified: bool = True, member: str | None = None,
         session: str = SID, batch: bool = False, memory: str | None = None) -> Iterator[None]:
    """The binding of one run: the run context, the artifact context, and the
    memory id."""
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id

    with run_context_scope(), artifact_context_scope():
        bind_run_context(run_id="r-co11b", user=user or None, member_verified=verified)
        bind_artifact_context(session_id=session, member=user if member is None else member,
                              batch_thread=batch, agent_name="orchestrator", run_id="r-co11b")
        binding = _bind_memory_user_id(memory if memory is not None else user)
        try:
            yield
        finally:
            _unbind_memory_user_id(binding)


# ── chat-pin-tool-this-chat ─────────────────────────────────────────────────


def test_the_tool_takes_only_pinned() -> None:
    """Mutation: add a chat id, a member or an org parameter -> red."""
    assert list(inspect.signature(cpt.pin_chat).parameters) == ["pinned"]
    assert len(cpt.TOOLS) == 1 and cpt.TOOLS[0] is cpt.pin_chat


@pytest.mark.parametrize(("pinned", "verb", "answer"), [
    (True, "PUT", cpt.PINNED), (False, "DELETE", cpt.UNPINNED)])
async def test_the_tool_sends_the_route_of_this_chat(
    gateway: _Gateway, pinned: bool, verb: str, answer: str,
) -> None:
    with _run():
        out = await cpt.pin_chat(pinned)
    assert out == answer
    [req] = gateway.requests
    assert req.method == verb
    assert req.url.raw_path.decode() == PATH
    assert req.content == b""
    names = {k.lower() for k in req.headers} - HTTPX_OWN
    assert names == {"authorization", "x-user-email", "content-type"}, names
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-user-email"] == MEMBER


async def test_the_address_keeps_its_case(gateway: _Gateway) -> None:
    """The visibility rule of the route binds the exact address. Mutation:
    lower the case of the header -> red."""
    with _run(member=MEMBER.lower()):
        await cpt.pin_chat(True)
    assert gateway.requests[0].headers["x-user-email"] == MEMBER


@pytest.mark.parametrize("memory", ["room:co11b-room", OTHER, ""])
async def test_the_memory_id_never_names_the_member(gateway: _Gateway, memory: str) -> None:
    """A shared room binds ``room:<thread>`` as the memory id. Mutation: read
    ``_get_memory_user_id`` for the header -> red."""
    with _run(memory=memory):
        out = await cpt.pin_chat(True)
    assert out == cpt.PINNED
    assert gateway.requests[0].headers["x-user-email"] == MEMBER


# ── chat-pin-tool-refuses ───────────────────────────────────────────────────


@pytest.mark.parametrize(("over", "answer"), [
    ({"verified": False}, cpt.NO_MEMBER),
    ({"user": ""}, cpt.NO_MEMBER),
    ({"user": "default", "member": "default"}, cpt.NO_MEMBER),
    ({"member": OTHER}, cpt.NO_MEMBER),
    ({"member": ""}, cpt.NO_MEMBER),
    ({"session": ""}, cpt.NO_CHAT),
    ({"batch": True}, cpt.NO_CHAT),
])
async def test_a_refusal_sends_nothing(gateway: _Gateway, over: dict[str, Any], answer: str) -> None:
    """Mutation: drop the member check, the compare of the two members, the
    session check or the batch check -> a request goes out -> red."""
    with _run(**over):
        out = await cpt.pin_chat(True)
    assert out == answer
    assert gateway.requests == []


async def test_a_whatsapp_run_sends_nothing(gateway: _Gateway) -> None:
    with _run(), ap.profile_channel("whatsapp", writes=True):
        out = await cpt.pin_chat(True)
    assert out == cpt.ON_WHATSAPP
    assert gateway.requests == []


async def test_a_value_that_is_not_a_bool_sends_nothing(gateway: _Gateway) -> None:
    with _run():
        out = await cpt.pin_chat("yes")  # type: ignore[arg-type]
    assert out == cpt.NOT_A_CHOICE
    assert gateway.requests == []


# ── Each status gives its sentence ──────────────────────────────────────────


@pytest.mark.parametrize(("status", "body", "pinned", "answer"), [
    (200, "auto", True, cpt.PINNED),
    (200, "auto", False, cpt.UNPINNED),
    (200, {"ok": True, "pinned": False}, True, cpt.NO_ANSWER),
    (200, "not json", True, cpt.NO_ANSWER),
    (404, {"detail": "Not Found"}, True, cpt.NOT_FOUND),
    (404, {"detail": "Session not found"}, False, cpt.NOT_FOUND),
    (409, {"detail": "This chat is archived. Restore it, then pin it."}, True,
     "Nothing changed. This chat is archived. Restore it, then pin it."),
    (409, {}, True, f"Nothing changed. {cpt.ARCHIVED}"),
    (409, {"detail": "x" * 300}, True, f"Nothing changed. {cpt.ARCHIVED}"),
    (409, {"detail": ["a", "list"]}, True, f"Nothing changed. {cpt.ARCHIVED}"),
    (403, {"detail": "Forbidden: missing permission 'feature:chat'."}, True, cpt.FORBIDDEN),
    (403, {"detail": "Sign in to pin a chat."}, False, cpt.FORBIDDEN),
    (503, {"detail": "We could not reach the directory."}, True, cpt.NO_IDENTITY),
    (401, {"detail": "Authentication required"}, True, cpt.NO_ANSWER),
    (405, {}, True, cpt.NO_ANSWER),
    (422, {}, True, cpt.NO_ANSWER),
    (500, {}, True, cpt.NO_ANSWER),
    (502, "bad gateway", False, cpt.NO_ANSWER),
])
async def test_each_status_gives_its_sentence(
    gateway: _Gateway, status: int, body: Any, pinned: bool, answer: str,
) -> None:
    gateway.status, gateway.body = status, body
    with _run():
        out = await cpt.pin_chat(pinned)
    assert out == answer
    assert len(gateway.requests) == 1


def test_a_409_detail_goes_on_one_line() -> None:
    body = {"detail": "This chat is\n archived."}
    assert cpt._answer(409, body, True) == "Nothing changed. This chat is archived."


@pytest.mark.parametrize("fault", [
    httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), RuntimeError("boom")])
async def test_a_fault_of_the_request_never_raises(gateway: _Gateway, fault: Exception) -> None:
    gateway.raises = fault
    with _run():
        out = await cpt.pin_chat(True)
    assert out == cpt.NO_ANSWER


def _texts() -> list[str]:
    return [v for k, v in vars(cpt).items()
            if k.isupper() and isinstance(v, str) and " " in v]


def test_the_texts_are_plain_and_name_no_tool() -> None:
    """Each text for the member: no em dash, no semicolon, no tool name, at
    most 25 words in a sentence. Each failure starts with "Nothing changed"."""
    texts = _texts()
    assert len(texts) >= 15, texts
    for text in texts:
        assert EM_DASH not in text and ";" not in text, text
        assert "pin_chat" not in text and "_" not in text, text
        for sentence in re.split(r"(?<=[.?])\s+", text):
            assert len(sentence.split()) <= 25, sentence
    for text in (cpt.NOT_A_CHOICE, cpt.NO_MEMBER, cpt.NO_CHAT, cpt.ON_WHATSAPP,
                 cpt.NOT_FOUND, cpt.FORBIDDEN, cpt.NO_IDENTITY, cpt.NO_ANSWER):
        assert text.startswith("Nothing changed. "), text
    doc = inspect.getdoc(cpt.pin_chat) or ""
    assert EM_DASH not in doc and ";" not in doc and "pin_chat" not in doc
    assert "Call it only when the member asks" in doc


# ── chat-pin-tool-card ──────────────────────────────────────────────────────


class _Card:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *a: Any, **k: Any) -> Any:
        self.calls.append((a, k))
        return self.answer


@pytest.mark.parametrize(("pinned", "title"), [(True, cpt.CARD_TITLE_PIN),
                                               (False, cpt.CARD_TITLE_UNPIN)])
async def test_a_held_run_shows_one_card_and_an_approve_sends_once(
    gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, pinned: bool, title: str,
) -> None:
    card = _Card(True)
    monkeypatch.setattr(egress, "write_must_show_card", _async(True))
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    with _run():
        out = await cpt.pin_chat(pinned)
    assert out == (cpt.PINNED if pinned else cpt.UNPINNED)
    [(args, kwargs)] = card.calls
    assert args[0] == title
    assert kwargs["responder"] == MEMBER
    assert kwargs["non_interactive_default"] == "deny"
    assert len(gateway.requests) == 1


async def test_a_reject_sends_nothing(gateway: _Gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: skip the card, or act on a reject -> a request -> red."""
    monkeypatch.setattr(egress, "write_must_show_card", _async(True))
    monkeypatch.setattr(ask_tools, "request_confirmation", _Card(False))
    with _run():
        out = await cpt.pin_chat(True)
    assert gateway.requests == []
    assert out == ask_tools.card_not_done_text(cpt.ACT_NOT_PINNED)
    assert out.startswith(ask_tools.CARD_OUTCOMES[ask_tools.CARD_DECLINED].lead)


async def test_a_revised_card_sends_nothing(
    gateway: _Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "write_must_show_card", _async(True))
    monkeypatch.setattr(ask_tools, "request_confirmation", _Card(ask_tools.REVISED))
    with _run():
        await cpt.pin_chat(False)
    assert gateway.requests == []


async def test_no_card_channel_refuses_and_sends_nothing(
    gateway: _Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real card gate, in a run with no card channel."""
    monkeypatch.setattr(egress, "write_must_show_card", _async(True))
    with _run(), ask_tools.refuse_cards():
        out = await cpt.pin_chat(True)
    assert gateway.requests == []
    assert out == ask_tools.card_outcome_text(ask_tools.CARD_NOT_SHOWN, cpt.ACT_NOT_PINNED)


async def test_a_fault_of_the_card_rule_shows_the_card(
    gateway: _Gateway, monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = _Card(False)
    monkeypatch.setattr(egress, "write_must_show_card", _async(RuntimeError("redis")))
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    with _run():
        await cpt.pin_chat(True)
    assert len(card.calls) == 1 and gateway.requests == []


async def test_a_clear_run_shows_no_card(gateway: _Gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    card = _Card(False)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    with _run():
        out = await cpt.pin_chat(True)
    assert card.calls == [] and out == cpt.PINNED


# ── chat-pin-tool-dark ──────────────────────────────────────────────────────


class _FakeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _mod() -> Any:
    import orchestrator.agents as pkg

    return pkg._mod


def _flags(monkeypatch: pytest.MonkeyPatch, pin: str, tool: str) -> None:
    """``CHAT_PIN_ORGS`` is *pin*, and ``CHAT_PIN_TOOL_ORGS`` is *tool*."""
    monkeypatch.setattr(get_settings(), "chat_pin_orgs", pin, raising=False)
    monkeypatch.setattr(get_settings(), "chat_pin_tool_orgs", tool, raising=False)


def _names(monkeypatch: pytest.MonkeyPatch, orgs: str, org: str | None,
           *, tool: str | None = None) -> list[str]:
    """The tool names of one build. *orgs* goes to both lists, unless *tool*
    gives ``CHAT_PIN_TOOL_ORGS`` its own value."""
    mod = _mod()
    _flags(monkeypatch, orgs, orgs if tool is None else tool)
    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    agent = mod.build_orchestrator_agent(with_history=False, organization_id=org)
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


def test_the_build_holds_the_tool_only_for_an_org_on_the_list(monkeypatch) -> None:
    """Mutation: give the build the tool with no flag test -> red."""
    off = _names(monkeypatch, "", IN)
    assert "pin_chat" not in off
    for orgs, org in ((OUT, IN), ("*", None), (IN, OUT)):
        assert "pin_chat" not in _names(monkeypatch, orgs, org), (orgs, org)
    for orgs in (IN, "*", f"x, {IN.upper()}"):
        on = _names(monkeypatch, orgs, IN)
        assert on.count("pin_chat") == 1
        assert [n for n in on if n != "pin_chat"] == off
        assert on.index("pin_chat") == on.index("spawn_copilot_agent") - 1


@pytest.mark.parametrize("pin", [IN, "*"])
def test_the_pin_alone_adds_no_tool_and_moves_no_count(monkeypatch, pin: str) -> None:
    """The owner decision of 2026-11-21 (task 3563). The pin of the chat list
    turns on, and the tool list is the list of both flags off. So no golden
    row moves. Mutation: point ``_chat_pin_enabled`` back at ``pin_allowed``
    -> red."""
    off = _names(monkeypatch, "", IN)
    assert _names(monkeypatch, pin, IN, tool="") == off
    assert _mod()._chat_pin_enabled(IN) is False


@pytest.mark.parametrize("tool", [IN, "*"])
def test_the_tool_flag_alone_adds_no_tool(monkeypatch, tool: str) -> None:
    """With the pin off, the routes answer 404, so a tool would only fail.
    Mutation: drop the test of ``pin_allowed`` from ``pin_tool_allowed``
    -> red."""
    off = _names(monkeypatch, "", IN)
    assert _names(monkeypatch, "", IN, tool=tool) == off
    assert _mod()._chat_pin_enabled(IN) is False


@pytest.mark.parametrize(("pin", "tool"), [(IN, IN), ("*", "*"), ("*", IN), (IN, "*")])
def test_both_flags_on_add_the_tool(monkeypatch, pin: str, tool: str) -> None:
    on = _names(monkeypatch, pin, IN, tool=tool)
    assert on.count("pin_chat") == 1
    assert _mod()._chat_pin_enabled(IN) is True


@pytest.mark.parametrize(("pin", "tool", "org", "want"), [
    ("", "", IN, False),
    (IN, "", IN, False),
    ("*", "", IN, False),
    ("", IN, IN, False),
    ("", "*", IN, False),
    (OUT, "*", IN, False),
    ("*", OUT, IN, False),
    (IN, IN, IN, True),
    ("*", "*", IN, True),
    ("*", IN, IN, True),
    (IN, "*", IN, True),
    (f"x, {IN.upper()}", f" {IN.upper()} ,y", IN, True),
    ("*", "*", None, False),
    ("*", "*", "", False),
    ("*", "*", "   ", False),
])
def test_the_reader_needs_both_lists(
    monkeypatch, pin: str, tool: str, org: str | None, want: bool,
) -> None:
    """``pin_tool_allowed`` is the one reader of ``CHAT_PIN_TOOL_ORGS``. It
    takes the org as its one argument, and no org reads as off."""
    from gateway.chat_session_member import pin_tool_allowed

    assert list(inspect.signature(pin_tool_allowed).parameters) == ["organization_id"]
    _flags(monkeypatch, pin, tool)
    assert pin_tool_allowed(org) is want


def test_the_build_reads_the_bound_tenant(monkeypatch) -> None:
    """The org comes from the session: the bound tenant, never an argument
    of the model."""
    from acb_common.db import bind_tenant, release_tenant

    for org, want in ((IN, True), (OUT, False)):
        token = bind_tenant(org)
        try:
            assert ("pin_chat" in _names(monkeypatch, IN, None)) is want, org
        finally:
            release_tenant(token)


def test_a_fault_of_the_flag_reads_as_none(monkeypatch) -> None:
    import gateway.chat_session_member as store

    def _boom(_org: Any) -> bool:
        raise RuntimeError("settings")

    monkeypatch.setattr(store, "pin_allowed", _boom)
    assert "pin_chat" not in _names(monkeypatch, "*", IN)
    assert store.pin_tool_allowed(IN) is False
    assert _mod()._chat_pin_enabled(IN) is False


def test_a_fault_of_the_tool_reader_reads_as_none(monkeypatch) -> None:
    import gateway.chat_session_member as store

    def _boom(_org: Any) -> bool:
        raise RuntimeError("settings")

    monkeypatch.setattr(store, "pin_tool_allowed", _boom)
    assert "pin_chat" not in _names(monkeypatch, "*", IN)
    assert _mod()._chat_pin_enabled(IN) is False


def test_a_fault_of_the_tool_list_reads_as_off(monkeypatch) -> None:
    """The pin is on, and the read of ``CHAT_PIN_TOOL_ORGS`` fails."""
    import acb_common
    import gateway.chat_session_member as store

    def _boom() -> Any:
        raise RuntimeError("settings")

    monkeypatch.setattr(store, "pin_allowed", lambda _org: True)
    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert store.pin_tool_allowed(IN) is False


def test_the_persona_does_not_change(monkeypatch) -> None:
    mod = _mod()
    _flags(monkeypatch, "*", "*")
    monkeypatch.setattr(mod, "Agent", _FakeAgent)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    on = mod.build_orchestrator_agent(with_history=False, organization_id=IN)
    assert "pin_chat" in [getattr(t, "__name__", "") for t in on.kwargs["tools"]]
    _flags(monkeypatch, "", "")
    off = mod.build_orchestrator_agent(with_history=False, organization_id=IN)
    assert on.kwargs["instructions"] == off.kwargs["instructions"]


# ── The risk annotations ────────────────────────────────────────────────────


def test_the_annotations_say_a_platform_write() -> None:
    risk = TOOL_ANNOTATIONS["pin_chat"]
    assert risk == {"read_only": False, "destructive": False, "idempotent": True,
                    "open_world": False}
    assert cpt.pin_chat.__tool_risk__ == risk
    assert "pin_chat" in _H236_UNLISTED
    assert egress.is_egress_tool(cpt.pin_chat) is False
    assert "pin_chat" not in risk_summary_block()


# ── chat-pin-tool-parity ────────────────────────────────────────────────────


def test_both_pin_actions_are_covered_by_the_tool() -> None:
    from tests.unit import _chat_parity_reader as r

    for verb in ("PUT", "DELETE"):
        key = f"{verb} /api/chat/sessions/{{}}/pin"
        assert cp.COVERED[key] == "pin_chat" and key not in cp.DEBT, key
        assert r.reaches("pin_chat", verb, "/chat/sessions/{}/pin"), verb
    assert not r.reaches("pin_chat", "PATCH", "/chat/sessions/{}/pin")
    assert "packages/acb_skills/acb_skills/chat_pin_tools.py" in cp.TOOL_SOURCES
    assert "pin_chat" in r.registered_tools()
    assert cp.DEBT_CEILING["chat"] == 28
