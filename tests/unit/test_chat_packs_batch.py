"""G2: the read packs of a WhatsApp turn (``chat_reach.md`` §3.2, task 3494).

The member's own WhatsApp turn of the orchestrator is a batch run
(``bot_run._model_answer`` calls ``executor.run_agent``). With
``CHAT_PACKS_BATCH_ORGS`` naming the bound org, that turn holds the READ tools
of the packs that its words or its sticky packs choose. WhatsApp shows no
card (D115), so the turn holds no write tool, and the specialist of each
owner stays for the writes. A shared room, a delegated run, a workflow run, a
run off the WhatsApp channel and a sender with no verified member get no
pack. With the flag empty the run is byte-identical, and the pack turn is
never called.

Each case names, in its docstring, the mutation that turns it red (R7).

1. The flag ships off, and org B's flag does not open org A.
2. The gate of ``executor._start_pack_turn`` with ``whatsapp=True``.
3. The channel gate and the holder of the run boundary.
4. The apply: the read tools only, the specialist stays, the persona keeps
   its bytes.
5. Every tool of every pack: the batch subset holds no write, fail closed.
6. A write tool that reaches a pack call answers the WhatsApp refusal.
7. The sticky state of WhatsApp never reaches the web chat.
8. The real batch run: flag off is byte-identical, flag on loads and binds.

Run: ``uv run pytest tests/unit/test_chat_packs_batch.py -q``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

import skill_projects  # noqa: F401  (it registers the risk marks of its tools)
from acb_common import get_settings
from acb_common.assistant_profile import member_typed, profile_channel
from acb_common.db import bind_tenant, release_tenant
from acb_common.settings import Settings
from acb_skills import crm_tools  # noqa: F401  (it registers the CRM read marks)
from acb_skills import skill_families as sf
from acb_skills import tool_annotations as ta
from acb_skills import tool_packs as tp
from acb_skills.subagent_contract import annotated_read_only
from acb_skills.write_artifact import (
    artifact_context,
    artifact_context_scope,
    bind_artifact_context,
)
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store

REPO = Path(__file__).resolve().parents[2]
ORG = "0a0a0a0a-0000-4000-8000-0000000000c2"
ORG_B = "0a0a0a0a-0000-4000-8000-0000000000c3"
MEMBER = "jane@acme.test"
THREAD = "wa-thread-g2-1"
PA = "projects-assistant"
CRM = "crm-assistant"
EA = "email-assistant"
#: Words of a WhatsApp turn that name Projects, the CRM and mail.
WORDS = "show my overdue tasks, the crm deals and my inbox"


def _persona() -> str:
    """A persona that holds each delegation text once, as the real one does,
    so the web swap of the non-vacuity case can run."""
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    return "\n".join(["You are the one assistant of the company.",
                      *dict.fromkeys(PACK_DELEGATION_BULLETS.values())])


PERSONA = _persona()
PACKS_OF = {owner: [s for s in sf.pack_slugs() if sf.SKILL_FAMILIES[s].get("owner") == owner]
            for owner in (PA, CRM, EA)}


# ── Fixtures and helpers ─────────────────────────────────────────────────────


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Any:
    return install_memory_mark_store(monkeypatch)


def _flags(monkeypatch: pytest.MonkeyPatch, batch: str = ORG) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", "*")
    monkeypatch.setattr(settings, "tool_packs_orgs", "*")
    monkeypatch.setattr(settings, "chat_packs_batch_orgs", batch)


def _probe(name: str, calls: list[str] | None = None) -> Any:
    async def probe(**_kw: Any) -> str:
        if calls is not None:
            calls.append(name)
        return f"{name} done"

    probe.__name__ = name
    return probe


def _stub_owners(monkeypatch: pytest.MonkeyPatch, calls: list[str] | None = None) -> None:
    """Owner modules whose tools are probes with the real tool names. The
    risk registry keeps its real marks, so the filter reads the real answer.
    No owner filter runs, so no build reads the network."""
    for owner in (PA, CRM, EA):
        names = sorted({n for s in PACKS_OF[owner] for n in sf.SKILL_FAMILIES[s]["tools"]})
        stub = types.SimpleNamespace(
            _agent_tools=lambda names=names: [_probe(n, calls) for n in names])
        monkeypatch.setitem(ti._PACK_OWNER_MODULES, owner, stub)
    monkeypatch.setattr(ti, "_PACK_OWNER_FILTERS", {})
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())


@contextlib.contextmanager
def _tenant(org: str = ORG) -> Iterator[None]:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _start(**over: Any) -> Any:
    kw: dict[str, Any] = {"member": MEMBER, "delegated": False, "solo_room": True,
                          "batch_thread": False, "shows_cards": False, "whatsapp": True}
    org = over.pop("org", ORG)
    thread = over.pop("thread", THREAD)
    agent = over.pop("agent", "orchestrator")
    kw.update(over)
    return ex._start_pack_turn(agent, org, thread, **kw)


def _pack_calls(store: Any) -> list[tuple[str, str]]:
    return [c for c in store.calls if ":tool-packs" in c[1]]


def _key(space: str, org: str = ORG) -> str:
    return f"cc:{org}:{space}:{THREAD}:{tp._member_part(MEMBER)}"


def _specialists() -> list[Any]:
    out = []
    for owner, name in ((PA, "projects_assistant"), (CRM, "crm_assistant"),
                        (EA, "email_assistant")):
        async def tool(task: str = "") -> str:
            return "handed"

        tool.__name__ = name
        tool.__delegates_to__ = owner  # type: ignore[attr-defined]
        out.append(tool)
    return out


class _Orch:
    """The native MAF ``Agent`` shape of the orchestrator: its tools live in
    ``default_options``, beside its persona."""

    def __init__(self) -> None:
        self.name = "orchestrator"
        self.default_options: dict[str, Any] = {
            "tools": _specialists(), "instructions": PERSONA}


def _names(agent: Any) -> list[str]:
    return [ti._tool_name(t) for t in agent.default_options["tools"]]


def _writes() -> frozenset[str]:
    """A second list of the writes, apart from the risk registry."""
    from orchestrator._tool_injection import MIRROR_RECORD_WRITES

    return frozenset(tp.WRITE_KINDS) | tp.CHAT_FILE_TOOLS | frozenset(MIRROR_RECORD_WRITES)


# ── 1. The flag ──────────────────────────────────────────────────────────────


def test_the_flag_ships_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: a default other than empty, or a reader that opens on any
    list -> red."""
    assert Settings.model_fields["chat_packs_batch_orgs"].default == ""
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_packs_batch_orgs", "")
    assert not tp.packs_batch_on(ORG)
    monkeypatch.setattr(settings, "chat_packs_batch_orgs", f"x, {ORG.upper()}")
    assert tp.packs_batch_on(ORG) and not tp.packs_batch_on("")
    monkeypatch.setattr(settings, "chat_packs_batch_orgs", "*")
    assert tp.packs_batch_on(ORG_B)
    from acb_common import env_guard

    assert env_guard.is_platform_env("CHAT_PACKS_BATCH_ORGS"), "a tenant cannot set it"


