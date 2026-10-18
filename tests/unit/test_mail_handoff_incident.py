"""WS-17 incident 2026-10-18: an email hand-off on WhatsApp.

Spec ``project-docs/specs/email_app_master_plan.md`` §12.3.13.8, "Incident
2026-10-18". The member asked over WhatsApp to send a mail. Each email
request of the thread was refused for good, and one run timed out.

This file fences the WhatsApp half of the fix. The egress half (a hand-off
asks no card, a delegated mail agent keeps its own carded writes, the mark
ends with the replay window) lives in ``test_delegation_no_egress.py``.

* The replay floor that ``bot_run`` gives a WhatsApp run
  (``_history_floor_ms``), and the row time that never reaches the model.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from gateway.routes.whatsapp_channel import bot_run


def _rows(n: int, start: int = 1_000) -> list[dict[str, Any]]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}",
             "server_ms": start + i} for i in range(n)]


def test_the_floor_is_the_oldest_replayed_row_of_a_full_window(monkeypatch) -> None:
    """A full window of ``HISTORY_TURNS`` rows gives the time of its oldest
    row. A shorter thread replays all of its rows, so the floor is 0, and so
    are a row with no time and a run that a stored session can replay."""
    from orchestrator import executor

    monkeypatch.setattr(executor, "_native_sessions_enabled", lambda: False)
    full = _rows(bot_run.HISTORY_TURNS)
    assert bot_run._history_floor_ms(full) == 1_000
    assert bot_run._history_floor_ms(full[1:]) == 0
    assert bot_run._history_floor_ms([*full[:-1], {"role": "user", "content": "x"}]) == 0
    monkeypatch.setattr(executor, "_native_sessions_enabled", lambda: True)
    assert bot_run._history_floor_ms(full) == 0


def test_a_forged_client_time_never_moves_the_floor(monkeypatch) -> None:
    """Fix round 1, item 3. A web client sends ``timestamp_ms``, and a web
    write can move it forward. The floor reads only the server's time of
    write (``server_ms``, from ``created_at``), so a forged future
    ``timestamp_ms`` on the oldest replayed row moves nothing. A floor from
    ``timestamp_ms`` would move to the next row."""
    from orchestrator import executor

    monkeypatch.setattr(executor, "_native_sessions_enabled", lambda: False)
    full = _rows(bot_run.HISTORY_TURNS)
    full[0]["timestamp_ms"] = 10**15  # a forged time on the oldest row
    for row in full[1:]:
        row["timestamp_ms"] = row["server_ms"]
    assert bot_run._history_floor_ms(full) == 1_000


def test_the_row_time_never_reaches_the_model() -> None:
    """``_thread_turns`` keeps each row's time for the floor. The payload's
    turns hold the role and the text only."""
    payload = bot_run.build_payload("hi", _rows(3), "m@example.com")
    assert payload["messages"] == [
        {"role": "user", "content": "t0"},
        {"role": "assistant", "content": "t1"},
        {"role": "user", "content": "t2"},
    ]
    assert payload["_history_loader"]() == payload["messages"]


# ── Fix 4: a delegation ends in time for the answer ─────────────────────────


def test_a_whatsapp_deadline_caps_each_delegation() -> None:
    """Outside a run with a deadline, a delegation gets the long budget. In a
    WhatsApp run it ends ``DELEGATION_RESERVE_S`` before the deadline, and
    never gets less than ``DELEGATION_MIN_S``."""
    import time

    from acb_skills import agent_tools as at
    from acb_skills.whatsapp_ui import whatsapp_run

    assert at.delegation_timeout() == at._SUB_AGENT_TIMEOUT
    with whatsapp_run("orchestrator", deadline=time.monotonic() + bot_run.RUN_TIMEOUT_S):
        budget = at.delegation_timeout()
        assert budget >= bot_run.RUN_TIMEOUT_S - at.DELEGATION_RESERVE_S - 2
        assert budget <= bot_run.RUN_TIMEOUT_S - at.DELEGATION_RESERVE_S
    with whatsapp_run("orchestrator", deadline=time.monotonic() + 5):
        assert at.delegation_timeout() == at.DELEGATION_MIN_S
    with whatsapp_run("orchestrator"):
        assert at.delegation_timeout() == at._SUB_AGENT_TIMEOUT


async def test_a_slow_hand_off_returns_in_time_for_the_answer(monkeypatch) -> None:
    """Run 15949617: two hand-offs took 101 s and 108 s, and the WhatsApp run
    hit its limit while the orchestrator wrote its answer. Now ``call_agent``
    and a specialist tool stop a slow hand-off before the deadline, and the
    model reads why, so it can still answer."""
    import asyncio
    import time

    from acb_skills import agent_tools as at
    from acb_skills.whatsapp_ui import whatsapp_run
    from orchestrator import agents as orch_package
    from orchestrator import executor

    # ``orchestrator/agents.py`` loads as ``orchestrator._agents_module``,
    # because the package ``orchestrator/agents/`` shadows its name.
    orch_agents = orch_package._mod

    async def hang(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(30)
        raise AssertionError("the hand-off was not stopped")

    monkeypatch.setattr(at, "DELEGATION_MIN_S", 0.2)
    monkeypatch.setattr(executor, "run_agent", hang)
    monkeypatch.setattr(orch_agents, "delegate_to_agent", hang)
    import gateway.routes.agent as routes_agent

    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    specialist = next(t for t in orch_agents._load_specialist_agents_as_tools()
                      if t.name == "email_assistant")
    for call in (lambda: at.call_agent("email-assistant", "send a top-up mail"),
                 lambda: specialist.func(task="send a top-up mail")):
        with whatsapp_run("orchestrator", deadline=time.monotonic() + at.DELEGATION_RESERVE_S):
            started = time.monotonic()
            out = await asyncio.wait_for(call(), 10)
        assert time.monotonic() - started < 5, "the hand-off ran past its budget"
        assert "timed out" in out and "time limit" in out, out


# ── Fix 5: every card asks on WhatsApp, through the Confirm buttons ─────────

ORG = "cccccccc-0000-0000-0000-0000000000c3"
THREAD = "wa-thread-incident"
MEMBER = "member@example.com"
URL = "https://example.com/page"


class _Req:
    """The fields of ``bot_run.RunRequest`` that the Confirm reads."""

    organization_id = ORG
    member_email = MEMBER
    chat_session_id = THREAD
    wa_id = "15550001111"
    message_id = "00000000-0000-0000-0000-000000000001"


def _closes(monkeypatch) -> list[tuple[str, str, str | None]]:
    """The Confirm's row writes, with no database."""
    from gateway.routes.whatsapp_channel import acts

    closed: list[tuple[str, str, str | None]] = []

    async def close(_act_id: str, frm: str, to: str, code: str | None) -> bool:
        closed.append((frm, to, code))
        return True

    monkeypatch.setattr(acts, "_close", close)
    return closed


