"""CSY-4 (task 3403): a turn that names a connected server evicts a sticky mail pack.

Spec: ``project-docs/specs/chat_connected_systems.md`` §3.4 and D-CSY-2. The
rule is one more reason inside ``tool_packs.choose`` (``server_named``), dark
behind ``MCP_MAIL_PRECEDENCE_ORGS``. OA-7b does not change: a run that holds a
mail pack still gets no MCP server.

1. Sticky email, server words, mark set, flag on: the pack leaves with the
   reason ``server_named``, and the server attaches.
2. A mail pack that the turn names (words, reference or screen) stays, and the
   server stays out. The CSY-5 ``mail`` line still shows.
3. The mark fails closed: clear, unknown or a Redis fault evicts nothing.
4. Flag off: the choice is byte-identical, over the TS-1b matrix too.
5. The grammar of a server's words.
6. A server that the grant refuses, out of scope, stdio or with no URL never
   matches.
7. The fresh guard: a pack used in the last member turn never leaves.
8. TOF-1b: a skill that brings the mail pack back keeps it, and the server
   stays out.
9. No run holds a mail-owner pack tool and an MCP tool together.
10. SAFETY: an MCP call of a marked chat still asks the member's card
    (step 3a), also with the read-only key and a member present.
11. OA-7i: where the server attaches, the email specialist stays.
12. The flag sits in each place of a flag.
13. The eviction path never reads ``chat_marked_now`` or ``session_is_marked``.

Each test names the mutation that turns it red where it is not plain. The pure
tests need no database, no Redis and no model.

Run: ``uv run pytest tests/unit/test_pack_choose_server_words.py -q``.
"""
from __future__ import annotations

import ast
import contextlib
import dataclasses
import inspect
import json
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import server_match as sm
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp

ROOT = Path(__file__).resolve().parents[2]
ORG = "0a0a0a0a-0000-4000-8000-0000000000c4"
SESSION = "chat-csy4"
MEMBER = "jane@acme.test"
EA = "email-assistant"
TURN = 10
BASE = 57
SPECIALISTS = {"projects-assistant": 1, "email-assistant": 1, "crm-assistant": 1}
SERVERS = (("shaurrya-app", sm.server_words("Shaurrya App", "shaurrya-app")),)
#: Words that name the server and no mail.
ASK = "How many open circuits does Shaurrya show for Pune?"
ALLOW = frozenset({"email", "email-setup", "crm-records", "projects"})


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "mcp_mail_precedence_orgs", value)


def _turn(words: str = ASK, *, used: int = 7, sticky: tuple[str, ...] = ("email",),
          servers: Any = SERVERS, marked: bool = True, surface: str = "",
          skills: tuple[Any, ...] = (), **over: Any) -> tp.PackTurn:
    stuck = tuple(tp.Sticky(slug=s, first=(1, i), used=used) for i, s in enumerate(sticky))
    return tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), TURN, stuck, words, False,
                       surface=surface, skills=skills, servers=servers, mail_marked=marked,
                       **over)


def _plain(turn: tp.PackTurn) -> tp.PackTurn:
    """*turn* as main reads it: no server words and no mark."""
    return dataclasses.replace(turn, servers=(), mail_marked=False)


def _choose(turn: tp.PackTurn, *, allow: frozenset[str] = ALLOW,
            skill_packs: Any = None) -> tp.Choice:
    return tp.choose(turn, allowed=allow, base_tools=BASE, specialists=SPECIALISTS,
                     skill_packs=skill_packs)


def _mail(slugs: Any) -> list[str]:
    return [s for s in slugs if tp._reads_strangers(s)]


# ── 5. The grammar of a server's words ──────────────────────────────────────


def test_the_words_of_a_server_are_its_label_and_name_less_the_stop_list() -> None:
    assert sm.server_words("Shaurrya App", "shaurrya-app") == frozenset({"shaurrya"})
    assert sm.server_words("Zoho Books", "zoho-books") == frozenset({"zoho", "book"})
    # Each stop-list word, and each word below 4 letters, is out.
    assert sm.server_words("The Data Tools API Server", "mcp-app") == frozenset()
    assert sm.server_words("ERP Hub", "erp") == frozenset()
    stop = frozenset({"app", "mcp", "server", "api", "the", "data", "tool", "tools"})
    assert stop == sm.STOP_WORDS
    assert sm.MIN_LETTERS == 4


