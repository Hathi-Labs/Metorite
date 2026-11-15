"""WS-67 TS-2a PR 3: with ``TOOL_SEARCH_ORGS`` empty, ``find_tools`` adds nothing.

``project-docs/specs/one_assistant_context.md`` §4.2.9, the block TS-2a, "PR 3
must" and "Acceptance": with the flag off, the run is byte-identical. Each
case names, in its docstring, the mutation that turns it red (R7).

The golden file ``tests/unit/golden/tool_search_off.json`` came from the code
of main BEFORE PR 3 (commit 9c838d023). It holds, for three turns of the
member's own web chat with the orchestrator, the tool names of the first model
request in their order, and the SHA-256 and the length of its system text:

* ``studio_surface``: a turn on the Studio surface (both Studio packs load);
* ``projects_pack``: a turn whose words load the core Projects pack;
* ``plain``: a turn that loads no pack (the loader is offered).

Each run goes through the REAL ``run_agent_stream`` with a scripted model
(``tests/unit/_native_maf_harness.py``), with production's ``TOOL_PACKS`` and
``TOOL_PACKS_ORGS=*``. Only the HTTP transport of the model is replaced.

A change on main that changes the persona or the tool list of the
orchestrator changes the golden too. Then write it again on the code of main
WITHOUT a change to the search (``--write``), and say so in the PR.

Run: ``uv run pytest tests/unit/test_tool_search_off.py -q -p no:xdist``.
Write: ``uv run python -m tests.unit.test_tool_search_off --write``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_common.assistant_profile import member_typed
from acb_skills import tool_packs as tp
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._native_maf_harness import (
    ScriptedModel,
    load_agent_module,
    parse_frames,
    text_turn,
)

#: Production's ``TOOL_PACKS``. ONE copy, so the files cannot drift.
from tests.unit.test_studio_workflow_write_tools import PRODUCTION

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden" / "tool_search_off.json"
ORCH_DIR = "apps/agents/agent-orchestrator"
STUDIO_DIR = "apps/agents/agent-studio-builder"
STUDIO = "studio-builder"
CONFIG = json.loads((REPO / ORCH_DIR / "config.json").read_text(encoding="utf-8"))
#: The bound tenant of each run (tenant A).
ORG = "0a0a0a0a-0000-4000-8000-0000000000d4"
#: Another org (tenant B). Only a request field names it.
OTHER = "0a0a0a0a-0000-4000-8000-0000000000d5"
MEMBER = "jane@acme.test"

#: The three turns of the golden. Each turn has a chat of its own, so no turn
#: reads the sticky state of another. Each holds fewer than 12 words, so the
#: turn-kind question sends no request.
TURNS: dict[str, dict[str, str]] = {
    "studio_surface": {"words": "make the form blue", "surface": "studio"},
    "projects_pack": {"words": "show my tasks", "surface": ""},
    "plain": {"words": "hello there", "surface": ""},
}


def _features(grants: list[str]) -> frozenset[str]:
    from acb_auth import build_access

    return frozenset(build_access(grants, []).allowed_features())


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis. It records each call."""
    return install_memory_mark_store(monkeypatch)


def _studio(monkeypatch) -> None:
    """The real Studio owner, an org of ``STUDIO_ORGS``, a member who holds
    ``feature:studio`` and an org with an approval store."""
    from orchestrator import in_app_path as iap

    holder = _features(["feature:*"])

    async def _read() -> frozenset[str] | None:
        return holder

    monkeypatch.setitem(ti._PACK_OWNER_MODULES, STUDIO, load_agent_module(STUDIO_DIR))
    monkeypatch.setattr(ti, "_studio_open", lambda: True)
    monkeypatch.setattr(iap, "member_features", _read)
    monkeypatch.setattr(ti, "_studio_requests_open", lambda: True)