def _stored(parked: Any) -> dict[str, Any]:
    """The row that ``acts.park`` would store for *parked*."""
    return {"id": "act-1", "tool_name": parked.tool, "arguments": parked.arguments,
            "card_text": parked.card, "act_home": parked.home}


def _platform_fetch(monkeypatch) -> tuple[Any, list[str]]:
    """A platform ``fetch_page`` that records each call, and its gated form.
    The chain registry of the Confirm finds the same function, and the
    egress registry trusts it as the platform's own, as the seam does."""
    from acb_skills import egress as eg
    from orchestrator import _tool_injection as ti

    calls: list[str] = []

    async def fetch_page(url: str) -> str:
        calls.append(url)
        return f"fetched: {url}"

    fetch_page.__tool_risk__ = {  # type: ignore[attr-defined]
        "read_only": True, "destructive": False, "idempotent": False, "open_world": True}
    monkeypatch.setitem(eg._PLATFORM_CALLABLES, fetch_page, frozenset({"fetch_page"}))
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools", lambda *a, **k: [fetch_page])
    return ti._gate_injected_tool(fetch_page), calls


def _live(parked: Any) -> dict[str, Any]:
    """The pending row that ``acts.settle`` reads, offered and in time."""
    return {**_stored(parked), "member_email": MEMBER, "link_id": "link-1",
            "live": True, "offered": True, "offered_wamid": "wamid.out",
            "tap_of": None, "after_offer": True}


