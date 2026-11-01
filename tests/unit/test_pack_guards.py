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

from acb_skills import agent_tools as at
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import subagent_contract as sc
from acb_skills import tool_annotations as ta
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
    """The orchestrator's run of a member's own chat, bound by the server.

    OA-7c: the run check of a pack call keys on ``tool_packs.PACK_RUN_KEY``,
    which the server mints for each run. Here each run id gets its own key,
    and an empty run id gets none."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.tool_packs import PACK_RUN_KEY

    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            values = {"run_id": run_id, "session_id": SESSION, "agent_name": "orchestrator",
                      "member": MEMBER, "workspace_root": "/tmp/orch-ws", "no_egress": False,
                      PACK_RUN_KEY: f"key-{run_id}" if run_id else ""}
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
        # Another process marked the chat (Redis holds it, this process does
        # not), so the call takes the open path, and the gate of the
        # ORCHESTRATOR would ask the untrusted card.
        await eg.mark_untrusted_answer()
        with eg._MARKED_LOCK:
            eg._MARKED_SESSIONS.clear()
        assert eg.read_untrusted_answer() is False, "this process holds no mark"
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
    # OA-7c condition 3: a client can choose the run id, so a frame with the
    # same run id and another server key is refused too.
    from acb_skills.tool_packs import PACK_RUN_KEY

    with _run(run_id="r-turn-1", **{PACK_RUN_KEY: "key-of-another-run"}):
        assert await tool(email_id="m-1") == out, "the run id alone opens nothing"
    assert ran == []


# ── Fix round 1: the scope supplies the held answer (F1) ────────────────────


def _held_spies(monkeypatch, module: Any) -> dict[str, list[Any]]:
    """Spies on the two held-card paths of the email module (§21). Each
    records its call and sends nothing."""
    seen: dict[str, list[Any]] = {"new": [], "forward": []}

    async def held_new(*a: Any, **k: Any) -> str:
        seen["new"].append(k)
        return "HELD NEW MAIL CARD"

    async def held_forward(*a: Any, **k: Any) -> str:
        seen["forward"].append(k)
        return "HELD FORWARD CARD"

    monkeypatch.setattr(module, "_held_new_mail", held_new)
    monkeypatch.setattr(module, "_held_forward", held_forward)
    return seen


async def test_the_pack_scope_supplies_the_held_answer(monkeypatch) -> None:
    """The orchestrator's run is open on its own frame. The chat read mail,
    so the build is held, and only ``owner_scope`` can tell the tool body.
    Mutation: ``owner_scope`` binds no ``no_egress`` → the body reads an
    open run, the FORWARD rule saves and ``send_email`` takes the open path
    → red."""
    F.flag_on(monkeypatch)
    module = _module()
    posts = _mailbox_gateway(monkeypatch, module)
    seen_held = _held_spies(monkeypatch, module)

    async def card(**_kw: Any) -> bool:
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    inside: dict[str, bool] = {}

    async def body() -> None:
        inside["no_egress_for_this_run"] = eg.no_egress_for_this_run()
        inside["module._egress_held"] = module._egress_held()

    with _run():
        assert eg.no_egress_for_this_run() is False, "non-vacuity: the outer run is open"
        await eg.mark_untrusted_answer()
        probe = await _build("email", _stub(_probe(body=body)))
        assert probe.held is True
        await _named(probe, "read_email")(email_id="m-1")
        setup = await _build("email-setup")
        rule = await _named(setup, "create_rule")(
            BOX, name="Out", action_type="FORWARD", forward_to="out@evil.test")
        mail = await _build("email")
        sent = await _named(mail, "send_email")(BOX, body="hi", to=[F.KNOWN], subject="x")
    assert inside == {"no_egress_for_this_run": True, "module._egress_held": True}
    assert rule.startswith("Nothing changed.") and "is off now" in rule, rule
    assert posts == []
    assert sent == "HELD NEW MAIL CARD" and len(seen_held["new"]) == 1


# ── Fix round 1: a call after the chat read mail in this turn is held (F2) ──


@pytest.mark.parametrize("switch", [False, True], ids=["switch-off", "switch-on"])
async def test_a_call_after_mail_in_the_same_turn_runs_held(monkeypatch, switch: bool) -> None:
    """Owner choice, STRICT: the build of a clean chat is open, and the
    first email call reads mail. Each later call of the same turn runs held.
    With the switch off a forward is refused. With the switch on it goes to
    the held card. A FORWARD rule is refused either way. Mutation: drop the
    hold within the turn → the forward takes the open path → red."""
    (F.flag_on if switch else F.flag_off)(monkeypatch)
    module = _module()
    posts = _mailbox_gateway(monkeypatch, module)
    seen_held = _held_spies(monkeypatch, module)
    cards: list[Any] = []

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    with _run():
        mail = await _build("email")
        setup = await _build("email-setup")
        assert mail.held is False and "forward_email" in {t.__name__ for t in mail.tools}
        with contextlib.suppress(Exception):
            await _named(mail, "query_inbox")(query="Navy order")
        assert eg.read_untrusted_answer() is True, "the first call marked the chat"
        forward = await _named(mail, "forward_email")(F.MAIL, to=[F.STRANGER])
        rule = await _named(setup, "create_rule")(
            BOX, name="Out", action_type="FORWARD", forward_to="out@evil.test")
    if switch:
        assert forward == "HELD FORWARD CARD" and len(seen_held["forward"]) == 1
    else:
        assert forward == eg.EGRESS_HELD_PACK_ANSWER.format(name="forward_email")
        assert seen_held["forward"] == []
    assert rule.startswith("Nothing changed.") and "is off now" in rule, rule
    assert [p for p, _ in posts if p.startswith("/email/rules")] == []
    assert cards == [], "a held act asks no open card"


async def test_the_first_call_of_a_clean_turn_stays_open(monkeypatch) -> None:
    """The hold starts after the first email call, not before it. Mutation:
    read the mark after the call marks the chat → the first call is held."""
    F.flag_off(monkeypatch)
    module = _module()
    seen_held = _held_spies(monkeypatch, module)
    calls: list[str] = []

    async def post(path: str, body: Any) -> Any:
        calls.append(path)
        return {"id": "fwd-1"}

    async def get(path: str, params: Any = None) -> Any:
        return {"account_id": BOX, "subject": "Quote", "from_address": {"email": F.KNOWN}}

    async def card(**_kw: Any) -> bool:
        return False  # the member says no, so nothing is sent

    monkeypatch.setattr(module, "_post", post)
    monkeypatch.setattr(module, "_get", get)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    with _run():
        mail = await _build("email")
        out = await _named(mail, "forward_email")(F.MAIL, to=[F.KNOWN])
    assert "off now" not in out and seen_held["forward"] == [], out
    assert calls == [], "the member said no to the open card"


# ── Fix round 1: the held answer fails closed (item 3) ──────────────────────


class _DownRedis:
    async def exists(self, *_a: Any) -> int:
        raise ConnectionError("redis down")

    async def expire(self, *_a: Any) -> bool:
        raise ConnectionError("redis down")

    async def setex(self, *_a: Any) -> bool:
        raise ConnectionError("redis down")


async def test_a_redis_fault_builds_a_held_pack(monkeypatch) -> None:
    """A delegation reads a fault as no mark. A pack reads it as held.
    Mutation: read only a KNOWN mark → red."""
    from acb_common.tenant_redis import TenantRedis

    monkeypatch.setattr(eg, "get_tenant_redis", lambda: TenantRedis(_DownRedis()))
    with _run(member=""):  # no member: a card cannot show, and it still holds
        build = await _build("email")
    assert build.held is True and "send_email" in build.dropped


async def test_a_run_with_no_chat_key_builds_a_held_pack() -> None:
    """No session or no tenant: no mark can be read, so the pack is held.
    Mutation: drop the ``_session_of`` check → red."""
    with _run(session_id=None):
        assert (await _build("email")).held is True
    with artifact_context_scope():
        bind_artifact_context(run_id="r-1", session_id=SESSION, agent_name="orchestrator",
                              no_egress=False)
        assert (await _build("email")).held is True  # no tenant bound


# ── Fix round 1: no run key, no build (item 5, keyed by OA-7c) ──────────────


async def test_a_run_with_no_run_key_builds_no_tool() -> None:
    """The run check of each call keys on the value that the server mints
    (``tool_packs.PACK_RUN_KEY``, OA-7c condition 3). Mutation: build with an
    empty key, so a call of any frame with no key passes → red. A run id
    alone builds nothing, because a client can set it."""
    from acb_skills.tool_packs import PACK_RUN_KEY

    with _run(run_id=""):
        build = await _build("email", _stub(_probe()))
    assert build.tools == () and build.held is True
    with _run(run_id="r-client-chosen", **{PACK_RUN_KEY: ""}):
        build = await _build("email", _stub(_probe()))
    assert build.tools == () and build.held is True


# ── Fix round 1: the held answer of a pack call (item 7) ────────────────────


@pytest.mark.parametrize("switch", [False, True], ids=["switch-off", "switch-on"])
def test_a_pack_call_reads_its_own_held_answer(monkeypatch, switch: bool) -> None:
    """In a pack turn no other assistant called the run. Mutation: give the
    pack call the delegation text → "Another assistant" → red."""
    (F.flag_on if switch else F.flag_off)(monkeypatch)
    pack_ctx = {"agent_name": EA, eg.PACK_CALL_KEY: True}
    text = eg.egress_held_answer("forward_email", pack_ctx)
    want = eg.EGRESS_HELD_PACK_ANSWER_CARDS if switch else eg.EGRESS_HELD_PACK_ANSWER
    assert text == want.format(name="forward_email")
    for claim in ("Another assistant", "Email app", "every app", "any app",
                  "every later turn", "new conversation", "by hand", "search the web"):
        assert claim not in text, claim
    delegated = eg.egress_held_answer("forward_email", {"agent_name": EA})
    assert delegated.startswith("forward_email is off in this run. Another assistant")


async def test_the_owner_scope_marks_the_call_as_a_pack_call() -> None:
    """Mutation: ``owner_scope`` sets no ``PACK_CALL_KEY``."""
    from acb_skills.tool_packs import owner_scope

    with _run(), owner_scope(EA, True):
        assert artifact_context().get(eg.PACK_CALL_KEY) is True
    assert artifact_context().get(eg.PACK_CALL_KEY) is None


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
    monkeypatch.setattr(ti, "_PACK_OWNER_FAILED", {})
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


def test_a_failed_import_is_kept_for_a_window_only(owner_dir, monkeypatch) -> None:
    """Fix round 1, item 4: a failure is kept for ``PACK_OWNER_RETRY_S``,
    not until the process restarts, so a fixed owner loads again. Mutation:
    keep a failure for ever, or never keep one → red."""
    now = {"t": 1000.0}
    monkeypatch.setattr(ti, "_pack_clock", lambda: now["t"])
    _owner_source(owner_dir, "raise RuntimeError('broken owner')")
    assert ti._pack_owner_module("probe-owner") is None
    now["t"] += ti.PACK_OWNER_RETRY_S - 1
    assert ti._pack_owner_module("probe-owner") is None
    assert (owner_dir / "imports.txt").read_text(encoding="utf-8") == "x"
    # The fix lands. A comment makes its size differ, so the bytecode cache of
    # the same second cannot give the old text back.
    _owner_source(owner_dir, "# fixed in this second\ndef _agent_tools():\n    return []")
    now["t"] += 2
    fixed = ti._pack_owner_module("probe-owner")
    assert fixed is not None and ti._pack_owner_module("probe-owner") is fixed
    assert ti.PACK_OWNER_RETRY_S == 60.0


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


# ── 11. The CRM pack (WS-67 OA-8a, §4.3.2) ──────────────────────────────────

CRM = "crm-assistant"
CRM_DIR = "apps/agents/agent-crm"
CRM_NINE = frozenset({
    "convert_lead", "create_lead", "crm_search_notes", "get_pipeline", "get_record",
    "get_timeline", "log_activity", "search_crm", "update_deal_status",
})
CRM_WRITES = frozenset({"create_lead", "update_deal_status", "log_activity", "convert_lead"})


def _crm_module() -> Any:
    return load_agent_module(CRM_DIR)


def _mode(monkeypatch, mode: str | None, calls: list[str] | None = None) -> None:
    """Patch the Mirror-mode read (``GET /crm/mode``). None makes it fail."""
    async def crm_mode() -> str | None:
        if calls is not None:
            calls.append("read")
        if mode is None:
            raise RuntimeError("gateway down")
        return mode

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)


async def test_the_built_crm_pack_holds_exactly_the_nine_tools(monkeypatch) -> None:
    """An empty or short pack must fail loudly: the builder skips a tool that
    is no coroutine function. Mutation: wrap one CRM tool in a ``FunctionTool``
    or rename it -> the pack holds 8 tools -> red."""
    _mode(monkeypatch, "live")
    with _run():
        build = await _build("crm-records", _crm_module())
    assert {t.__name__ for t in build.tools} == CRM_NINE and len(build.tools) == 9
    assert build.owner == CRM and build.held is False and build.skipped == ()


@pytest.mark.parametrize("layer", ["a-build", "b-load", "c-holds"])
async def test_mirror_mode_takes_the_three_record_writes_from_every_layer(
    monkeypatch, layer: str,
) -> None:
    """The drop lives in the builder, because a pack that ``load_skill_tools``
    (layer b) or a turn-kind item (layer c) loads builds AFTER the executor's
    ``_drop_mirror_record_writes``. Layer (a) is covered by order alone, but
    runs the same builder. ``log_activity`` stays. The three cases call the
    builder as each layer does: with the owner module, through
    ``_build_pack_for_load``, and with the names of a live list. Mutation:
    take the drop out of ``_pack_tools`` -> all 9 tools remain -> red."""
    _mode(monkeypatch, "mirror")
    with _run():
        if layer == "a-build":
            build = await _build("crm-records", _crm_module())
        elif layer == "b-load":
            build = await ti._build_pack_for_load("crm-records", artifact_context(), frozenset())
        else:
            build = await _build("crm-records", _crm_module(),
                                 holds=frozenset({"web_search", "read_attachment"}))
    names = {t.__name__ for t in build.tools}
    assert names == CRM_NINE - ti.MIRROR_RECORD_WRITES
    assert "log_activity" in names and not names & ti.MIRROR_RECORD_WRITES
    assert set(build.dropped) == ti.MIRROR_RECORD_WRITES


async def test_the_mirror_drop_reuses_the_executor_set(monkeypatch) -> None:
    """Mutation: copy the three names into a second set -> the set that the
    builder drops differs from ``MIRROR_RECORD_WRITES`` when that set grows."""
    _mode(monkeypatch, "mirror")
    monkeypatch.setattr(ti, "MIRROR_RECORD_WRITES", frozenset({"create_lead"}))
    with _run():
        build = await _build("crm-records", _crm_module())
    assert build.dropped == ("create_lead",)
    assert len(build.tools) == 8


@pytest.mark.parametrize("mode", ["live", None])
async def test_a_live_org_or_a_failed_mode_read_keeps_the_writes(monkeypatch, mode) -> None:
    """A failed read keeps the tools: the 409 relay of the write tools is the
    fallback, as in ``_drop_mirror_record_writes``. Mutation: drop on a failed
    read -> red for ``None``."""
    _mode(monkeypatch, mode)
    with _run():
        build = await _build("crm-records", _crm_module())
    assert {t.__name__ for t in build.tools} == CRM_NINE and build.dropped == ()


async def test_a_held_crm_pack_drops_the_four_writes_and_reads_no_mode(monkeypatch) -> None:
    """A held pack (the chat read mail) drops each egress tool: the four
    writes. It reads nothing for the Mirror drop. Mutation: read the mode in a
    held build -> ``calls`` is not empty -> red."""
    calls: list[str] = []
    _mode(monkeypatch, "mirror", calls)
    with _run():
        await eg.mark_untrusted_answer()  # the chat read mail on an earlier turn
        build = await _build("crm-records", _crm_module())
    assert build.held is True and calls == []
    assert set(build.dropped) == CRM_WRITES
    assert {t.__name__ for t in build.tools} == CRM_NINE - CRM_WRITES


async def test_the_crm_held_drop_follows_is_egress(monkeypatch) -> None:
    """The pack drop is the one seam ``_is_egress``. Mutation: test the
    annotation in the builder -> the patched rule is not followed -> red."""
    _mode(monkeypatch, "live")
    module = _crm_module()
    with _run(no_egress=True):
        assert set((await _build("crm-records", module)).dropped) == CRM_WRITES
        monkeypatch.setattr(ti, "_is_egress", lambda t: ti._tool_name(t) == "get_record")
        assert (await _build("crm-records", module)).dropped == ("get_record",)
        monkeypatch.setattr(ti, "_is_egress", lambda t: False)
        open_build = await _build("crm-records", module)
    assert open_build.dropped == () and len(open_build.tools) == 9


async def test_a_marked_mail_chat_holds_the_crm_pack_and_shows_no_card(monkeypatch) -> None:
    """The chat read mail (``email`` pack), then the member asks for a CRM
    write. The CRM pack builds held: no write tool is on the list, so no card
    shows, the owner's or the untrusted one. Mutation: build the pack open in
    a marked chat -> ``create_lead`` is on the list -> red."""
    _mode(monkeypatch, "live")
    cards: list[str] = []

    async def card(**kw: Any) -> bool:
        cards.append(str(kw.get("title")))
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    with _run():
        await eg.mark_untrusted_session(EA)  # what an email pack call does first
        build = await _build("crm-records", _crm_module())
    assert build.held is True and "create_lead" not in {t.__name__ for t in build.tools}
    assert cards == []


async def test_a_crm_pack_run_with_no_user_refuses() -> None:
    """R5: the identity comes from the bound run. With none, the owner's
    ``_headers`` refuses before any request. Mutation: fall back to a default
    user in a pack call -> the call goes out -> red."""
    from acb_skills.memory_tools import _get_memory_user_id

    with _run():
        assert not _get_memory_user_id(), "non-vacuity: this run binds no user"
        build = await _build("crm-records", _crm_module())
        search = _named(build, "search_crm")
        with pytest.raises(RuntimeError, match="No acting user"):
            await search("Acme")


# ── OA-9c: packs in a spawn child (§6.7, the child case of §5) ───────────────
# The spawn builds the pack of a child on the parent frame, through this one
# builder (``agent_tools._child_pack_tools`` -> ``_pack_tools``), with a run
# key minted for that child. The child keeps the tools that
# ``subagent_contract.child_may_hold`` lets through: read tools only.

PROJECTS = "projects-assistant"
PROJECTS_DIR = "apps/agents/agent-projects"
_WRITE = {"read_only": False, "destructive": False, "idempotent": False, "open_world": False}

#: The tools that each real pack gives a child (2026-10-29). The one marker
#: decides: ``read_only=True`` in the risk registry, less the SUB-1 names and
#: ``DISPLAY_TOOLS``. No email tool says read-only, so an email pack gives a
#: child no tool and its job does not run.
CHILD_TOOLS: dict[str, frozenset[str]] = {
    "email": frozenset(),
    "email-setup": frozenset(),
    "crm-records": frozenset({
        "crm_search_notes", "get_pipeline", "get_record", "get_timeline", "search_crm"}),
    "projects": frozenset({
        "calendar", "find_tasks", "list_tasks", "my_task", "my_work", "people_for",
        "project_summary", "projects_tree", "task_detail", "vocabulary"}),
    "projects-plan": frozenset({
        "analytics_finished", "analytics_load", "analytics_outlook", "analytics_stuck",
        "analytics_throughput", "find_conflicts", "fit_for_task", "needs_attention",
        "rebalance", "recurrence", "report_list", "report_recipients",
        "report_render", "task_dataset", "team_capacity", "waiting_agent_requests"}),
    "projects-structure": frozenset({
        "intake_queue", "my_areas", "my_contexts", "notifications", "project_access",
        "project_views", "watchers"}),
    "projects-bulk": frozenset({"list_reminders", "my_led_projects"}),
    # STU-G1: STUDIO_ORGS holds no org here, so the pack builds no tool.
    "studio": frozenset(),
}

#: The calls that put an event on the run's stream: a card, a page move, a
#: panel. A tool that reaches one draws something in the member's chat. The
#: last two are the direct push that `todo_tools` and `ask_tools` use.
_DISPLAY_EMITTERS = frozenset({"emit_generative_ui", "emit_frontend_tool", "resolve_run_queue",
                               "_active_run_queue", "_push_sse_to_stream",
                               # STU-G2: a tool that awaits the confirmation card
                               "request_confirmation"})
#: The packages whose functions the scan follows. A call into any other
#: package (httpx, the standard library) is never a display emitter.
_SCAN_ROOTS = ("skill_projects", "acb_skills", "acb_common", "orchestrator",
               "_native_maf_probe", "_tool_pack_owner")


def _code_names(code: Any) -> set[str]:
    """Each global and attribute name that *code* and its nested code read."""
    import types

    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


def _display_path(fn: Any, seen: set[int] | None = None) -> str:
    """The call path from tool *fn* to a display emitter, or ``""``.

    It reads the tool, its ``func`` and each ``__wrapped__`` layer, and it
    follows each function that a layer names in its module globals, inside
    :data:`_SCAN_ROOTS`. So ``render_board > _emit > emit_generative_ui``."""
    seen = set() if seen is None else seen
    layer = getattr(fn, "func", fn)
    while layer is not None and id(layer) not in seen:
        seen.add(id(layer))
        code = getattr(layer, "__code__", None)
        if code is not None:
            names = _code_names(code)
            hit = sorted(names & _DISPLAY_EMITTERS)
            if hit:
                return f"{layer.__qualname__} > {hit[0]}"
            for name in sorted(names):
                target = getattr(layer, "__globals__", {}).get(name)
                if (inspect.isfunction(target)
                        and str(target.__module__).startswith(_SCAN_ROOTS)):
                    found = _display_path(target, seen)
                    if found:
                        return f"{layer.__qualname__} > {found}"
        layer = getattr(layer, "__wrapped__", None)
    return ""


def _owners(monkeypatch, **by_owner: Any) -> None:
    """The owner module that the builder loads for each owner (``_pack_owner_module``)."""
    monkeypatch.setattr(ti, "_pack_owner_module", lambda owner: by_owner.get(owner))


async def _child(*packs: str, held: bool = False) -> tuple[str, Any]:
    """The spawn's build of a child's packs, on this (parent) frame."""
    job = sc.Job(1, "Read.", (), "fast", packs=packs)
    return await at._child_pack_tools(job, artifact_context(), held=held)


def _no_egress_probe(name: str, seen: list[Any]) -> Any:
    async def body() -> None:
        seen.append(artifact_context().get("no_egress"))

    return _probe(name, body=body)


async def test_a_child_with_the_email_pack_gets_the_held_set_less_the_writes(
    monkeypatch,
) -> None:
    """§5, OA-9c, in a held chat: the main assistant's held set, read tools
    only. Mutation: drop the ``child_may_hold`` filter -> ``snooze_email``
    reaches the child. Drop the held drop -> ``send_email`` reaches both."""
    for name in ("read_email", "query_inbox"):
        monkeypatch.setitem(ta.TOOL_ANNOTATIONS, name, dict(_READ_ONLY))
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "snooze_email", dict(_WRITE))
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "send_email", dict(_SENDS))
    seen: list[Any] = []
    stub = _stub(_no_egress_probe("read_email", seen), _probe("query_inbox"),
                 _probe("send_email", _SENDS), _probe("snooze_email", _WRITE))
    _owners(monkeypatch, **{EA: stub})
    from acb_skills.tool_packs import PACK_RUN_KEY

    with _run():
        await eg.mark_untrusted_answer()  # the chat read mail on an earlier turn
        main = await _build("email", stub)
        why, job = await _child("email")
    assert why == "" and main.held is True and "send_email" in main.dropped
    main_names = {t.__name__ for t in main.tools}
    child = {t.__name__ for t in job.pack_tools}
    assert child == {n for n in main_names if sc.child_may_hold(n)}
    assert child == {"read_email", "query_inbox"}
    with _run(run_id="child-run", **{PACK_RUN_KEY: job.pack_run}):
        await next(t for t in job.pack_tools if t.__name__ == "read_email")(email_id="m-1")
    assert seen == [True], "the held answer reaches the child's call"


async def test_a_child_in_an_open_chat_gets_the_open_read_set_and_no_write(
    monkeypatch,
) -> None:
    """§5, OA-9c, in an open chat, on the real CRM owner: the main assistant
    holds the nine tools, and the child holds the five reads. No write and no
    send reaches the child (verdict acceptance, open chat)."""
    _mode(monkeypatch, "live")
    crm = _crm_module()
    _owners(monkeypatch, **{CRM: crm})
    with _run():
        main = await _build("crm-records", crm)
        why, job = await _child("crm-records")
    assert main.held is False and {t.__name__ for t in main.tools} == CRM_NINE
    child = {t.__name__ for t in job.pack_tools}
    assert why == "" and child == CRM_NINE - CRM_WRITES == CHILD_TOOLS["crm-records"]


async def test_an_email_child_builds_held_in_an_open_chat(monkeypatch) -> None:
    """The owner reads stranger text, so ``_with_pack_tools`` builds the pack
    held in a clean chat too. The spawn marks the chat before the child
    starts, so no pack call of a child may clear ``no_egress``. Mutation:
    build with ``held=False`` -> the call runs with ``no_egress`` False."""
    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "read_email", dict(_READ_ONLY))
    seen: list[Any] = []
    stub = _stub(_no_egress_probe("read_email", seen))
    _owners(monkeypatch, **{EA: stub})
    from acb_skills.tool_packs import PACK_RUN_KEY

    with _run():
        main = await _build("email", stub)
        (job,), stopped, held = await at._with_pack_tools(
            (sc.Job(1, "Read.", (), "fast", packs=("email",)),), artifact_context())
    assert main.held is False and stopped == () and held is True
    with _run(run_id="child-run", **{PACK_RUN_KEY: job.pack_run}):
        await job.pack_tools[0](email_id="m-1")
    assert seen == [True]


async def test_a_child_pack_tool_acts_only_under_its_own_key(monkeypatch) -> None:
    """The spawn mints one key for each child. The parent frame, a sibling's
    frame and a frame with no key all refuse the tool. Mutation: the spawn
    reuses the parent's key -> the parent frame runs it -> red."""
    ran: list[str] = []

    async def body() -> None:
        ran.append("body")

    monkeypatch.setitem(ta.TOOL_ANNOTATIONS, "read_email", dict(_READ_ONLY))
    _owners(monkeypatch, **{EA: _stub(_probe("read_email", body=body))})
    from acb_skills.tool_packs import PACK_RUN_KEY

    refused = ti.PACK_CALL_REFUSED.format(tool="read_email")
    with _run():
        _, one = await _child("email", held=True)
        _, two = await _child("email", held=True)
        tool = one.pack_tools[0]
        assert await tool(email_id="m-1") == refused, "the parent frame"
    assert await tool(email_id="m-1") == refused, "a frame with no run"
    with _run(run_id="child-2", **{PACK_RUN_KEY: two.pack_run}):
        assert await tool(email_id="m-1") == refused, "a sibling"
    with _run(run_id="child-1", **{PACK_RUN_KEY: one.pack_run}):
        assert await tool(email_id="m-1") == "read m-1"
    assert ran == ["body"]
    assert len({one.pack_run, two.pack_run, "key-r-1"}) == 3


