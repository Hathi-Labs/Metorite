"""WS-67 OA-7b: the guards that move with the tools of a pack.

``project-docs/specs/one_assistant_context.md`` §4.2.3, §4.2.8 (OA-7b) and
the narrowings of §4.5. The builder is
``orchestrator._tool_injection._pack_tools``. It has no live caller: OA-7c
wires it into runs. A pack tool runs as its owner would run it in a sub-run
of this chat. Each test names, in its docstring, the mutation that turns it
red.

1. Open and held packs equal the owner's open and held runs.
2. Each held-card tool in a pack is the email module's own function, at
   ``__wrapped__``.
3. The session mark comes before the tool body.
4. A pack write asks its own card, and never the untrusted card too.
5. After the mark, the orchestrator's own ``fetch_page`` and ``web_search``
   ask the untrusted card. They stay on (owner answer 1).
6. After the mark, each store write refuses.
7. A run with the ``email`` pack gets no registry MCP server.
8. Each reader of ``agent_name`` reads the owner inside a pack call. The
   card row of ``pending_ask`` names the run's agent, as for a delegation.
9. The builder calls the one seam: the held drop follows ``_is_egress``.
10. Each pack tool carries ``__cc_gated__``, so no assembly gates it twice.

Also: the drop at load is the only held-set control of a pack run, a pack
tool acts only in the run that loaded it, the working set and the profile
writes, the names that the run holds, and the one owner loader (OA-7a
verifier F2 and F3).

Run: ``uv run pytest tests/unit/test_pack_guards.py -q``.
"""
from __future__ import annotations

import contextlib
import inspect
import json
import types
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills.write_artifact import (
    artifact_context,
    artifact_context_scope,
    bind_artifact_context,
)
from orchestrator import _tool_injection as ti

from tests.unit import _held_card_fakes as F
from tests.unit._native_maf_harness import load_agent_module
from tests.unit.test_egress_card_bound_pin import SECTION_21_WITHHELD
from tests.unit.test_no_egress_tool_audit import (  # noqa: F401 - a fixture
    EXPECTED_WITHHELD,
    _hermetic,
    _in_repo_agents,
    _run_tools,
)

EA = "email-assistant"
EA_DIR = "apps/agents/agent-email-assistant"
ORG = "0a0a0a0a-0000-4000-8000-0000000000aa"
MEMBER = "jane@acme.test"
SESSION = "chat-pack-1"
BOX = "acc-pack-1"
PACK_NAMES = frozenset(n for slug in sf.pack_slugs() for n in sf.SKILL_FAMILIES[slug]["tools"])

_READ_ONLY = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}
_SENDS = {"read_only": False, "destructive": True, "idempotent": False, "open_world": True}


def _module() -> Any:
    return load_agent_module(EA_DIR)


@contextlib.contextmanager
def _run(run_id: str = "r-1", **over: Any) -> Iterator[None]:
    """The orchestrator's run of a member's own chat, bound by the server."""
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            values = {"run_id": run_id, "session_id": SESSION, "agent_name": "orchestrator",
                      "member": MEMBER, "workspace_root": "/tmp/orch-ws", "no_egress": False}
            values.update(over)
            bind_artifact_context(**values)
            yield
    finally:
        release_tenant(token)


def _stub(*tools: Any) -> Any:
    """An owner module whose ``_agent_tools`` builds *tools*."""
    return types.SimpleNamespace(_agent_tools=lambda: list(tools))


def _write_artifact_module() -> Any:
    """The module ``acb_skills.write_artifact``. The package exports a tool
    of the same name, so an attribute import gives the tool."""
    import importlib

    return importlib.import_module("acb_skills.write_artifact")


def _named(build: Any, name: str) -> Any:
    return next(t for t in build.tools if t.__name__ == name)


async def _build(slug: str, module: Any = None, **kw: Any) -> Any:
    return await ti._pack_tools(slug, artifact_context(), module=module or _module(), **kw)


async def _pack_names() -> set[str]:
    names: set[str] = set()
    for slug in sf.pack_slugs():
        names |= {t.__name__ for t in (await _build(slug)).tools}
    return names


def _probe(name: str = "read_email", risk: dict[str, Any] | None = None,
           body: Any = None) -> Any:
    """A tool of the ``email`` pack whose body runs *body*."""

    async def tool(email_id: str = "") -> str:
        if body is not None:
            await body()
        return f"read {email_id}"

    tool.__name__ = name
    tool.__tool_risk__ = dict(risk or _READ_ONLY)  # type: ignore[attr-defined]
    return tool