@pytest.mark.parametrize(("words", "hit"), [
    ("what does shaurrya say", True),
    ("WHAT DOES SHAURRYA SAY", True),  # any case
    ("Shaurrya's circuits in Pune", True),  # a possessive
    ("open the shaurryas list", True),  # a plural
    ("shaurrya-app status", True),  # a hyphen splits words
    ("ask shaurryaapp", False),  # a longer word holds it: no match
    ("ask notshaurrya", False),
    ("ask shaurr", False),  # a part of the word
    ("check the app and the server data tools", False),  # stop words only
    ("", False),
])
def test_a_turn_names_a_server_by_whole_words_only(words: str, hit: bool) -> None:
    assert (sm.named_servers(words, dict(SERVERS)) == ("shaurrya-app",)) is hit


def test_a_plural_server_word_matches_the_singular_and_a_short_word_never() -> None:
    servers = {"tickets": sm.server_words("Tickets", "tickets"),
               "bus": sm.server_words("Bus", "bus")}
    assert servers["tickets"] == frozenset({"ticket"})
    assert sm.named_servers("show my TICKET", servers) == ("tickets",)
    assert sm.named_servers("take the bus", servers) == ()


def test_the_admin_words_field_is_not_read() -> None:
    """D-CSY-4 is open and the CSY-7 ``words`` field is held. Mutation: read a
    ``words`` argument or a row field → the signature grows → red."""
    assert list(inspect.signature(sm.server_words).parameters) == ["label", "name"]


# ── 1, 2, 3, 7. The rule inside ``choose`` ──────────────────────────────────


def test_a_sticky_email_pack_leaves_when_the_turn_names_a_server(monkeypatch) -> None:
    """Mutation: skip the new reason → email stays in the slugs → red."""
    _flag(monkeypatch, "*")
    turn = _turn()
    assert _choose(_plain(turn)).slugs == ("email",), "non-vacuity: main keeps it"
    choice = _choose(turn)
    assert choice.slugs == () and choice.evicted == ("email",)
    assert choice.server_named == ("email",)
    assert _mail(choice.slugs) == []


def test_each_unnamed_mail_pack_leaves_and_another_sticky_pack_stays(monkeypatch) -> None:
    _flag(monkeypatch, ORG)
    choice = _choose(_turn(sticky=("email", "email-setup", "crm-records")))
    assert choice.server_named == ("email", "email-setup")
    assert choice.slugs == ("crm-records",)


@pytest.mark.parametrize(("words", "surface"), [
    (ASK + " and check my email", ""),  # the words
    (ASK + " /email?email=abc123", ""),  # a reference
    (ASK, "email"),  # the screen
])
def test_a_mail_pack_that_the_turn_names_never_leaves(words: str, surface: str,
                                                       monkeypatch) -> None:
    """Mutation: evict also a named pack → email leaves → red."""
    _flag(monkeypatch, "*")
    turn = _turn(words, surface=surface)
    choice = _choose(turn)
    assert "email" in choice.slugs and choice.server_named == ()
    assert choice == _choose(_plain(turn))


def test_one_named_mail_pack_keeps_every_mail_pack(monkeypatch) -> None:
    """The server cannot attach while one mail pack stays, so the rule evicts
    all of them or none. Mutation: evict per pack → email-setup leaves for
    nothing → red."""
    _flag(monkeypatch, "*")
    turn = _turn(ASK + " and check my email", sticky=("email", "email-setup"))
    choice = _choose(turn)
    assert choice.server_named == () and choice == _choose(_plain(turn))


@pytest.mark.parametrize("marked", [False])
def test_with_no_mark_set_nothing_leaves(marked: bool, monkeypatch) -> None:
    """``mail_marked`` is True only for MARK_MARKED (see the apply tests for
    clear, unknown and a fault). Mutation: ignore the mark → red."""
    _flag(monkeypatch, "*")
    turn = _turn(marked=marked)
    assert _choose(turn) == _choose(_plain(turn))