async def test_a_marked_whatsapp_fetch_asks_confirm_and_runs_only_after_a_yes(
    monkeypatch,
) -> None:
    """Fix 5. A WhatsApp run in a marked session calls ``fetch_page``. The
    untrusted-answer card PARKS for the member's Confirm (it was refused with
    ``no_card_channel`` before), and the fetch does not run. Cancel runs
    nothing. Confirm runs the same call once, through the gate, and its card
    answers the same card."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills import egress as eg
    from acb_skills.ask_tools import refuse_cards
    from acb_skills.whatsapp_acts import HOME_PLATFORM, PARKED_TEXT
    from acb_skills.whatsapp_ui import whatsapp_run
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from gateway.routes.whatsapp_channel import acts

    from tests.unit._egress_mark_store import install_memory_mark_store

    store = install_memory_mark_store(monkeypatch)
    gated, calls = _platform_fetch(monkeypatch)
    closed = _closes(monkeypatch)
    token = bind_tenant(ORG)
    try:
        store.values[f"cc:{ORG}:{eg.MARK_NAMESPACE}:{THREAD}"] = "1"
        with artifact_context_scope():
            bind_artifact_context(run_id="r", session_id=THREAD, member=MEMBER,
                                  agent_name="orchestrator")
            with refuse_cards(), whatsapp_run("orchestrator", writes=True,
                                              session_id=THREAD) as run:
                out = await gated(url=URL)
        assert out == PARKED_TEXT, out
        assert calls == [], "the fetch ran before the Confirm"
        assert run.parked is not None and run.parked.home == HOME_PLATFORM
        assert run.parked.tool == "fetch_page" and run.parked.arguments == {"url": URL}
        assert "reaches outside Metorite" in run.parked.title

        async def live(_req: Any) -> dict[str, Any]:
            return _live(run.parked)

        monkeypatch.setattr(acts, "_live_act", live)
        assert await acts.settle(_Req(), "Cancel") == acts.REPLY_CANCELLED
        assert calls == [] and closed[-1] == ("pending", "cancelled", None)

        eg._MARKED_SESSIONS.clear()
        reply = await acts._run(_Req(), "act-1", _stored(run.parked))
        assert calls == [URL], "the Confirm did not run the call once"
        assert reply == f"fetched: {URL}"
        assert closed[-1] == ("running", "done", None)
    finally:
        release_tenant(token)


def _email_module(monkeypatch) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    """The email assistant, with one mailbox and a recorded send."""
    from tests.unit._native_maf_harness import load_agent_module

    email = load_agent_module("apps/agents/agent-email-assistant")
    posts: list[tuple[str, dict[str, Any]]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        return {"id": "m-1"}

    async def one_mailbox(_recipient: str) -> tuple[str, str]:
        return "acct-1", ""

    async def name(_account_id: str) -> str:
        return "Work - me@example.com"

    monkeypatch.setattr(email, "_post", post)
    monkeypatch.setattr(email, "_new_mail_mailbox", one_mailbox)
    monkeypatch.setattr(email, "_mailbox_name", name)
    return email, posts


def _loads(monkeypatch, module: Any) -> None:
    """``executor.load_agent`` gives *module*'s agents, as the loader does."""
    import types

    import gateway.routes.agent as routes_agent
    from orchestrator import executor

    class _Loaded:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def __enter__(self) -> Any:
            return types.SimpleNamespace(build_agents=module.build_agents)

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", _Loaded)
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])


async def _park_send(email: Any, **kwargs: Any) -> tuple[str, Any]:
    """``send_email`` of *email* in a WhatsApp run with writes on, as the
    email assistant's own tool. Returns its answer and the parked act."""
    from acb_skills.ask_tools import refuse_cards
    from acb_skills.whatsapp_ui import whatsapp_run
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator import _tool_injection as ti

    send = next(t for t in email._agent_tools() if t.__name__ == "send_email")
    with artifact_context_scope():
        bind_artifact_context(run_id="r", session_id=THREAD, member=MEMBER,
                              agent_name="email-assistant")
        with refuse_cards(), whatsapp_run("orchestrator", writes=True,
                                          session_id=THREAD) as run:
            out = await ti._gate_injected_tool(send)(**kwargs)
    return out, run.parked