# ── 1. Open and held packs equal the owner's runs ───────────────────────────


@pytest.mark.usefixtures("_hermetic")
@pytest.mark.parametrize("switch", [False, True], ids=["switch-off", "switch-on"])
async def test_open_and_held_packs_equal_the_owners_open_and_held_runs(
    monkeypatch, switch: bool,
) -> None:
    """Mutation: the builder drops by another rule than ``_is_egress`` (for
    example it keeps the card-bound sends out), or it drops nothing in a held
    run, or it reads the held answer of the wrong agent."""
    (F.flag_on if switch else F.flag_off)(monkeypatch)
    rel = _in_repo_agents()[EA]
    owner_open = _run_tools(EA, rel, no_egress=False) & PACK_NAMES
    owner_held = _run_tools(EA, rel, no_egress=True) & PACK_NAMES
    with _run():
        pack_open = await _pack_names()
        await eg.mark_untrusted_answer()  # the chat read mail on an earlier turn
        pack_held = await _pack_names()
    assert len(pack_open) >= 58, "non-vacuity: the owner builds its tools"
    assert pack_open == owner_open
    assert pack_held == owner_held
    want = SECTION_21_WITHHELD if switch else EXPECTED_WITHHELD[EA]
    assert pack_open - pack_held == want


async def test_a_mark_that_only_redis_holds_holds_the_pack() -> None:
    """Another process marked the chat, so this process holds no mark. The
    pack is held as ``call_agent`` holds a delegation. Mutation: drop the
    read of ``session_is_known_marked`` from ``_pack_held`` → red."""
    with _run():
        await eg.mark_untrusted_answer()
        with eg._MARKED_LOCK:
            eg._MARKED_SESSIONS.clear()
        assert eg.read_untrusted_answer() is False
        build = await _build("email")
    assert build.held is True and "send_email" in build.dropped


async def test_an_empty_run_context_builds_a_held_pack() -> None:
    """Mutation: an empty context reads as an open run, as the delegation
    rule reads "no parent"."""
    assert await ti._pack_held(EA, {}) is True
    with _run(run_id="r-open"):
        assert await ti._pack_held(EA, artifact_context()) is False


# ── 2. The held-card tools are the email module's own functions ─────────────


async def test_each_held_card_tool_is_the_email_modules_own_function(monkeypatch) -> None:
    """``_agent_tools`` makes new ``refusals_as_text`` wrappers on each call,
    so the identity holds at ``__wrapped__``. Mutation: the builder copies a
    function or builds a stand-in of the same name → red."""
    F.flag_on(monkeypatch)
    module = _module()
    names = set(eg.HELD_CARD_SEND_TOOLS) | set(eg.CARD_BOUND_SEND_TOOLS)
    with _run(no_egress=True):
        first = {t.__name__: t for slug in sf.pack_slugs() for t in (await _build(slug)).tools}
        again = {t.__name__: t for slug in sf.pack_slugs() for t in (await _build(slug)).tools}
    assert names <= set(first), sorted(names - set(first))
    for name in names:
        tool = first[name]
        assert inspect.unwrap(tool) is getattr(module, name), name
        assert inspect.unwrap(again[name]) is inspect.unwrap(tool)
        assert again[name] is not tool, "each build wraps again"
        assert eg.is_card_bound_send(tool) is True, name


# ── 3. The mark comes before the tool body ───────────────────────────────────


async def test_the_session_mark_comes_before_the_tool_body(monkeypatch) -> None:
    """EM-G11f3: the first email call marks the chat before it reads any
    mail. Mutation: mark after the call, or not at all → red."""
    order: list[str] = []
    real = eg.mark_untrusted_session

    async def spy(agent_name: Any, ctx: Any = None) -> bool:
        order.append(f"mark:{agent_name}")
        return await real(agent_name, ctx)

    monkeypatch.setattr(eg, "mark_untrusted_session", spy)
    seen: dict[str, bool] = {}

    async def body() -> None:
        order.append("body")
        seen["marked"] = await eg.session_is_known_marked()

    with _run():
        build = await _build("email", _stub(_probe(body=body)))
        assert await eg.session_is_known_marked() is False
        await _named(build, "read_email")(email_id="m-1")
    assert order == [f"mark:{EA}", "body"]
    assert seen == {"marked": True}