def test_org_b_flag_does_not_open_org_a(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: the reader ignores the org, or ``_start_pack_turn`` skips the
    reader -> org A starts a read -> red."""
    _flags(monkeypatch, batch=ORG_B)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    assert not tp.packs_batch_on(ORG)
    assert _start() is None
    assert _pack_calls(store) == []


# ── 2. The gate of _start_pack_turn ──────────────────────────────────────────


async def test_a_verified_whatsapp_member_reads_in_its_own_namespace(
    store: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The member's own WhatsApp turn starts the read, in the WhatsApp
    namespace, with no mail pack. Mutation: ``begin_turn`` with no
    namespace, or no ``batch_allowed`` -> red."""
    _flags(monkeypatch)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    with member_typed(WORDS):
        task = _start()
    assert task is not None
    turn, allowed = await task
    assert turn.namespace == tp.BATCH_NAMESPACE
    assert {"projects", "crm-records", "calendar-day"} <= set(allowed)
    assert not set(allowed) & set(PACKS_OF[EA]), "a mail pack never loads on WhatsApp"
    assert ("hincrby", _key(tp.BATCH_NAMESPACE)) in _pack_calls(store)
    assert not [c for c in _pack_calls(store) if f":{tp.NAMESPACE}:" in c[1]]


@pytest.mark.parametrize("over", [
    {"member": "eve@acme.test"},       # not the verified member
    {"solo_room": False},              # a shared room
    {"solo_room": None},               # no answer from the run boundary
    {"delegated": True},               # a delegated run
    {"batch_thread": True},            # a workflow run (a minted thread)
    {"thread": ""},                    # no chat thread
    {"org": ""},                       # no bound org
    {"resume_tool": "create_task"},    # a continuation never runs here
    {"agent": "crm-assistant"},        # not the orchestrator
], ids=["other-member", "room", "no-solo", "delegated", "workflow", "no-thread",
        "no-org", "resume", "agent"])
def test_each_gate_refuses_with_no_state(
    store: Any, monkeypatch: pytest.MonkeyPatch, over: dict[str, Any],
) -> None:
    """Mutation: drop the gate that the id names -> a read starts -> red."""
    _flags(monkeypatch)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    assert _start(**over) is None
    assert _pack_calls(store) == []


def test_an_unverified_sender_gets_no_pack(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run with no verified member (a payload claim only). Mutation: drop
    the verified-member check -> red."""
    _flags(monkeypatch)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: "")
    assert _start() is None
    assert _pack_calls(store) == []


async def test_the_web_gate_is_unchanged(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The web turn still needs a card channel and reads the web namespace.
    Mutation: ``whatsapp`` defaults to True, or the web read takes the
    WhatsApp namespace -> red."""
    _flags(monkeypatch)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    assert _start(whatsapp=False) is None, "a web run with no card loads no pack"
    task = _start(whatsapp=False, shows_cards=True)
    assert task is not None
    turn, _allowed = await task
    assert turn.namespace == tp.NAMESPACE
    assert ("hincrby", _key(tp.NAMESPACE)) in _pack_calls(store)


# ── 3. The channel gate and the holder ───────────────────────────────────────


def test_only_the_whatsapp_channel_with_the_flag_is_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: drop the ``channel()`` check -> a web batch run is due -> red."""
    _flags(monkeypatch)
    assert not ex._whatsapp_pack_turn_due("orchestrator", ORG), "channel chat"
    with profile_channel("whatsapp", writes=False):
        assert ex._whatsapp_pack_turn_due("orchestrator", ORG)
        assert not ex._whatsapp_pack_turn_due("crm-assistant", ORG)
        assert not ex._whatsapp_pack_turn_due("orchestrator", ORG_B)
        _flags(monkeypatch, batch="")
        assert not ex._whatsapp_pack_turn_due("orchestrator", ORG)


def test_the_holder_reads_the_run_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """The org is the run binding's and must equal the caller's stamp. The
    delegation answer is the parent's. Mutation: take the org from the
    argument alone -> the mismatch case gets a holder -> red."""
    _flags(monkeypatch, batch="*")
    kw: dict[str, Any] = {"run_id": "r-g2", "session_user": MEMBER, "parent_ctx": {},
                          "batch_thread": False, "no_egress": False, "is_authorized": None}
    with profile_channel("whatsapp", writes=False), _tenant():
        held = ex._batch_packs_for_run("orchestrator", ORG, THREAD, **kw)
        assert held is not None and held.organization_id == ORG and held.member == MEMBER
        assert held.run_ctx["session_id"] == THREAD and held.delegated is False
        assert ex._batch_packs_for_run("orchestrator", ORG_B, THREAD, **kw) is None
        sub = ex._batch_packs_for_run(
            "orchestrator", ORG, THREAD, **{**kw, "parent_ctx": {"run_id": "p"}})
        assert sub is not None and sub.delegated is True
    with profile_channel("whatsapp", writes=False):
        assert ex._batch_packs_for_run("orchestrator", ORG, THREAD, **kw) is None, "no tenant"


# ── 4. The apply ─────────────────────────────────────────────────────────────


async def _apply(monkeypatch: pytest.MonkeyPatch, *, read_only: bool,
                 words: str = WORDS) -> tuple[Any, _Orch]:
    _flags(monkeypatch)
    _stub_owners(monkeypatch)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    agent = _Orch()
    with member_typed(words), _tenant():
        task = _start(whatsapp=read_only, shows_cards=not read_only)
        ctx = {"run_id": "r-g2", "session_id": THREAD, "agent_name": "orchestrator",
               "member": MEMBER, "batch_thread": False, "no_egress": False}
        applied = await ex._apply_pack_turn(
            task, [agent], ctx, agent_name="orchestrator", tool_scope=None,
            agent_config={"name": "orchestrator"}, read_only=read_only)
    return applied, agent


async def test_the_read_tools_load_and_the_specialist_stays(
    store: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Words that name Projects, the CRM and mail load the read tools of the
    Projects and CRM packs, and no write tool. Each specialist stays, no
    hand-off refuses, and the persona keeps its bytes with one rules line.
    Mutation: drop ``batch_read_tools`` from the build, or let the record
    replace an owner -> red."""
    before = [ti._delegates_to(t) for t in _Orch().default_options["tools"]]
    applied, agent = await _apply(monkeypatch, read_only=True)
    assert applied is not None
    record = applied.record
    assert record.read_only is True and record.refusals == {} and not record.replaced
    assert {"projects", "crm-records"} <= set(record.slugs)
    assert not set(record.slugs) & set(PACKS_OF[EA])
    names = _names(agent)
    added = [n for n in names if n not in {"projects_assistant", "crm_assistant",
                                            "email_assistant"}]
    assert {"find_tasks", "crm_search_notes"} <= set(added), "non-vacuity"
    assert all(annotated_read_only(n) for n in added), added
    assert not set(added) & _writes()
    assert "create_task" not in names and "read_email" not in names
    assert [ti._delegates_to(t) for t in agent.default_options["tools"]
            if ti._delegates_to(t)] == before
    rules = tp.batch_rules(record.slugs)
    assert agent.default_options["instructions"] == f"{PERSONA.rstrip(chr(10))}\n\n{rules}\n"
    assert "create_task" not in rules
    assert ex._pack_loader(agent) is None and ex._pack_finder(agent) is None
    assert tp.delegation_refusal(PA, applied.context()) is None


async def test_the_web_apply_of_the_same_words_holds_writes(
    store: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity of the case above: the web turn of the same words holds a
    write tool, so the filter is what keeps it out."""
    applied, agent = await _apply(monkeypatch, read_only=False,
                                  words="show my overdue tasks")
    assert applied is not None and applied.record.read_only is False
    assert "create_task" in _names(agent)


# ── 5. Every tool of every pack ──────────────────────────────────────────────


def test_every_pack_tool_in_the_batch_subset_is_a_read() -> None:
    """Each real owner builds each pack. The batch subset holds no tool that
    the risk registry does not mark read-only, and no tool of a second list
    of writes. A mail pack is never allowed. So a write tool that joins a
    pack later cannot slip in. Mutation: ``batch_read_tools`` keeps a tool
    with no read mark, or ``batch_allowed`` keeps a mail pack -> red."""
    allowed = tp.batch_allowed(tp.PACKS)
    assert not allowed & set(PACKS_OF[EA])
    kept_all: set[str] = set()
    for slug in sorted(allowed):
        module = ti._pack_owner_module(tp.PACKS[slug].owner)
        if module is None:
            continue
        built = tp.owner_tools(slug, module)
        kept = {ti._tool_name(t) for t in tp.batch_read_tools(built)}
        from acb_skills.subagent_contract import DISPLAY_TOOLS

        for name in (ti._tool_name(t) for t in built):
            assert (name in kept) == (annotated_read_only(name) and name not in DISPLAY_TOOLS), (slug, name)
        assert not kept & _writes(), (slug, sorted(kept & _writes()))
        assert not kept & DISPLAY_TOOLS, (slug, sorted(kept & DISPLAY_TOOLS))
        kept_all |= kept
    assert {"find_tasks", "my_tasks_list_events"} <= kept_all, "non-vacuity"


def test_a_display_tool_never_joins_the_batch_subset() -> None:
    """``open_in_app`` and ``studio_plan`` hold a read mark in the registry, yet
    each acts on the web page or needs a card. Mutation: ``batch_read_tools``
    drops its DISPLAY_TOOLS test -> red."""
    from acb_skills.subagent_contract import DISPLAY_TOOLS

    names = ["find_tasks", "open_in_app", "studio_plan", "show_view"]
    assert {"open_in_app", "studio_plan"} <= DISPLAY_TOOLS
    tools = [_probe(n) for n in names]
    assert [ti._tool_name(t) for t in tp.batch_read_tools(tools)] == ["find_tasks"]


def test_a_tool_with_no_read_mark_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: treat a tool with no entry as a read -> red."""
    tools = [_probe("find_tasks"), _probe("g2_unknown_tool"), _probe("create_task")]
    assert [ti._tool_name(t) for t in tp.batch_read_tools(tools)] == ["find_tasks"]
    monkeypatch.delitem(ta.TOOL_ANNOTATIONS, "find_tasks", raising=False)
    assert tp.batch_read_tools(tools) == []


# ── 6. A write tool at a pack call ───────────────────────────────────────────


def _record(read_only: bool, namespace: str = tp.BATCH_NAMESPACE) -> tp.PackRun:
    turn = tp.PackTurn(ORG, THREAD, tp._member_part(MEMBER), 3, (), "", stored=True,
                       namespace=namespace)
    choice = tp.Choice(slugs=("projects",), layers={"projects": "words"}, first={})
    return tp.PackRun.of(turn, choice, [("projects", False)], read_only=read_only)


@contextlib.contextmanager
def _frame(record: tp.PackRun, run_key: str) -> Iterator[None]:
    with _tenant(), artifact_context_scope():
        bind_artifact_context(session_id=THREAD, agent_name="orchestrator", run_id="r-g2",
                              member=MEMBER, no_egress=False)
        ex._bind_batch_packs(ex._BatchPacks(
            organization_id=ORG, thread_id=THREAD, member=MEMBER, solo_room=True,
            delegated=False, run_ctx={},
            applied=ti.PackApply(builds=(), record=record, run_key=run_key)))
        yield


async def test_a_write_tool_answers_the_whatsapp_refusal(
    store: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write tool that reaches a pack call in a WhatsApp turn does not run,
    and answers the WhatsApp text. A tool with no read mark does the same. A
    read tool runs in the same frame, so the bound run key works. Mutation:
    drop the ``batch_write_refusal`` step of ``_pack_call``, or drop
    ``_bind_batch_packs`` -> red."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    calls: list[str] = []
    key = "rk-g2"
    write = ti._pack_call(_probe("create_task", calls), PA, False, key, "projects")
    unknown = ti._pack_call(_probe("g2_unknown_tool", calls), PA, False, key, "projects")
    read = ti._pack_call(_probe("find_tasks", calls), PA, False, key, "projects")
    with _frame(_record(True), key):
        assert artifact_context().get(tp.PACK_RUN_KEY) == key
        assert await write() == tp.BATCH_WRITE_REFUSED
        assert await unknown() == tp.BATCH_WRITE_REFUSED
        assert await read() == "find_tasks done"
    assert calls == ["find_tasks"]
    assert tp.BATCH_WRITE_REFUSED == (
        "That needs a card, and WhatsApp cannot show one. Open the chat in the app.")
    # Non-vacuity: a web record does not refuse the write here.
    with _frame(_record(False, tp.NAMESPACE), key):
        assert await write() == "create_task done"


# ── 7. The sticky state ──────────────────────────────────────────────────────


async def test_the_whatsapp_sticky_state_never_reaches_the_web_chat(
    store: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read tool of a WhatsApp turn makes its pack sticky in the WhatsApp
    namespace only. The web turn of the same thread reads none of it.
    Mutation: ``note_used`` or ``begin_turn`` writes the web namespace for a
    WhatsApp record -> red."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    key = "rk-g2-sticky"
    read = ti._pack_call(_probe("find_tasks"), PA, False, key, "projects")
    with _frame(_record(True), key):
        assert await read() == "find_tasks done"
    assert ("hset", _key(tp.BATCH_NAMESPACE)) in _pack_calls(store)
    assert not [c for c in _pack_calls(store) if f":{tp.NAMESPACE}:" in c[1]]
    web = await tp.begin_turn(ORG, THREAD, MEMBER, "", "")
    assert web.sticky == ()
    wa = await tp.begin_turn(ORG, THREAD, MEMBER, "", "", namespace=tp.BATCH_NAMESPACE)
    assert [s.slug for s in wa.sticky] == ["projects"]
    await tp.drop_sticky(wa, ["projects"])
    assert ("hdel", _key(tp.BATCH_NAMESPACE)) in _pack_calls(store)


# ── 8. The real batch run ────────────────────────────────────────────────────


@pytest.fixture
def wa_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: Any) -> Any:
    """One batch run of the orchestrator, as ``bot_run`` starts it. The model
    call, the load, the integrations and the MCP servers are stubbed, so the
    suite needs no database and no network."""
    import acb_memory
    from gateway.routes import agent as agent_routes
    from orchestrator import app_tools, workflow_tools

    clone = tmp_path / "agents" / "repos" / "orchestrator"
    clone.mkdir(parents=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "apps"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": "orchestrator", "local_path": str(clone)}])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    _flags(monkeypatch)
    _stub_owners(monkeypatch)
    seen: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def _load(name: str, **_kw: Any) -> Iterator[Any]:
        yield types.SimpleNamespace(agent_dir=clone, agent_name=name,
                                    config={"name": "orchestrator"},
                                    build_agents=lambda: [_Orch()])

    async def _run_maf(agents: list[Any], **_kw: Any) -> dict[str, Any]:
        ctx = artifact_context()
        seen.append({"tools": _names(agents[0]),
                     "persona": agents[0].default_options["instructions"],
                     "record": ctx.get(tp.PACKS_KEY), "key": ctx.get(tp.PACK_RUN_KEY)})
        return {"answer": "ok"}

    async def _quiet(*_a: Any, **_k: Any) -> Any:
        return None

    async def _empty(*_a: Any, **_k: Any) -> str:
        return ""

    async def _no(*_a: Any, **_k: Any) -> bool:
        return False

    async def _authorizer(*_a: Any, **_k: Any) -> Any:
        return None

    async def _web_search(query: str) -> str:
        return ""

    _web_search.__name__ = "web_search"
    monkeypatch.setattr(ex, "load_agent", _load)
    monkeypatch.setattr(ex, "build_integrations", lambda *_a, **_k: ({}, {}))
    monkeypatch.setattr(ex, "_integration_authorizer", _authorizer)
    monkeypatch.setattr(ex, "_run_with_maf_agent", _run_maf)
    monkeypatch.setattr(ex, "_inject_mcp_servers", _quiet)
    monkeypatch.setattr(ex, "_install_push_guard", _quiet)
    monkeypatch.setattr(ex, "_get_current_head", _empty)
    monkeypatch.setattr(ex, "_detect_agent_commits", _quiet)
    monkeypatch.setattr(ex, "_agent_push_binding", _no)
    monkeypatch.setattr(ex, "_tier_policy_covers", lambda _name: False)
    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _quiet)
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools",
                        lambda *_a, **_k: [_web_search])
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(workflow_tools, "load_workflow_tools", lambda *_a, **_k: [])

    def _go(*, channel: str = "whatsapp", thread: str | None = THREAD,
            user: str | None = MEMBER, solo: bool = True,
            parent: dict[str, Any] | None = None) -> dict[str, Any]:
        seen.clear()
        with artifact_context_scope(), profile_channel(channel, writes=False), \
                member_typed(WORDS), _tenant():
            if parent is not None:
                bind_artifact_context(**parent)
            out = asyncio.run(ex.run_agent(
                "orchestrator", {"message": WORDS, "user_email": MEMBER},
                run_id="r-g2-run", thread_id=thread, organization_id=ORG,
                session_user=user, solo_room=solo))
        assert out == {"answer": "ok"} and len(seen) == 1
        return seen[0]

    return _go


def _no_pack(got: dict[str, Any]) -> None:
    assert got["record"] is None and got["key"] is None
    assert "find_tasks" not in got["tools"] and "WHATSAPP READ TOOLS." not in got["persona"]
    assert got["persona"].startswith(PERSONA)


def test_flag_off_the_batch_run_is_byte_identical(wa_run: Any, monkeypatch) -> None:
    """With the flag empty, the run never calls the pack turn, and its tool
    list and persona equal those of the run with the G2 code taken out.
    Mutation: start the pack turn before the flag check -> red."""
    _flags(monkeypatch, batch="")

    def _never(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the pack turn ran with the flag off")

    monkeypatch.setattr(ex, "_start_pack_turn", _never)
    off = wa_run()
    with monkeypatch.context() as m:
        m.setattr(ex, "_batch_packs_for_run", lambda *_a, **_k: None)
        bare = wa_run()
    assert off == bare
    _no_pack(off)


def test_flag_on_the_whatsapp_run_loads_and_binds_the_read_packs(wa_run: Any) -> None:
    """The member's own WhatsApp turn holds the read tools, the specialists
    stay, and the run binds the read-only record and the run key before the
    model call. Mutation: drop ``_bind_batch_packs`` from the batch run, or
    drop ``packs=`` from its assembly -> red."""
    got = wa_run()
    assert "find_tasks" in got["tools"] and "create_task" not in got["tools"]
    assert "projects_assistant" in got["tools"] and "crm_assistant" in got["tools"]
    assert isinstance(got["record"], tp.PackRun) and got["record"].read_only is True
    assert got["record"].namespace == tp.BATCH_NAMESPACE and got["key"]
    assert got["persona"].startswith(PERSONA) and "WHATSAPP READ TOOLS." in got["persona"]


@pytest.mark.parametrize("case", ["web-channel", "room", "unverified", "workflow", "delegated"])
def test_no_other_batch_run_gets_a_pack(wa_run: Any, case: str) -> None:
    """A batch run off the WhatsApp channel, a shared room, a sender with no
    verified member, a workflow run (no chat thread) and a delegated run get
    no pack. Mutation: drop the channel check, the solo check, the member
    check, the thread check or the parent check -> red."""
    kw: dict[str, Any] = {
        "web-channel": {"channel": "chat"},
        "room": {"solo": False},
        "unverified": {"user": None},
        "workflow": {"thread": None},
        "delegated": {"parent": {"agent_name": "orchestrator", "run_id": "r-parent",
                                 "session_id": "web-chat-1", "member": MEMBER,
                                 "no_egress": False}},
    }[case]
    _no_pack(wa_run(**kw))


def test_the_spec_names_this_fence() -> None:
    """Mutation: the status line leaves the spec -> red."""
    text = (REPO / "project-docs/specs/one_assistant_context.md").read_text(encoding="utf-8")
    assert "CHAT_PACKS_BATCH_ORGS" in text and "test_chat_packs_batch.py" in text
    rules = (REPO / "apps/services/orchestrator/AGENTS.md").read_text(encoding="utf-8")
    assert "CHAT_PACKS_BATCH_ORGS" in rules
    assert json.loads((REPO / "tests/fences.json").read_text(encoding="utf-8"))