async def test_a_whatsapp_send_email_asks_confirm_and_sends_after_a_yes(monkeypatch) -> None:
    """Fix 5. On WhatsApp the email assistant's ``send_email`` PARKS for the
    member's Confirm (it answered "a card cannot show here" before). Nothing
    is sent. The Confirm loads the agent, runs its own ``send_email`` once,
    and the send's own card answers the stored card."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.whatsapp_acts import PARKED_TEXT
    from gateway.routes.whatsapp_channel import acts

    email, posts = _email_module(monkeypatch)
    closed = _closes(monkeypatch)
    token = bind_tenant(ORG)
    try:
        out, parked = await _park_send(
            email, to=["payal@example.com"], subject="Top-up",
            body="Please share the priority list.")
        assert out == PARKED_TEXT, out
        assert posts == [], "the mail went before the Confirm"
        assert parked is not None and parked.home == "agent:email-assistant"
        assert parked.title.startswith("Send this")

        _loads(monkeypatch, email)
        reply = await acts._run(_Req(), "act-2", _stored(parked))
        assert [p for p, _ in posts] == ["/email/send"], posts
        assert posts[0][1]["to"] == ["payal@example.com"]
        assert reply.startswith("Sent email to payal@example.com"), reply
        assert closed[-1] == ("running", "done", None)
    finally:
        release_tenant(token)


async def test_a_confirm_whose_send_card_changed_sends_nothing(monkeypatch) -> None:
    """The stored card binds the act. A mailbox that changed its name
    between the ask and the Confirm builds another card, so nothing is sent
    and the act closes ``void``."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.whatsapp_channel import acts

    email, posts = _email_module(monkeypatch)
    closed = _closes(monkeypatch)
    token = bind_tenant(ORG)
    try:
        _out, parked = await _park_send(
            email, to=["payal@example.com"], subject="Top-up", body="Hi")

        async def renamed(_account_id: str) -> str:
            return "Other - else@example.com"

        monkeypatch.setattr(email, "_mailbox_name", renamed)
        _loads(monkeypatch, email)
        assert await acts._run(_Req(), "act-3", _stored(parked)) == acts.REPLY_CHANGED
        assert posts == [] and closed[-1] == ("running", "void", "changed")
    finally:
        release_tenant(token)


def test_the_confirm_finds_only_a_known_home_and_a_held_tool(monkeypatch) -> None:
    """``executor.act_tool`` finds the real platform ``fetch_page`` for the
    ``platform`` home, inside the gate. Any other home, an agent that the
    registry does not name, and a tool that the agent does not hold give
    None, so the act fails closed. A stored row cannot name a module."""
    import gateway.routes.agent as routes_agent
    from orchestrator import executor

    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    with executor.act_tool("platform", "fetch_page") as fn:
        assert callable(fn) and fn.__name__ == "fetch_page"
    for home, tool in (("platform", "no_such_tool"), ("os.system", "fetch_page"),
                       (None, "fetch_page"), ("agent:no-such-agent", "send_email"),
                       ("agent:", "send_email")):
        with executor.act_tool(home, tool) as fn:
            assert fn is None, (home, tool)
    from tests.unit._native_maf_harness import load_agent_module

    _loads(monkeypatch, load_agent_module("apps/agents/agent-email-assistant"))
    with executor.act_tool("agent:email-assistant", "send_email") as fn:
        assert callable(fn) and fn.__name__ == "send_email"
    with executor.act_tool("agent:email-assistant", "fetch_page") as fn:
        assert fn is None, "the agent does not hold that tool"


# ── Fence 6: no new "cannot show a card" answer on WhatsApp ─────────────────
#
# The owner rule of 2026-10-18: "WhatsApp should have full capability to do
# all activities." A channel changes delivery, never function. Two checks.
# The scan fails when a NEW string that tells the member or the model "a card
# cannot show here" enters the product, until its WhatsApp behaviour is named
# below. The run check binds the WhatsApp channel and asks each card gate:
# each one asks (parks for the Confirm buttons), and none refuses.

_REPO = Path(__file__).resolve().parents[2]