# ── 4. A pack write asks its own card, never the untrusted card too ─────────


def _mailbox_gateway(monkeypatch, module: Any) -> list[tuple[str, Any]]:
    posts: list[tuple[str, Any]] = []

    async def get(path: str, params: Any = None) -> Any:
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": MEMBER, "display_label": "Work"}]
        return {}

    async def post(path: str, body: Any) -> Any:
        posts.append((path, body))
        return {"id": "k-1"}

    monkeypatch.setattr(module, "_get", get)
    monkeypatch.setattr(module, "_post", post)
    return posts


async def test_a_pack_write_asks_its_own_card_and_never_the_untrusted_card(
    monkeypatch,
) -> None:
    """The chat read mail during the run, so the gate of the ORCHESTRATOR
    would ask the untrusted card for an egress tool. The pack call runs in
    the scope of the owner, outside the gate, so it asks only its own card.
    Mutation: put the scope inside the gate, or drop it → two cards → red."""
    module = _module()
    posts = _mailbox_gateway(monkeypatch, module)
    own: list[str] = []
    untrusted: list[str] = []

    async def card(**kw: Any) -> bool:
        own.append(str(kw.get("title")))
        return True

    async def untrusted_card(tool: str, _kwargs: Any) -> bool:
        untrusted.append(tool)
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr(ti, "_untrusted_answer_card", untrusted_card)
    with _run():
        build = await _build("email-setup")
        assert build.held is False
        await eg.mark_untrusted_answer()  # an email call of this run read mail
        save = _named(build, "save_knowledge")
        assert ti._is_egress(save), "non-vacuity: an egress tool the gate would hold"
        out = await save(BOX, title="Prices", content="List A")
    assert untrusted == [], untrusted
    assert len(own) == 1 and "knowledge entry" in own[0], own
    assert [p for p, _ in posts] == ["/email/knowledge"], out


# ── 5. After the mark, the orchestrator's web tools ask the untrusted card ──


def _web_tool(name: str, ran: list[str]) -> Any:
    async def tool(url: str = "", query: str = "") -> str:
        ran.append(name)
        return f"{name} ran"

    tool.__name__ = name
    tool.__tool_risk__ = dict(_SENDS, read_only=True, destructive=False)  # type: ignore[attr-defined]
    return ti._gate_injected_tool(tool)


async def test_after_the_mark_the_orchestrators_web_tools_ask_the_untrusted_card(
    monkeypatch,
) -> None:
    """Owner answer 1 (2026-10-26): a run with the email pack keeps
    ``web_search`` and ``fetch_page`` behind the untrusted-answer card. The
    first email call marks the chat, so each later web call asks. Mutation:
    drop the mark of the pack call → the web tools run with no card → red."""
    cards: list[str] = []

    async def no_card(tool: str, _kwargs: Any) -> bool:
        cards.append(tool)
        return False

    monkeypatch.setattr(ti, "_untrusted_answer_card", no_card)
    ran: list[str] = []
    fetch, search = _web_tool("fetch_page", ran), _web_tool("web_search", ran)
    with _run():
        build = await _build("email", _stub(_probe()))
        assert await eg.session_is_marked() is False
        await _named(build, "read_email")(email_id="m-1")
        out_fetch = await fetch(url="https://evil.example/x")
        out_search = await search(query="x")
    assert cards == ["fetch_page", "web_search"]
    assert ran == [] and "did not run" in out_fetch and "did not run" in out_search
    # They stay on: no pack holds them, and the build never touches them.
    assert not {"fetch_page", "web_search"} & PACK_NAMES
    assert not {"fetch_page", "web_search"} & set(build.dropped)


# ── 6. After the mark, each store write refuses ──────────────────────────────


async def test_after_the_mark_each_store_write_refuses() -> None:
    """EM-G11f3: a chat that holds mail keeps no copy of it in a memory store
    or a note. Mutation: drop the mark of the pack call → red."""
    ran: list[str] = []

    def _store(name: str) -> Any:
        async def tool(text: str = "") -> str:
            ran.append(name)
            return "saved"

        tool.__name__ = name
        return ti._gate_injected_tool(tool)

    writes = {name: _store(name) for name in sorted(eg.STORE_WRITES)}
    with _run():
        build = await _build("email", _stub(_probe()))
        for tool in writes.values():  # non-vacuity: before the mark they run
            assert await tool(text="x") == "saved"
        assert ran == sorted(eg.STORE_WRITES)
        await _named(build, "read_email")(email_id="m-1")
        ran.clear()
        for name, tool in writes.items():
            assert await tool(text="x") == eg.MAIL_STORE_WRITE_REFUSED.format(tool=name)
        assert await eg.run_holds_mail("orchestrator") is True
    assert ran == []