@pytest.mark.parametrize("used", [9, 10])
def test_a_pack_used_in_the_last_member_turn_never_leaves(used: int, monkeypatch) -> None:
    """The fresh guard. Mutation: drop it → email leaves → red."""
    _flag(monkeypatch, "*")
    turn = _turn(used=used)
    choice = _choose(turn)
    assert choice.slugs == ("email",) and choice.server_named == ()
    assert choice == _choose(_plain(turn))


def test_one_fresh_mail_pack_keeps_every_mail_pack(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    turn = dataclasses.replace(_turn(sticky=("email",)), sticky=(
        tp.Sticky(slug="email", first=(1, 0), used=6),
        tp.Sticky(slug="email-setup", first=(1, 1), used=9)))
    assert _choose(turn).server_named == ()


def test_a_turn_that_names_no_server_evicts_nothing(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    turn = _turn("summarise the Pune circuits")
    assert _choose(turn) == _choose(_plain(turn))


_SKILL = types.SimpleNamespace(slug="skill-mail-digest", tools=("email_list_messages",))
_NEEDS = {"skill-mail-digest": ("email",)}


@pytest.mark.parametrize("sticky", [("email",), ("email", "email-setup")])
def test_tof_1b_a_skill_that_needs_the_mail_pack_keeps_each_mail_pack(
    sticky: tuple[str, ...], monkeypatch,
) -> None:
    """A skill of the turn needs ``email``: that counts as a name, so no mail
    pack leaves, and the run holds a mail pack, so the server stays out.
    Mutation: skip the skill test → email-setup leaves for nothing, or email
    leaves and comes back → red."""
    _flag(monkeypatch, "*")
    turn = _turn(sticky=sticky, skills=(_SKILL,))
    choice = _choose(turn, skill_packs=_NEEDS)
    assert choice.server_named == () and choice == _choose(_plain(turn), skill_packs=_NEEDS)
    # A mail pack stays, so the server stays out. With both packs the cap of
    # main moves ``email`` out, and ``email-setup`` stays.
    assert _mail(choice.slugs)


def test_tof_1b_a_pack_that_a_skill_brings_back_leaves_the_result(monkeypatch) -> None:
    """The second guard of TOF-1b, in ``choose``: a pack that a skill brings
    back leaves ``evicted`` and ``server_named``. ``_server_named`` is forced
    here, because its own skill test stops the case first. Mutation: keep the
    brought pack in ``server_named`` → red."""
    _flag(monkeypatch, "*")
    monkeypatch.setattr(tp, "_server_named", lambda *_a, **_k: ("email",))
    choice = _choose(_turn(skills=(_SKILL,)), skill_packs=_NEEDS)
    assert choice.slugs == ("email",) and choice.layers["email"] == "skill"
    assert "email" not in choice.evicted and choice.server_named == ()


# ── 4. Flag off: byte-identical ─────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "org-other"])
def test_with_the_flag_off_the_choice_is_byte_identical(value: str, monkeypatch) -> None:
    _flag(monkeypatch, value)
    for turn in (_turn(), _turn(sticky=("email", "email-setup", "crm-records")),
                 _turn(ASK + " and check my email")):
        assert _choose(turn) == _choose(_plain(turn))
        assert _choose(turn).server_named == ()


def _matrix_with(monkeypatch, servers: Any, marked: bool) -> dict[str, Any]:
    """The TS-1b matrix, with each turn given *servers* and *marked*."""
    from tests.unit import test_studio_split_matrix as m

    real = tp.PackTurn

    def turn(*a: Any, **k: Any) -> tp.PackTurn:
        return real(*a, **{**k, "servers": servers, "mail_marked": marked})

    monkeypatch.setattr(m.tp, "PackTurn", turn)
    try:
        return m.matrix()
    finally:
        monkeypatch.setattr(m.tp, "PackTurn", real)


@pytest.mark.parametrize("flag", ["", "*"])
def test_the_ts_1b_matrix_keeps_its_golden(flag: str, monkeypatch) -> None:
    """No case of the matrix names a server, so the flag and the fields change
    no row. Flag off with the fields set is the byte-identical claim."""
    from tests.unit import test_studio_split_matrix as m

    _flag(monkeypatch, flag)
    want = m._golden()["split"]
    assert _matrix_with(monkeypatch, SERVERS, True) == want


#: Two matrix rows in the style of ``test_studio_split_matrix``: the words
#: name the server, a sticky email pack is old, and the mark is set.
MATRIX_CASES: dict[str, dict[str, Any]] = {
    "sticky_email_and_server_words": {
        "words": ASK, "surface": "",
        "sticky": (tp.Sticky(slug="email", first=(5, 0), used=7),),
    },
    "named_email_and_server_words": {
        "words": ASK + " and check my emails", "surface": "",
        "sticky": (tp.Sticky(slug="email", first=(5, 0), used=7),),
    },
}


@pytest.mark.parametrize("flag", ["", "*"])
def test_the_two_matrix_rows(flag: str, monkeypatch) -> None:
    from tests.unit import test_studio_split_matrix as m

    _flag(monkeypatch, flag)
    real = tp.PackTurn
    monkeypatch.setattr(m.tp, "PackTurn", lambda *a, **k: real(
        *a, **{**k, "servers": SERVERS, "mail_marked": True}))
    rows = {name: m._row(case) for name, case in MATRIX_CASES.items()}
    monkeypatch.setattr(m.tp, "PackTurn", real)
    plain = {name: m._row(case) for name, case in MATRIX_CASES.items()}
    named = rows["named_email_and_server_words"]
    assert named == plain["named_email_and_server_words"] and "email" in named["packs"]
    sticky = rows["sticky_email_and_server_words"]
    if not flag:
        assert sticky == plain["sticky_email_and_server_words"]
        assert sticky["packs"] == ["email"]
        return
    assert sticky["packs"] == [] and sticky["evicted"] == ["email"]
    assert sticky["tools"] == BASE + 0


# ── 9. The invariant: no run holds a mail pack and an MCP tool ──────────────


def test_no_choice_that_names_a_server_keeps_a_mail_pack(monkeypatch) -> None:
    """Over a matrix of turns: when ``server_named`` is not empty, the choice
    holds no mail-owner pack. A choice that keeps one keeps the server out
    (OA-7b, ``_inject_mcp_servers``), so no run holds both."""
    skill = types.SimpleNamespace(slug="skill-mail-digest", tools=("email_list_messages",))
    seen = 0
    for flag in ("", "*"):
        _flag(monkeypatch, flag)
        for words in (ASK, ASK + " and check my email", "reply to Anil", "check my inbox"):
            for sticky in ((), ("email",), ("email-setup",), ("email", "email-setup"),
                           ("email", "crm-records"), ("crm-records", "projects")):
                for used in (5, 9):
                    for marked in (False, True):
                        for skills in ((), (skill,)):
                            turn = _turn(words, used=used, sticky=sticky, marked=marked,
                                         skills=skills)
                            choice = _choose(turn, skill_packs={
                                "skill-mail-digest": ("email",)} if skills else None)
                            if choice.server_named:
                                seen += 1
                                assert _mail(choice.slugs) == [], turn
                                assert set(choice.server_named) <= set(choice.evicted)
                                assert flag and marked and used == 5
                            else:
                                assert choice == _choose(_plain(turn), skill_packs={
                                    "skill-mail-digest": ("email",)} if skills else None)
    assert seen > 0, "non-vacuity"


# ── 10. SAFETY: the card of a marked chat (step 3a) ─────────────────────────


@pytest.mark.parametrize("read_only_key", [False, True])
@pytest.mark.parametrize("member_present", [False, True])
@pytest.mark.parametrize("hints", [
    None, {"readOnlyHint": True}, {"destructiveHint": True}, {"readOnlyHint": False}])
def test_a_marked_chat_still_asks_the_card_for_each_mcp_call(
    read_only_key: bool, member_present: bool, hints: Any,
) -> None:
    """The step-3a pin of CSY-4. The pack leaves only when the mark reads set,
    and a set mark gives each MCP call ``untrusted_answer_confirm``. Mutation:
    move step 3a after the read-only key or after MCP-NC1 → red."""
    from acb_skills.permission_policy import UNTRUSTED_ANSWER_CONFIRM, decide

    approved, code, _ = decide({
        "tool_name": "shaurrya-app_call_operation", "mcp_tool": True,
        "mcp_annotations": hints, "mcp_always_confirm": False,
        "mcp_read_only_key": read_only_key, "mcp_member_present": member_present,
        "after_untrusted_answer": True,
    })
    assert approved is True and code == UNTRUSTED_ANSWER_CONFIRM


def test_the_mcp_gate_reads_the_mark_that_fails_closed() -> None:
    """The gate asks ``session_is_marked`` (unknown counts as marked), so a
    Redis fault after the eviction still shows the card."""
    from orchestrator import _maf_mcp

    src = inspect.getsource(_maf_mcp)
    assert "after_untrusted_answer=await session_is_marked()" in src


# ── 12. The flag sits in each place of a flag ───────────────────────────────


def test_the_flag_is_named_in_the_places_of_a_flag() -> None:
    from acb_common import env_guard
    from gateway import flag_reload

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nMCP_MAIL_PRECEDENCE_ORGS=\n" in env
    assert env_guard.is_platform_env("MCP_MAIL_PRECEDENCE_ORGS")
    assert flag_reload.reloadable("MCP_MAIL_PRECEDENCE_ORGS")
    doc = (ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "`MCP_MAIL_PRECEDENCE_ORGS` turns on the precedence rule" in doc
    plan = (ROOT / "project-docs" / "work_plan.md").read_text(encoding="utf-8")
    assert "Also `MCP_MAIL_PRECEDENCE_ORGS` (WS-CSY CSY-4" in plan


def test_the_flag_reader_reads_ids_and_star_and_fails_closed(monkeypatch) -> None:
    import acb_common
    from acb_skills import egress

    _flag(monkeypatch, "")
    assert not egress.mcp_mail_precedence_on(ORG)
    _flag(monkeypatch, f" {ORG.upper()} , org-b")
    assert egress.mcp_mail_precedence_on(ORG) and not egress.mcp_mail_precedence_on("org-c")
    assert not egress.mcp_mail_precedence_on("")
    _flag(monkeypatch, "*")
    assert egress.mcp_mail_precedence_on("org-c")

    def _broken() -> Any:
        raise RuntimeError("no settings")

    monkeypatch.setattr(acb_common, "get_settings", _broken)
    assert not egress.mcp_mail_precedence_on(ORG)


# ── 13. Polarity: the eviction path reads MARK_MARKED only ──────────────────

#: The readers that fail OPEN for an eviction: each reads an unknown mark as
#: set, so a Redis fault would evict a pack.
_WRONG_READERS = frozenset({"chat_marked_now", "session_is_marked", "_chat_marked",
                            "chat_is_held", "run_holds_mail"})


def _names_in(func: Any) -> set[str]:
    tree = ast.parse(inspect.getsource(func).lstrip())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
    return out


def test_the_eviction_path_never_reads_a_mark_reader_that_fails_open() -> None:
    from orchestrator import _tool_injection as ti

    for func in (ti._mail_mark_set, ti._with_server_words, tp.choose, tp._server_named):
        assert not (_names_in(func) & _WRONG_READERS), func.__name__
    assert {"session_mark_state", "MARK_MARKED"} <= _names_in(ti._mail_mark_set)


# ── The apply: the lookahead, the mark and the attach ───────────────────────


def _row(**over: Any) -> Any:
    base = {"name": "shaurrya-app", "label": "Shaurrya App", "description": "Tele data",
            "enabled": True, "agent_scope": ("*",), "transport": "http-sse",
            "url": "https://shaurrya.example/mcp", "headers": {}, "always_confirm": False,
            "read_only_key": False, "oauth": False, "refreshed": False, "connected_by": None}
    return types.SimpleNamespace(**{**base, **over})


@contextlib.contextmanager
def _tenant() -> Iterator[None]:
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


def _run_ctx() -> dict[str, Any]:
    return {"run_id": "r-csy4", "session_id": SESSION, "agent_name": "orchestrator",
            "member": MEMBER, "batch_thread": False, "no_egress": False,
            "workspace_root": "/tmp/orch-csy4"}


@pytest.fixture
def run(monkeypatch) -> Any:
    """The orchestrator agent, a stub email owner, and fakes for the registry,
    the mark and the attach. ``run.state`` sets the mark."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    import gateway.connections.mcp as registry
    from orchestrator import _maf_mcp
    from orchestrator import _tool_injection as ti
    from orchestrator.agents import build_orchestrator_agent

    names = tuple(sf.SKILL_FAMILIES["email"]["tools"]) + tuple(
        sf.SKILL_FAMILIES["email-setup"]["tools"])

    def _probe(name: str) -> Any:
        async def probe(email_id: str = "") -> str:
            return "ok"

        probe.__name__ = name
        probe.__tool_risk__ = {"read_only": True, "destructive": False,  # type: ignore[attr-defined]
                               "idempotent": True, "open_world": False}
        return probe

    monkeypatch.setitem(ti._PACK_OWNER_MODULES, EA, types.SimpleNamespace(
        _agent_tools=lambda: [_probe(n) for n in names]))
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: True)
    monkeypatch.setattr(_maf_mcp, "is_native_maf_agent", lambda _a: True)
    state = types.SimpleNamespace(mark="marked", reads=[], marks=0, attached=[],
                                  rows=[_row()], grant=lambda _svc: True)

    async def enabled_meta(org: str) -> list[Any]:
        state.reads.append(org)
        return list(state.rows)

    async def servers_for_org(org: str, **_k: Any) -> list[Any]:
        return list(state.rows)

    async def mark_state(ctx: Any = None, **_k: Any) -> str:
        # Only the CSY-4 reader counts and sees the fault. Other readers of the
        # run (the pack build, the held chat) read the same state.
        mine = inspect.stack()[1].function == "_mail_mark_set"
        state.marks += int(mine)
        if state.mark == "fault":
            if mine:
                raise ConnectionError("redis down")
            return "unknown"
        return state.mark

    async def attach(agent: Any, servers: list[Any], opened: list[Any], **_k: Any) -> list[str]:
        state.attached.append([s.name for s in servers])
        return [s.name for s in servers]

    monkeypatch.setattr(registry, "enabled_meta", enabled_meta)
    monkeypatch.setattr(registry, "servers_for_org", servers_for_org)
    monkeypatch.setattr(ti, "session_mark_state", mark_state)
    monkeypatch.setattr(_maf_mcp, "attach_mcp_tools", attach)
    original = routes_agent._load_dynamic_agents
    routes_agent._load_dynamic_agents = lambda: []
    try:
        state.agents = [build_orchestrator_agent(with_history=False)]
    finally:
        routes_agent._load_dynamic_agents = original
    state.ti = ti
    return state


async def _apply(run: Any, turn: tp.PackTurn) -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    config = json.loads((ROOT / "apps/agents/agent-orchestrator/config.json")
                        .read_text(encoding="utf-8"))
    with _tenant():
        applied = await run.ti._apply_tool_packs(
            run.agents, _plain(turn), _run_ctx(), allowed=ALLOW, agent_name="orchestrator",
            tool_scope=list(config["tool_scope"]), agent_config=config,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=True, is_authorized=run.grant)
        await run.ti._inject_mcp_servers(
            run.agents[0], "orchestrator", organization_id=ORG, is_authorized=run.grant,
            opened=[], member_present=True)
    return applied


def _tools(run: Any) -> list[str]:
    return [run.ti._tool_name(t) for t in run.agents[0].default_options["tools"]]


async def test_the_apply_evicts_the_pack_and_the_server_attaches(run, monkeypatch) -> None:
    """Acceptance 1, end to end, and OA-7i (11): with no mail pack the server
    attaches, ``_holds_untrusted_pack`` reads false, and the email specialist
    stays, so a mail ask goes to the email assistant. The mark is read once."""
    _flag(monkeypatch, "*")
    applied = await _apply(run, _turn())
    assert not run.ti._holds_untrusted_pack(run.agents[0])
    assert run.attached == [["shaurrya-app"]]
    assert run.marks == 1
    assert "email_assistant" in _tools(run)
    assert applied is not None and EA not in applied.record.via_loader
    assert "email" in applied.record.gone


@pytest.mark.parametrize("flag", ["", "org-other"])
async def test_with_the_flag_off_the_apply_reads_nothing_new(flag: str, run,
                                                             monkeypatch) -> None:
    """Flag off: no lookahead read, no mark read, the pack loads, and the
    server stays out, as on main."""
    _flag(monkeypatch, flag)
    await _apply(run, _turn())
    assert run.marks == 0
    assert run.ti._holds_untrusted_pack(run.agents[0]) and run.attached == []


@pytest.mark.parametrize("mark", ["clear", "unknown", "fault"])
async def test_a_mark_that_is_not_set_evicts_nothing(mark: str, run, monkeypatch) -> None:
    """Fail closed. Mutation: test ``!= MARK_CLEAR`` → unknown and the fault
    evict → red."""
    _flag(monkeypatch, "*")
    run.mark = mark
    await _apply(run, _turn())
    assert run.marks == 1
    assert run.ti._holds_untrusted_pack(run.agents[0]) and run.attached == []


async def test_a_named_mail_pack_keeps_the_server_out_with_the_mail_line(run,
                                                                       monkeypatch) -> None:
    """Acceptance 2: the pack stays, the server stays out, and the CSY-5
    ``mail`` line names the server."""
    from acb_skills import server_brief as sb
    from orchestrator import _maf_mcp

    _flag(monkeypatch, "*")
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda _o: True)
    sb.set_attached(())
    try:
        await _apply(run, _turn(ASK + " and check my email"))
        assert run.ti._holds_untrusted_pack(run.agents[0]) and run.attached == []
        assert "Shaurrya App (shaurrya-app): " + sb.REASON_TEXT["mail"] in sb.attached_block()
    finally:
        sb.set_attached(())


@pytest.mark.parametrize(("over", "granted"), [
    ({"agent_scope": ("crm-assistant",)}, True),
    ({"transport": "stdio"}, True),
    ({"url": ""}, True),
    ({"enabled": False}, True),
    ({}, False),
])
async def test_a_server_that_cannot_attach_never_matches(over: dict[str, Any], granted: bool,
                                                        run, monkeypatch) -> None:
    """Acceptance 6: the one shared filter. Mutation: match every row of
    ``enabled_meta`` → the pack leaves for a server that never attaches →
    red."""
    _flag(monkeypatch, "*")
    run.rows = [_row(**over)]
    run.grant = lambda _svc: granted
    await _apply(run, _turn())
    assert run.marks == 0, "no mark read with no attachable server"
    assert run.ti._holds_untrusted_pack(run.agents[0]) and run.attached == []


async def test_a_registry_fault_in_the_lookahead_evicts_nothing(run, monkeypatch) -> None:
    import gateway.connections.mcp as registry

    _flag(monkeypatch, "*")

    async def boom(_org: str) -> list[Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(registry, "enabled_meta", boom)
    with _tenant():
        turn = await run.ti._with_server_words(
            _plain(_turn()), run.agents[0], "orchestrator", _run_ctx(), run.grant)
    assert turn == _plain(_turn())


async def test_the_eviction_path_never_awaits_a_reader_that_fails_open(run,
                                                                      monkeypatch) -> None:
    """13, by behaviour: the readers that fail open raise here, and the run
    still evicts on a set mark."""
    from acb_skills import egress
    from orchestrator import _tool_injection as ti

    async def no(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("a reader that fails open was read")

    _flag(monkeypatch, "*")
    monkeypatch.setattr(egress, "chat_marked_now", lambda *_a, **_k: no())
    monkeypatch.setattr(ti, "session_is_marked", no)
    monkeypatch.setattr(ti, "_chat_marked", no)
    with _tenant():
        turn = await ti._with_server_words(
            _plain(_turn()), run.agents[0], "orchestrator", _run_ctx(), run.grant)
    assert turn.servers == SERVERS and turn.mail_marked is True


async def test_the_apply_logs_the_reason_on_the_chosen_line(run, monkeypatch) -> None:
    """(d): ``Choice.server_named`` rides on ``executor.packs_chosen``.

    The module logger is replaced with a recorder. ``capture_logs`` sees
    nothing when an earlier test in the same process ran
    ``configure_logging(level="WARNING")``, because the filtering wrapper drops
    ``info`` before any processor. So that form passed or failed by the order
    of the shard (local CI, 2026-11-22).
    """
    from orchestrator import _tool_injection as ti

    seen: list[dict[str, Any]] = []

    class _Recorder:
        def __getattr__(self, _level: str) -> Any:
            return lambda event, **kw: seen.append({"event": event, **kw})

    monkeypatch.setattr(ti, "_log", _Recorder())
    _flag(monkeypatch, "*")
    await _apply(run, _turn())
    chosen = [e for e in seen if e["event"] == "executor.packs_chosen"]
    assert chosen and chosen[0]["server_named"] == ["email"]