#: Each text that answers "no card here", by file and its first words, with
#: what a WhatsApp member gets. A new one fails the fence until it is here.
NO_CARD_TEXTS: dict[tuple[str, str], str] = {
    ("apps/agents/agent-email-assistant/agents.py", "Nothing changed. This needs your"): (
        "_NO_CARD_CHANNEL. On WhatsApp with writes on, the card parks, and the "
        "injection seam answers the parked line instead. It reaches a WhatsApp "
        "run only for a second card in a run that already parked its one act."),
    ("apps/agents/agent-email-assistant/agents.py", " (Drafts is unchanged, because"): (
        "draft_reply's unsaved draft. On WhatsApp with writes on, "
        "confirmation_channel_open() is True, so save=true asks its card."),
    ("apps/agents/agent-email-assistant/agents.py", " characters, so the card canno"): (
        "A draft too long for its card. A refusal of content, not of a channel."),
    ("apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py",
     "You are answering a member of t"): (
        "SCOPE_RULE: a WhatsApp run with writes OFF for its org (spec 14.6, the "
        "owner's switch). With writes on, SCOPE_RULE_WRITES replaces it."),
    ("apps/services/orchestrator/orchestrator/_maf_mcp.py", "[not run: {tool} can change da"): (
        "NO_CARD_CHANNEL_TEXT. RESIDUAL: an MCP call needs the run's live server "
        "session, so it cannot park for a Confirm (email_app_master_plan.md "
        "12.3.13.8, incident 2026-10-18)."),
    ("apps/skills/skill-projects/skill_projects/writes.py", "not readable here "): (
        "A task outside the member's lens. A refusal of content, not of a channel."),
    ("packages/acb_skills/acb_skills/ask_tools.py", "This chat cannot show question"): (
        "NO_CARD_QUESTIONS: a question, not an approval. The model asks in text, "
        "and the member answers in the next message."),
    ("packages/acb_skills/acb_skills/write_artifact.py", "This chat cannot show a card t"): (
        "GENUI_NO_CARD_TEXT: a blocking UI card, not an approval. The model asks "
        "in text."),
}

_NO_CARD = re.compile(
    r"(cannot|can't|can not) (show|take)[^.]{0,30}(card|step|questions)"
    r"|card cannot show|no card channel", re.I)


def _no_card_texts() -> set[tuple[str, str]]:
    """Every string constant in the product code that says "no card here"
    (docstrings aside), as (file, text)."""
    found: set[tuple[str, str]] = set()
    for base in ("apps", "packages"):
        for path in (_REPO / base).rglob("*.py"):
            rel = path.relative_to(_REPO).as_posix()
            if "/tests/" in rel or "/.venv/" in rel or "node_modules" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef))
                    and n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docs and _NO_CARD.search(node.value)):
                    found.add((rel, node.value))
    return found


def test_every_no_card_answer_names_its_whatsapp_behaviour() -> None:
    """Fence 6, the scan. A new "a card cannot show here" text fails until
    NO_CARD_TEXTS names what a WhatsApp member gets. A stale entry fails too."""
    found = _no_card_texts()

    def names(rel: str, text: str) -> list[tuple[str, str]]:
        return [k for k in NO_CARD_TEXTS if k[0] == rel and text.startswith(k[1])]

    unnamed = sorted((rel, text[:60]) for rel, text in found if not names(rel, text))
    assert not unnamed, f"name the WhatsApp behaviour of: {unnamed}"
    used = {k for rel, text in found for k in names(rel, text)}
    assert used == set(NO_CARD_TEXTS), f"stale entries: {sorted(set(NO_CARD_TEXTS) - used)}"


async def test_with_the_whatsapp_channel_bound_every_card_gate_asks() -> None:
    """Fence 6, the run. In a WhatsApp run with writes on, each card gate
    asks the card, and none answers "no card here": the untrusted-answer
    card, the channel check that a tool reads, and an agent's own card,
    which parks. A batch run and a run with no member still refuse."""
    from acb_skills import ask_tools
    from acb_skills import whatsapp_acts as wacts
    from acb_skills.whatsapp_ui import whatsapp_run
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator import _tool_injection as ti

    def own_tool() -> None:
        return None

    own_tool.__name__ = "send_email"
    own_tool.__module__ = "agent_email_assistant_probe"
    with artifact_context_scope():
        bind_artifact_context(run_id="r", session_id=THREAD, member=MEMBER,
                              agent_name="email-assistant")
        with ask_tools.refuse_cards(), whatsapp_run("orchestrator", writes=True) as run:
            assert ti._untrusted_answer_card_refusal() is None
            assert ask_tools.confirmation_channel_open() is True
            with ti._whatsapp_tool_call(own_tool, (), {"to": ["a@example.com"]}):
                answer = await ask_tools.request_confirmation(
                    title="Send this email?", detail="d", context="c")
            assert answer is False and run.parked is not None, "the card did not park"
            assert run.parked.home == "agent:email-assistant"
        bind_artifact_context(run_id="r", session_id=THREAD, member=MEMBER,
                              agent_name="email-assistant", batch_thread=True)
        with ask_tools.refuse_cards(), whatsapp_run("orchestrator", writes=True):
            assert ti._untrusted_answer_card_refusal() is not None, "a batch run asked"
        with ask_tools.refuse_cards(), whatsapp_run("orchestrator", writes=False):
            assert wacts.answers_cards() is False, "writes off is the owner's switch"