# ── 7. No registry MCP server beside the email pack ──────────────────────────


async def test_a_run_with_the_email_pack_gets_no_registry_mcp_server(monkeypatch) -> None:
    """Mutation: drop ``_holds_untrusted_pack`` from ``_inject_mcp_servers``,
    or drop the owner mark of a pack tool → red."""
    import gateway.connections.mcp as mcp_registry
    from agent_framework._tools import normalize_tools
    from orchestrator import _maf_mcp

    reads: list[str] = []

    async def servers_for_org(org: str, **_k: Any) -> list[Any]:
        reads.append(org)
        return []

    monkeypatch.setattr(mcp_registry, "servers_for_org", servers_for_org)
    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: True)
    monkeypatch.setattr(_maf_mcp, "is_native_maf_agent", lambda _a: True)
    with _run():
        build = await _build("email", _stub(_probe()))
    plain = types.SimpleNamespace(default_options={"tools": [_probe("list_spaces")]})
    await ti._inject_mcp_servers(plain, "orchestrator", organization_id=ORG)
    assert reads == [ORG], "non-vacuity: a run with no pack reads the registry"
    for tools in (list(build.tools), normalize_tools(list(build.tools))):
        agent = types.SimpleNamespace(default_options={"tools": tools})
        await ti._inject_mcp_servers(agent, "orchestrator", organization_id=ORG)
    assert reads == [ORG]


# ── 8. Each reader of agent_name reads the owner inside a pack call ─────────


async def test_each_reader_of_agent_name_reads_the_owner_inside_a_pack_call() -> None:
    """Mutation: the scope binds no ``agent_name``, or binds the run's own.
    Each reader below then reads ``orchestrator`` → red."""
    from acb_skills import system_one, whatsapp_acts

    wa = _write_artifact_module()
    seen: dict[str, Any] = {}

    async def body() -> None:
        ctx = wa.artifact_context()
        seen["artifact_context"] = ctx.get("agent_name")
        seen["egress.hold_is_for_mail"] = eg.hold_is_for_mail()
        seen["ti._run_reads_mail_itself"] = ti._run_reads_mail_itself()
        seen["ti._run_holds_mail_here"] = await ti._run_holds_mail_here()
        seen["ti._delegated_no_egress"] = ti._delegated_no_egress(ctx, "crm-assistant")
        seen["write_artifact._current_agent_name"] = wa._current_agent_name()
        seen["write_artifact.delegation_target"] = (wa.delegation_target(ctx) or {}).get(
            "agent_name")
        seen["system_one._calling_agent"] = system_one._calling_agent()
        seen["whatsapp_acts._run_agent"] = whatsapp_acts._run_agent()

    with _run():
        build = await _build("email", _stub(_probe(body=body)))
        await _named(build, "read_email")(email_id="m-1")
        outside = {"agent_name": artifact_context().get("agent_name"),
                   "reads_mail_itself": ti._run_reads_mail_itself()}
    assert seen == {
        "artifact_context": EA,
        "egress.hold_is_for_mail": True,
        "ti._run_reads_mail_itself": True,
        "ti._run_holds_mail_here": True,
        "ti._delegated_no_egress": True,
        "write_artifact._current_agent_name": EA,
        "write_artifact.delegation_target": EA,
        "system_one._calling_agent": EA,
        "whatsapp_acts._run_agent": EA,
    }
    assert outside == {"agent_name": "orchestrator", "reads_mail_itself": False}