@pytest.mark.parametrize("slug", sorted(CHILD_TOOLS))
async def test_the_tools_that_each_real_pack_gives_a_child(monkeypatch, slug: str) -> None:
    """The real owners, the real builder and the one marker. A pack with no
    read tool stops the job. Mutation: read another marker (``open_world``)
    -> the email writes reach a child -> red."""
    from acb_skills.tool_guard import tool_name

    _owners(monkeypatch, **{EA: _module(), CRM: _crm_module(),
                            PROJECTS: load_agent_module(PROJECTS_DIR),
                            "studio-builder": load_agent_module("apps/agents/agent-studio-builder")})
    with _run():
        why, job = await _child(slug, held=True)
    want = CHILD_TOOLS[slug]
    if not want:
        assert job is None and f"the tool pack '{slug}' has no read tool" in why
        return
    assert why == "" and {tool_name(t) for t in job.pack_tools} == want


def _pack_owners() -> dict[str, Any]:
    return {EA: _module(), CRM: _crm_module(), PROJECTS: load_agent_module(PROJECTS_DIR),
            "studio-builder": load_agent_module("apps/agents/agent-studio-builder")}


def test_each_pack_tool_that_draws_is_a_display_tool() -> None:
    """OA-9c review: a child's card would reach the member's chat, because a
    child copies the parent's run queue. No single marker names the tools that
    draw, so this scan does. Each read-only pack tool that reaches a display
    emitter must be in ``DISPLAY_TOOLS`` (or ``MEMBER_TOOLS``), so a new
    display tool fails here and never reaches a child. Mutation: drop
    ``render_board`` from ``DISPLAY_TOOLS`` -> red."""
    from acb_skills import tool_packs
    from acb_skills.tool_guard import tool_name

    owners = _pack_owners()
    draws: dict[str, str] = {}
    for slug in sf.pack_slugs():
        found = tool_packs.pack(slug)
        assert found is not None
        for fn in tool_packs.owner_tools(slug, owners[found.owner]):
            path = _display_path(fn)
            if path:
                draws[tool_name(fn)] = path
    read_draws = {n for n in draws if sc.annotated_read_only(n)}
    # STU-G2: ``studio_plan`` awaits a card. Mutation: take it out of
    # ``DISPLAY_TOOLS`` -> ``loose`` below holds it -> red.
    assert "studio_plan" in read_draws, f"non-vacuity: the scan finds the plan card {draws}"
    assert "studio_interview" not in draws  # it returns text for ask_questions, draws nothing
    assert {"render_board", "render_tasks", "render_timeline", "open_in_app",
            "show_view"} <= read_draws, f"non-vacuity: the scan finds the views {draws}"
    loose = {n: draws[n] for n in read_draws - sc.DISPLAY_TOOLS - sc.MEMBER_TOOLS}
    assert loose == {}, f"a read-only pack tool draws, and a child could hold it: {loose}"
    assert not any(sc.child_may_hold(n) for n in draws)