def _flags(monkeypatch, search: str = "") -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", PRODUCTION, raising=False)
    monkeypatch.setattr(settings, "tool_packs_orgs", "*", raising=False)
    # The code of main before PR 3 has no such field. The golden came from it.
    if search or "tool_search_orgs" in type(settings).model_fields:
        monkeypatch.setattr(settings, "tool_search_orgs", search, raising=False)


def session_of(name: str) -> str:
    """The chat of turn *name*. Each turn has its own."""
    return f"chat-ts2a-off-{name}"


def drive(
    monkeypatch,
    name: str,
    *,
    extra: dict[str, Any] | None = None,
    turns: list[list[dict[str, Any]]] | None = None,
    words: str | None = None,
    surface: str | None = None,
) -> dict[str, Any]:
    """One turn *name* of :data:`TURNS` through the real stream run. *words*
    and *surface* replace those of the case, or name a turn that is not in
    :data:`TURNS`.

    Returns each request body of the model, the events, the agents that the
    run built and the view of the run after the whole inject chain. *extra*
    adds keys to the request payload. *turns* is the script of the model (one
    text answer by default)."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    from acb_skills import email_chat_context as ecc

    case = dict(TURNS.get(name) or {"words": "", "surface": ""})
    if words is not None:
        case["words"] = words
    if surface is not None:
        case["surface"] = surface
    model = ScriptedModel(turns or [text_turn("Done.")])
    mod = load_agent_module(ORCH_DIR)
    built: list[Any] = []

    def _build() -> list[Any]:
        agents = mod.build_agents()
        oc = agents[0].client.client
        oc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(model), event_hooks=oc._client.event_hooks
        )
        built.extend(agents)
        return agents

    class _Loaded:
        agent_dir = REPO / ORCH_DIR
        agent_name = "orchestrator"
        config = CONFIG

        def build_agents(self) -> list[Any]:
            return _build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    async def _none(*_a: Any, **_k: Any) -> None:
        return None

    import acb_auth.access as auth_access

    monkeypatch.setattr(auth_access, "_tables_missing", auth_access._tables_missing)
    monkeypatch.setattr(ex, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(ex, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset())
    monkeypatch.setattr(ecc, "prime_mailboxes", _none)
    monkeypatch.setattr("acb_common.today.today_line", lambda *_a, **_k: "")
    # The agents of the run after the whole inject chain: the executor logs
    # the tool count of a pack run with them.
    views: list[Any] = []
    real_log = ex._log_pack_request_tools

    def _log(agents: list[Any]) -> None:
        views.extend(agents[:1])
        real_log(agents)

    monkeypatch.setattr(ex, "_log_pack_request_tools", _log)
    payload: dict[str, Any] = {"message": case["words"], **(extra or {})}
    session = session_of(name)

    async def _collect() -> list[str]:
        return [
            line
            async for line in ex.run_agent_stream(
                "orchestrator",
                payload,
                run_id=f"run-ts2a-off-{name}",
                thread_id=session,
                organization_id=ORG,
                session_user=MEMBER,
                shows_cards=True,
                solo_room=True,
            )
        ]

    with tp.surface_scope(case["surface"]), member_typed(case["words"]):
        events = parse_frames(asyncio.run(_collect()))
    return {"events": events, "bodies": list(model.bodies), "agents": built, "views": views}


def tool_names(body: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in body.get("tools") or []]


def system_text(body: dict[str, Any]) -> str:
    """The system messages of a request body, joined."""
    return "\n".join(
        str(m.get("content") or "") for m in body.get("messages") or [] if m.get("role") == "system"
    )


def view(body: dict[str, Any]) -> dict[str, Any]:
    """What the golden keeps of the first request of a turn."""
    text = system_text(body)
    return {
        "names": tool_names(body),
        "system_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "system_chars": len(text),
    }


def capture(monkeypatch, search: str = "") -> dict[str, dict[str, Any]]:
    """The view of the first request of each turn of :data:`TURNS`."""
    _flags(monkeypatch, search)
    _studio(monkeypatch)
    out: dict[str, dict[str, Any]] = {}
    for name in TURNS:
        got = drive(monkeypatch, name)
        kinds = [e.get("type") for e in got["events"]]
        assert kinds and kinds[-1] == "RUN_FINISHED" and "RUN_ERROR" not in kinds, got["events"]
        out[name] = view(got["bodies"][0])
    return out


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _finders(agents: list[Any]) -> list[Any]:
    """Each ``FindToolsProvider`` on the view of the run."""
    return [
        p
        for agent in agents
        for p in getattr(agent, "context_providers", None) or []
        if isinstance(p, tp.FindToolsProvider)
    ]


# ── 1. The flag ships off ───────────────────────────────────────────────────


def test_the_flag_ships_off() -> None:
    """Mutation: a default other than empty for ``tool_search_orgs`` -> each
    box that sets nothing offers ``find_tools`` -> red."""
    from acb_common.settings import Settings

    assert Settings.model_fields["tool_search_orgs"].default == ""
    assert getattr(get_settings(), "tool_search_orgs", None) == ""
    assert tp.tool_search_on(ORG) is False


# ── 2. The run is byte-identical with the flag off ──────────────────────────


def test_flag_off_each_turn_equals_the_golden_of_main(store, monkeypatch) -> None:
    """The three turns with the flag off give the tool names, their order and
    the system text that main gave before PR 3. Mutation: the search runs with
    the flag empty -> ``find_tools`` joins -> a name list differs -> red."""
    got = capture(monkeypatch)
    want = _golden()["turns"]
    assert sorted(got) == sorted(want)
    for name in want:
        assert got[name] == want[name], name
    # Non-vacuity: the turns differ, and the packs and the loader are in them.
    assert "studio_draft" in got["studio_surface"]["names"]
    assert "load_skill_tools" in got["plain"]["names"]
    assert all(tp.FIND_TOOL not in got[name]["names"] for name in got)


def test_flag_off_the_base_has_no_find_tools(store, monkeypatch) -> None:
    """With the flag off, the first choice of the Studio turn gets the
    estimate of the base and no more (D222 decision 13 counts ``find_tools``
    only with the flag on). The turns of the golden are not at the cap, so
    this case reads the count itself. Mutation: ``base += 1`` with no flag
    test -> the estimate + 1 -> red."""
    _flags(monkeypatch, "")
    _studio(monkeypatch)
    estimates: list[int] = []
    bases: list[int] = []
    real_count, real_choose = ti._pack_base_count, tp.choose

    def count(*a: Any, **k: Any) -> int:
        estimates.append(real_count(*a, **k))
        return estimates[-1]

    def choose(*a: Any, **k: Any) -> Any:
        bases.append(int(k["base_tools"]))
        return real_choose(*a, **k)

    monkeypatch.setattr(ti, "_pack_base_count", count)
    monkeypatch.setattr(tp, "choose", choose)
    drive(monkeypatch, "studio_surface")
    assert len(estimates) == 1 and len(bases) == 2, (estimates, bases)
    assert bases == [estimates[0], estimates[0] + 1], "the load tool alone adds 1"


async def _apply_nothing(monkeypatch) -> tuple[Any, Any, Any]:
    """``_apply_tool_packs`` for a turn with nothing to give: the org has no
    pack on (``allowed`` is empty), so no pack loads, the loader offers
    nothing and no skill is named. Gives the apply, the agent that the turn
    started with and the agent of the run after the apply."""
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit.test_tool_packs_choice import _bullets, _run_ctx

    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    from orchestrator.agents import build_orchestrator_agent

    agent = build_orchestrator_agent(with_history=False)
    agents = [agent]
    token = bind_tenant(ORG)
    try:
        turn = await tp.begin_turn(ORG, "chat-ts2a-nothing", MEMBER, "hello there")
        applied = await ti._apply_tool_packs(
            agents,
            turn,
            _run_ctx(),
            allowed=frozenset(),
            agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]),
            agent_config=CONFIG,
            bullets=_bullets(),
            offer_loader=True,
        )
    finally:
        release_tenant(token)
    return applied, agent, agents[0]


async def test_a_turn_with_nothing_to_give_binds_nothing_with_the_flag_off(
    store,
    monkeypatch,
) -> None:
    """Verifier P2. With the flag off, a turn that loads no pack, offers no
    loader and names no skill returns None, as main does. So the executor
    binds no run key and no record (``_pack_apply.context()`` never runs),
    the agent object stays the same, and the apply marks nothing on it.
    Mutation: the early return of such a turn is gone -> the apply marks the
    agent (``PACK_REFUSED_ATTR``) -> red. Mutation: both returns give the
    apply -> a run key binds -> red. The final return alone giving the apply
    is an equivalent mutant: the early return stops the turn first."""
    _flags(monkeypatch, "")
    applied, before, after = await _apply_nothing(monkeypatch)
    assert applied is None
    assert after is before and ex._pack_finder(after) is None
    assert not hasattr(before, ti.PACK_REFUSED_ATTR)


async def test_the_same_turn_with_the_flag_on_holds_find_tools(store, monkeypatch) -> None:
    """The decision of the audit: ``find_tools`` joins every turn where the
    flag is on, also with nothing to give. The apply comes back with a run
    key and a record, so ``FindTools._gate`` finds them, and the finder has
    no candidate. Mutation: the early return or the final return drops the
    test of ``candidates`` -> None -> red."""
    _flags(monkeypatch, ORG)
    applied, before, after = await _apply_nothing(monkeypatch)
    assert applied is not None and applied.run_key
    assert applied.record.slugs == () and applied.builds == ()
    finder = ex._pack_finder(after)
    assert after is not before and finder is not None
    assert finder.record is applied.record and finder.run_key == applied.run_key
    assert finder.candidates.packs == () and ex._pack_loader(after) is None
    context = applied.context()
    assert context[tp.PACK_RUN_KEY] == applied.run_key and context[tp.PACKS_KEY] is applied.record


def test_flag_off_no_provider_and_no_finder(store, monkeypatch) -> None:
    """With the flag empty, the run holds no ``FindToolsProvider`` and the
    apply makes no ``FindTools``. Mutation: attach the provider while the flag
    is empty -> red. Mutation: make the finder with the flag off -> red."""
    _flags(monkeypatch, "")
    _studio(monkeypatch)
    made: list[Any] = []
    real = tp.FindTools.__init__

    def spy(self: Any, *a: Any, **k: Any) -> None:
        made.append(self)
        real(self, *a, **k)

    monkeypatch.setattr(tp.FindTools, "__init__", spy)
    got = drive(monkeypatch, "plain")
    assert "load_skill_tools" in tool_names(got["bodies"][0]), "non-vacuity: packs are on"
    assert made == []
    assert len(got["views"]) == 1, "non-vacuity: the run applied the packs"
    assert _finders(got["views"]) == []


def test_flag_on_the_same_turn_offers_find_tools(store, monkeypatch) -> None:
    """Non-vacuity of the cases above: with the flag on for the bound org,
    the same plain turn holds ``find_tools`` once, before ``load_skill_tools``,
    and ONE ``FindTools`` after the whole inject chain. Mutation: two finders
    on one run (a second ``agent_with_providers``) -> red."""
    _flags(monkeypatch, ORG)
    _studio(monkeypatch)
    made: list[Any] = []
    real = tp.FindTools.__init__

    def spy(self: Any, *a: Any, **k: Any) -> None:
        made.append(self)
        real(self, *a, **k)

    monkeypatch.setattr(tp.FindTools, "__init__", spy)
    got = drive(monkeypatch, "plain")
    names = tool_names(got["bodies"][0])
    assert names.count(tp.FIND_TOOL) == 1, names
    assert names.index(tp.FIND_TOOL) < names.index("load_skill_tools"), names
    assert len(made) == 1
    # The view of the run after the whole inject chain holds the provider once.
    assert len(got["views"]) == 1 and len(_finders(got["views"])) == 1
    off = _golden()["turns"]["plain"]["names"]
    assert [n for n in names if n != tp.FIND_TOOL] == off, "only find_tools joins"


def test_the_flag_comes_from_the_bound_tenant_not_the_payload(store, monkeypatch) -> None:
    """R5. The flag names tenant B, the run binds tenant A, and the payload
    names B in each field a client could set. The run offers no ``find_tools``.
    Mutation: read the org of the flag from the request payload -> red."""
    _flags(monkeypatch, OTHER)
    _studio(monkeypatch)
    got = drive(
        monkeypatch,
        "plain",
        extra={"organization_id": OTHER, "org": OTHER, "org_id": OTHER, "tenant": OTHER},
    )
    names = tool_names(got["bodies"][0])
    assert "load_skill_tools" in names, "non-vacuity: packs are on for tenant A"
    assert tp.FIND_TOOL not in names
    assert len(got["views"]) == 1 and _finders(got["views"]) == []


# ── 3. The one reader ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "org", "on"),
    [
        ("", ORG, False),
        ("   ", ORG, False),
        (",,", ORG, False),
        ("*", ORG, True),
        (" * ", ORG, True),
        (ORG, ORG, True),
        (f"x, {ORG.upper()} ,y", ORG, True),
        (ORG, f" {ORG.upper()} ", True),
        (OTHER, ORG, False),
        ("*", "", False),
        ("*", None, False),
        (ORG, "", False),
    ],
)
def test_the_reader_fails_closed(monkeypatch, value: str, org: Any, on: bool) -> None:
    """``tool_search_on`` reads ``*`` and a comma list, in any case and with
    spaces, and fails closed. Mutation: an empty org reads as on, or ``*``
    needs an org list -> red."""
    monkeypatch.setattr(get_settings(), "tool_search_orgs", value, raising=False)
    assert tp.tool_search_on(org) is on


def test_the_reader_fails_closed_on_a_fault(monkeypatch) -> None:
    """A fault in the settings read gives False. Mutation: let the fault
    raise, or read it as on -> red."""
    import acb_common

    def _boom() -> Any:
        raise RuntimeError("settings")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert tp.tool_search_on(ORG) is False


def test_the_reader_reads_the_setting_on_each_call(monkeypatch) -> None:
    """A SIGHUP reload changes the value in place, so the reader keeps no
    copy. Mutation: cache the first answer -> red."""
    settings = get_settings()
    monkeypatch.setattr(settings, "tool_search_orgs", "", raising=False)
    assert tp.tool_search_on(ORG) is False
    monkeypatch.setattr(settings, "tool_search_orgs", ORG, raising=False)
    assert tp.tool_search_on(ORG) is True


def test_the_reader_does_not_call_packs_on(monkeypatch) -> None:
    """The one reader of ``TOOL_SEARCH_ORGS`` reads its own setting. It does
    not ask ``packs_on`` again: the caller has the allow list. Mutation:
    ``tool_search_on`` calls ``packs_on`` -> red."""
    called: list[Any] = []
    monkeypatch.setattr(tp, "packs_on", lambda org: called.append(org) or frozenset())
    monkeypatch.setattr(get_settings(), "tool_search_orgs", "*", raising=False)
    assert tp.tool_search_on(ORG) is True
    assert called == []


if __name__ == "__main__":  # write the golden again, on the code of main only
    if "--write" not in sys.argv:
        raise SystemExit("usage: python -m tests.unit.test_tool_search_off --write")
    patcher = pytest.MonkeyPatch()
    try:
        install_memory_mark_store(patcher)
        turns = capture(patcher)
        install_memory_mark_store(patcher)
        again = capture(patcher)
    finally:
        patcher.undo()
    if turns != again:
        raise SystemExit("two captures differ, so the golden would not hold")
    GOLDEN.write_text(
        json.dumps({"base": "9c838d023", "turns": turns}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {GOLDEN}")