async def test_the_card_row_names_the_runs_agent_as_for_a_delegation(monkeypatch) -> None:
    """``pending_ask`` reads the agent of a card row from the run record of
    the thread (``_run_facts``), never from the artifact context. So a card
    of a pack call names the run's agent, as a card of a delegated sub-run
    does. Mutation: read ``agent_name`` from the artifact context there →
    the row names the owner → red."""
    from orchestrator import pending_ask, run_liveness, stream_relay

    class _Redis:
        async def hget(self, _key: str, _field: str) -> str:
            return json.dumps({"org": ORG, "runId": "r-1", "agent": "orchestrator"})

    async def client() -> Any:
        return _Redis()

    async def actor(_thread: str) -> str:
        return MEMBER

    async def bump(*_a: Any) -> None:
        return None

    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(stream_relay, "_get_client", client)
    monkeypatch.setattr(stream_relay, "get_run_actor", actor)
    monkeypatch.setattr(run_liveness, "instance_runs_key", lambda _i: "k")
    monkeypatch.setattr(pending_ask, "insert_ask", lambda _org, row: rows.append(row) or True)
    monkeypatch.setattr(pending_ask, "_bump_my_day", bump)
    ask = pending_ask.CardAsk(request_id="q-1", kind="confirm", question="Send?", payload={})

    async def body() -> None:
        await pending_ask._record(SESSION, ask)

    with _run():
        build = await _build("email", _stub(_probe(body=body)))
        await _named(build, "read_email")(email_id="m-1")
        await pending_ask._record(SESSION, ask)
    assert [r["agent_name"] for r in rows] == ["orchestrator", "orchestrator"]


# ── 9. One seam: the builder calls, and copies no body ──────────────────────


async def test_the_held_drop_follows_is_egress(monkeypatch) -> None:
    """The builder calls ``_is_egress`` and copies none of its body. Mutation:
    test the annotation in the builder itself → the patched rule is not
    followed → red."""
    stub = _stub(_probe("read_email"), _probe("send_email", _SENDS))
    with _run(no_egress=True):
        assert (await _build("email", stub)).dropped == ("send_email",)
        monkeypatch.setattr(ti, "_is_egress", lambda t: ti._tool_name(t) == "read_email")
        assert (await _build("email", stub)).dropped == ("read_email",)
        monkeypatch.setattr(ti, "_is_egress", lambda t: False)
        held = await _build("email", stub)
    assert held.dropped == () and {t.__name__ for t in held.tools} == {
        "read_email", "send_email"}


async def test_the_builder_calls_the_held_rule_the_gate_and_the_scope(monkeypatch) -> None:
    """``_run_no_egress``, ``_gate_injected_tool`` and
    ``artifact_context_scope`` are called, not copied. Mutation: inline any
    of them in the builder → its spy reads zero → red."""
    wa = _write_artifact_module()
    held_asked: list[str] = []
    gated: list[str] = []
    scopes: list[int] = []
    real_gate, real_scope = ti._gate_injected_tool, wa.artifact_context_scope

    def spy_held(agent: Any, ctx: Any, org: Any = None) -> bool:
        held_asked.append(str(agent))
        return True

    def spy_gate(fn: Any) -> Any:
        gated.append(fn.__name__)
        return real_gate(fn)

    @contextlib.contextmanager
    def spy_scope() -> Iterator[None]:
        scopes.append(1)
        with real_scope():
            yield

    monkeypatch.setattr(ti, "_run_no_egress", spy_held)
    monkeypatch.setattr(ti, "_gate_injected_tool", spy_gate)
    monkeypatch.setattr(wa, "artifact_context_scope", spy_scope)
    stub = _stub(_probe("read_email"), _probe("send_email", _SENDS))
    with _run():
        build = await _build("email", stub)
        await _named(build, "read_email")(email_id="m-1")
    assert held_asked == [EA] and build.held is True
    assert gated == ["read_email"] and build.dropped == ("send_email",)
    assert scopes == [1]


def _code_of(fn: Any) -> str:
    """The code of *fn* with no docstring, so a word of the text never counts."""
    import ast
    import textwrap

    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    for item in ast.walk(node):
        body = getattr(item, "body", None)
        if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and body
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)):
            item.body = body[1:] or [ast.Pass()]
    return ast.unparse(node)


def test_the_builder_copies_no_body_of_the_seam() -> None:
    """Mutation: paste the body of a guard into the builder."""
    source = "".join(_code_of(fn) for fn in (
        ti._pack_tools, ti._pack_held, ti._pack_call, ti._mark_pack_session,
        ti._record_pack_call))
    assert "_is_egress(" in source and "_run_no_egress(" in source, "non-vacuity"
    for body in ("is_egress_tool(", "HOST_NETWORK_TOOLS", "_delegated_no_egress(",
                 "_run_covered(", "_permission_gate(", "derive_artifact_context(",
                 "UNTRUSTED_INPUT_AGENTS", "EgressGuardProvider"):
        assert body not in source, body