def test_each_platform_tool_that_draws_is_blocked_for_a_child() -> None:
    """The same scan over the platform tools that a job can name, and the
    orchestrator's ``show_in_app``. Each read-only one that draws is blocked
    by the parser (``DISPLAY_TOOLS`` or ``MEMBER_TOOLS``)."""
    import orchestrator.agents as pkg

    tools = [*ti._collect_injectable_platform_tools("orchestrator"), pkg._mod.show_in_app]
    draws = {ti._tool_name(fn): _display_path(fn) for fn in tools}
    draws = {n: p for n, p in draws.items() if p}
    assert {"show_in_app", "share_artifact", "emit_generative_ui"} <= set(draws), draws
    loose = {n: p for n, p in draws.items() if sc.annotated_read_only(n)
             and n not in sc.DISPLAY_TOOLS | sc.MEMBER_TOOLS}
    assert loose == {}
    for name in draws:
        plan = sc.parse_jobs(json.dumps([{"job": "Show.", "tools": [name]}]),
                             parent_tools={name})
        assert plan.jobs == (), f"the parser lets a child hold {name}"


def test_each_pack_tools_own_mark_equals_its_registry_mark() -> None:
    """``child_may_hold`` reads the risk registry BY NAME, as ``parse_jobs``
    does. The registry keeps the last module that named a tool. So a module of
    another agent with a tool of the same name could mark a pack write
    read-only. With every in-repo agent loaded, each pack tool's own mark must
    equal its registry mark. Mutation: register ``get_record`` as a write in
    another module -> red."""
    from pathlib import Path

    from acb_skills import tool_packs
    from acb_skills.tool_guard import tool_name

    owners = _pack_owners()
    own: dict[str, bool] = {}
    for slug in sf.pack_slugs():
        found = tool_packs.pack(slug)
        assert found is not None
        for fn in tool_packs.owner_tools(slug, owners[found.owner]):
            risk = getattr(getattr(fn, "func", fn), "__tool_risk__", None) or {}
            own[tool_name(fn)] = risk.get("read_only") is True
    root = Path(__file__).resolve().parents[2]
    for agents_py in sorted((root / "apps/agents").glob("*/agents.py")):
        load_agent_module(agents_py.parent.relative_to(root).as_posix())
    assert len(own) >= 150, "non-vacuity: the three owners build their tools"
    clash = {n: ro for n, ro in own.items() if sc.annotated_read_only(n) is not ro}
    assert clash == {}


def test_the_child_build_calls_the_one_builder_and_copies_no_body() -> None:
    """OA-9c calls ``_pack_tools`` and copies none of the held rule. Mutation:
    decide the held set in the spawn -> a body name shows here -> red."""
    src = inspect.getsource(at._child_pack_tools)
    for body in ("_is_egress", "_pack_held", "_run_no_egress", "is_egress_tool",
                 "session_mark_state"):
        assert body not in src, body
    assert "_pack_builder()" in src
    assert at._pack_builder() is ti._pack_tools