# ── 10. The __cc_gated__ mark ─────────────────────────────────────────────────


async def test_each_pack_tool_carries_the_gated_mark(monkeypatch) -> None:
    """OA-7c puts pack tools in the list before ``_inject_agent_tools``, and
    ``_gate_own_maf_tools`` wraps each tool with no ``__cc_gated__``. A
    second gate would show a second card in a marked chat. Mutation: drop the
    mark → the assembly wraps the tool again and the gate runs twice → red."""
    from agent_framework._tools import normalize_tools

    calls: list[str] = []
    real = ti._permission_gate

    def spy(tool_name: str, *a: Any, **k: Any) -> Any:
        calls.append(tool_name)
        return real(tool_name, *a, **k)

    monkeypatch.setattr(ti, "_permission_gate", spy)
    with _run():
        build = await _build("email", _stub(_probe()))
        assert all(t.__cc_gated__ is True for t in build.tools)
        tools = normalize_tools(list(build.tools))
        funcs = [t.func for t in tools]
        assert ti._gate_own_maf_tools(tools) == 0
        assert [t.func for t in tools] == funcs
        await tools[0].func(email_id="m-1")
    assert calls == ["read_email"], "one gate, so one card at most"


# ── The drop at load is the only held-set control ───────────────────────────


async def test_the_drop_at_load_is_the_only_held_set_control(monkeypatch) -> None:
    """Audit of 2026-10-26 (§4.5): ``EgressGuardProvider`` adds
    ``WithholdEgressTools`` and ``RefuseEgressTools`` to a held run of the
    owner. A pack run gets neither, so the drop at load is the ONLY control
    of the held set. This test says so: with the drop gone, a held pack holds
    ``save_knowledge`` and its call reaches the gateway, and nothing else
    stops it. Mutation: a later change that relies on a middleware behind
    the pack and drops less at load → the first assertion goes red."""
    module = _module()
    posts = _mailbox_gateway(monkeypatch, module)

    async def card(**_kw: Any) -> bool:
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    with _run(no_egress=True):
        held = await _build("email-setup")
        assert "save_knowledge" in held.dropped
        assert "save_knowledge" not in {t.__name__ for t in held.tools}
        monkeypatch.setattr(ti, "_is_egress", lambda _t: False)
        open_door = await _build("email-setup")
        await _named(open_door, "save_knowledge")(BOX, title="Prices", content="List A")
    assert [p for p, _ in posts] == ["/email/knowledge"], "no middleware stood behind the drop"
    for tool in held.tools:
        assert inspect.isfunction(tool), "a pack tool is a plain function with no provider"


# ── A pack tool acts only in the run that loaded it ─────────────────────────


async def test_a_pack_tool_acts_only_in_the_run_that_loaded_it() -> None:
    """The held answer belongs to one run. Mutation: drop the run check →
    a tool built in an open turn acts in a later turn → red."""
    ran: list[str] = []

    async def body() -> None:
        ran.append("body")

    with _run(run_id="r-turn-1"):
        build = await _build("email", _stub(_probe(body=body)))
    tool = _named(build, "read_email")
    with _run(run_id="r-turn-2"):
        out = await tool(email_id="m-1")
    assert out == ti.PACK_CALL_REFUSED.format(tool="read_email")
    assert await tool(email_id="m-1") == out, "a frame with no run is refused too"
    assert ran == []


# ── The working set, the profile writes and the names the run holds ─────────


async def test_each_email_call_writes_the_working_set(monkeypatch) -> None:
    """The builder calls the executor's two functions, which the delegated
    path calls after an email sub-run. Mutation: drop the record → red."""
    import asyncio

    from orchestrator import executor as ex

    seen: list[tuple[str, Any, Any]] = []

    async def before(agent: str, ctx: Any) -> tuple[dict[str, str], str]:
        return {}, ""

    async def record(agent: str, ctx: Any, ws: Any, calls: Any, emit: Any) -> None:
        seen.append((agent, ctx.get("session_id"), calls))
        await emit({"type": "CUSTOM", "name": "mark"})

    monkeypatch.setattr(ex, "_email_working_set_for", before)
    monkeypatch.setattr(ex, "_record_email_working_set", record)
    queue: asyncio.Queue[Any] = asyncio.Queue()
    token = ex._active_run_queue.set(queue)
    try:
        with _run():
            build = await _build("email", _stub(_probe()))
            await _named(build, "read_email")("m-7")
    finally:
        ex._active_run_queue.reset(token)
    assert seen == [(EA, SESSION, [{"id": "", "name": "read_email",
                                    "args": {"email_id": "m-7"}, "result": "read m-7",
                                    "success": True}])]
    assert queue.get_nowait() == {"type": "CUSTOM", "name": "mark"}


async def test_each_pack_call_closes_the_profile_writes() -> None:
    """PZ-6a: a call that hands the model mail closes the profile writes,
    also after a raise. Mutation: drop the close, or close only on success."""
    from acb_common import assistant_profile as ap

    async def boom() -> None:
        raise RuntimeError("tool failed")

    for tool in (_probe(), _probe(body=boom)):
        token = ap.bind_writes(True)
        try:
            with _run():
                build = await _build("email", _stub(tool))
                with contextlib.suppress(RuntimeError):
                    await _named(build, "read_email")(email_id="m-1")
            assert ap.writes_open() is False
        finally:
            ap.release_writes(token)


async def test_a_name_that_the_run_holds_stays_the_runs() -> None:
    """Mutation: the builder adds a tool whose name the run already holds,
    so a pack tool shadows a tool of the run."""
    stub = _stub(_probe("read_email"), _probe("read_thread"))
    with _run(run_tools=frozenset({"read_thread"})):
        build = await _build("email", stub, holds=("read_email",))
    assert build.tools == () and set(build.skipped) == {"read_email", "read_thread"}


async def test_a_slug_that_is_no_pack_builds_nothing() -> None:
    with _run():
        assert await ti._pack_tools("memory", artifact_context()) is None


# ── The one owner loader (OA-7a verifier F2 and F3) ─────────────────────────


@pytest.fixture()
def owner_dir(monkeypatch, tmp_path) -> Any:
    import gateway.routes.agent as routes_agent

    monkeypatch.setattr(ti, "_PACK_OWNER_MODULES", {})
    entries = [{"name": "probe-owner", "local_path": str(tmp_path)}]
    monkeypatch.setattr(routes_agent, "_AGENT_REGISTRY", entries)
    return tmp_path


def _owner_source(path: Any, last_line: str) -> None:
    count = (path / "imports.txt").as_posix()
    (path / "agents.py").write_text(
        f"with open({count!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write('x')\n" + last_line + "\n", encoding="utf-8")


def test_an_owner_that_fails_to_import_is_tried_once(owner_dir) -> None:
    """F2: a failed import is kept, so a catalog GET (two packs of one owner)
    does not import it again. Mutation: keep only a success → red."""
    import gateway.routes.integrations_skills as sk

    _owner_source(owner_dir, "raise RuntimeError('broken owner')")
    assert ti._pack_owner_module("probe-owner") is None
    assert sk._owner_module("probe-owner") is None
    assert (owner_dir / "imports.txt").read_text(encoding="utf-8") == "x"


def test_an_owner_that_exits_at_import_cannot_stop_the_process(owner_dir) -> None:
    """F3: ``SystemExit`` at import is a failed import. Mutation: catch
    ``Exception`` only → ``SystemExit`` escapes → red."""
    import sys

    _owner_source(owner_dir, "raise SystemExit(3)")
    assert ti._pack_owner_module("probe-owner") is None
    assert "_tool_pack_owner_probe_owner" not in sys.modules


def test_a_keyboard_interrupt_at_import_still_propagates(owner_dir) -> None:
    """F3: only ``SystemExit`` joins the failures. Mutation: catch every
    ``BaseException`` → red."""
    import sys

    _owner_source(owner_dir, "raise KeyboardInterrupt")
    with pytest.raises(KeyboardInterrupt):
        ti._pack_owner_module("probe-owner")
    assert "_tool_pack_owner_probe_owner" not in sys.modules


def test_a_good_owner_loads_once_for_the_process(owner_dir) -> None:
    """Mutation: a builder that imports the owner on each build."""
    import gateway.routes.integrations_skills as sk

    _owner_source(owner_dir, "def _agent_tools():\n    return []")
    first = ti._pack_owner_module("probe-owner")
    assert first is not None and sk._owner_module("probe-owner") is first
    assert ti._pack_owner_module("probe-owner") is first
    assert (owner_dir / "imports.txt").read_text(encoding="utf-8") == "x"
