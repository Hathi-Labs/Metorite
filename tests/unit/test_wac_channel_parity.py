"""WS-47 — the WhatsApp run and the web chat run get the same function.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §17.

The owner's rule (2026-10-10, restated 2026-10-19): a channel changes
delivery, never function. A WhatsApp run loses only the web DELIVERY tools
(``whatsapp_ui.WITHHELD_TOOLS``), and each one has its WhatsApp form in
``whatsapp_ui.WHATSAPP_FORMS``.

The defect this file was written for (production, 2026-10-18). A DEWiN member
asked on WhatsApp for the IP billing of the Mumbai circle. The orchestrator
held ``crm_aggregate`` and the CRM facts block, called ``search_company``
twice, and answered "the company records hold nothing". Two causes:

* The batch path (``executor._run_with_maf_agent``), which the WhatsApp
  channel runs, built its system message from the scope rule and the
  integrations only. The org name, the Knowledge index, the memory and the
  number-system line of the web path never reached a WhatsApp run.
* No line told the orchestrator that business figures live in the CRM. The
  persona's rule 1 sends a company question to ``search_company`` first, and
  the WhatsApp scope rule named only tasks, projects and the calendar.

The REAL injection seam, the REAL CRM facts block, the REAL batch composer
and the REAL ``bot_run`` payload run here. Only the reads of the gateway and
the database are fakes.

R7 fence named here: ``wac-channel-parity``. A tool, a context block or a
prompt line that reaches one channel and not the other fails this file,
unless ``WHATSAPP_FORMS`` names it as delivery, or :data:`KNOWN_GAPS` names
it as an open defect with its ticket.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import orchestrator._tool_injection as ti
import pytest
from acb_skills import crm_tools
from acb_skills import whatsapp_ui as wui
from acb_skills.org_facts_tools import BLOCK_MARKER, CRM_ROUTING_LINE

ORG = "f01d029f-07b6-4892-830b-1480e9f7edf3"
MEMBER = "member@example.com"
QUESTION = "What is the IP billing for the Mumbai circle for September?"

#: The open defects of channel parity, each with its ticket in spec §17.3.
#: The fence names them, so a fix shows here as a failing ratchet, and the
#: entry goes in the same change.
KNOWN_GAPS: dict[str, str] = {
    "memory_context": "WAC-P2: the web route pre-reads Mem0, room, prefs, agent and "
                      "org memory into the turn. bot_run does not. remember() works",
    "mcp_servers": "WAC-P3: the web stream injects the org's MCP servers. The batch "
                   "path does not",
}


def _orchestrator_scope() -> list[str]:
    cfg = Path(__file__).resolve().parents[2] / "apps/agents/agent-orchestrator/config.json"
    return json.loads(cfg.read_text(encoding="utf-8"))["tool_scope"]


class _Orchestrator:
    """The orchestrator as MAF builds it: own tools in ``default_options``."""

    def __init__(self) -> None:
        async def search_company(query: str) -> str: ...
        async def remember(query: str) -> str: ...

        self.name = "orchestrator"
        self.default_options: dict[str, Any] = {
            "tools": [search_company, remember], "instructions": "Persona."}


def _names(agent: _Orchestrator) -> set[str]:
    return {ti._tool_name(t) for t in agent.default_options["tools"]}


@pytest.fixture()
def crm_org(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """An org on the CRM lists, whose member reads 2 answered facts."""
    import orchestrator.app_tools as app_tools

    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents:")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: ORG)
    monkeypatch.setattr(crm_tools, "questions_box_on", lambda: True)
    monkeypatch.setattr(crm_tools, "questions_org_on", lambda org: org == ORG)
    monkeypatch.setattr(crm_tools, "answer_first_org_on", lambda org: org == ORG)
    rows = [{"key": "amount", "meaning": "Invoices: the amount is Grand Total.",
             "subject": "s"},
            {"key": "date", "meaning": "Invoices: the date is Invoice Date.",
             "subject": "s"}]

    async def _facts() -> list[dict[str, Any]]:
        return list(rows)

    monkeypatch.setattr("acb_skills.org_facts_tools.crm_facts_for_run", _facts)
    return rows


async def _assemble(channel: str) -> tuple[set[str], str]:
    """The orchestrator of one run on *channel*, as the executor assembles
    it: the injection, then the CRM facts block. The holder of the run's CRM
    questions is the one each channel opens (the stream path's card holder,
    and ``bot_run``'s text holder)."""
    agent = _Orchestrator()
    if channel == "web":
        token = crm_tools.open_asks_for_run()
        release = crm_tools.release_asks
        ctx: Any = contextlib.nullcontext()
    else:
        token = crm_tools.open_text_asks("whatsapp")
        release = crm_tools.release_text_asks
        ctx = wui.whatsapp_run("orchestrator")
    try:
        with ctx:
            held = ti._inject_agent_tools(
                [agent], tool_scope=_orchestrator_scope(), agent_name="orchestrator",
                agent_config={}, no_egress=False)
            await ti._add_crm_facts_block([agent], held, mark_run=channel == "web")
    finally:
        release(token)
    return _names(agent), agent.default_options["instructions"]


# ── 1. The tools ─────────────────────────────────────────────────────────────


async def test_the_forms_map_is_exactly_the_withheld_tools() -> None:
    assert set(wui.WHATSAPP_FORMS) == set(wui.WITHHELD_TOOLS)
    assert all(form.strip() for form in wui.WHATSAPP_FORMS.values())


async def test_a_whatsapp_run_differs_from_a_web_run_only_by_delivery_tools(
    crm_org: list[dict[str, Any]],
) -> None:
    web, _ = await _assemble("web")
    wa, _ = await _assemble("whatsapp")
    only_web = web - wa
    assert only_web <= set(wui.WHATSAPP_FORMS), sorted(only_web - set(wui.WHATSAPP_FORMS))
    assert wa - web == {"whatsapp_ui"}, sorted(wa - web)


async def test_a_whatsapp_run_of_a_crm_member_holds_the_crm_reads_and_org_facts(
    crm_org: list[dict[str, Any]],
) -> None:
    wa, _ = await _assemble("whatsapp")
    assert {"crm_aggregate", "crm_list_modules", "crm_list_module_records",
            "crm_get_module_record", "org_facts", "crm_answer_question",
            "search_company"} <= wa
    # Every CRM tool that the orchestrator's scope names, none withheld.
    scoped = {n for n in _orchestrator_scope() if n.startswith("crm_") or n == "org_facts"}
    assert scoped <= wa, sorted(scoped - wa)


async def test_a_member_with_no_crm_fact_gets_no_org_facts_and_no_block(
    crm_org: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member that the gateway gives no CRM fact (no ``feature:crm``, or the
    access rule) reads none. ``org_facts`` and the block leave the run on
    both channels. The CRM reads stay injected, and the gateway refuses each
    one for this member: the access rule lives at the route, not here."""
    async def _none() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr("acb_skills.org_facts_tools.crm_facts_for_run", _none)
    for channel in ("web", "whatsapp"):
        names, text = await _assemble(channel)
        assert "org_facts" not in names and "crm_answer_question" not in names
        assert BLOCK_MARKER not in text and CRM_ROUTING_LINE not in text


async def test_an_org_with_the_crm_skill_off_holds_no_crm_read_on_either_channel(
    crm_org: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset({"crm"}))
    for channel in ("web", "whatsapp"):
        names, _ = await _assemble(channel)
        assert not names & crm_tools.CRM_TOOL_NAMES, (channel, sorted(names))


async def test_the_delivery_tools_stay_withheld_on_whatsapp(
    crm_org: list[dict[str, Any]],
) -> None:
    wa, _ = await _assemble("whatsapp")
    assert not wa & wui.WITHHELD_TOOLS, sorted(wa & wui.WITHHELD_TOOLS)


async def test_the_answer_check_covers_both_channels(crm_org: list[dict[str, Any]]) -> None:
    """``_apply_answer_check`` reads the held tools, never the channel."""
    from acb_skills.answer_check import holds_crm_tool

    web, _ = await _assemble("web")
    wa, _ = await _assemble("whatsapp")
    assert holds_crm_tool(sorted(web)) and holds_crm_tool(sorted(wa))


# ── 2. The prompt ────────────────────────────────────────────────────────────


async def test_the_prompts_differ_only_by_the_web_delivery_blocks(
    crm_org: list[dict[str, Any]],
) -> None:
    """Each web-only block belongs to a withheld tool: the UI directive to
    ``emit_generative_ui``, the output discipline block to ``write_artifact``
    and the design kits, the attachment rule to ``read_attachment``."""
    from acb_skills.addendum import ATTACHMENT_FAILURE_RULE

    _, web = await _assemble("web")
    _, wa = await _assemble("whatsapp")
    stripped = web
    for block in (ti._ui_first_directive(compact=False),
                  ti._build_output_discipline_block(compact=False, design_system=True),
                  ATTACHMENT_FAILURE_RULE):
        assert block in stripped
        stripped = stripped.replace("\n\n" + block, "", 1)
    assert stripped == wa


async def test_the_crm_routing_line_reaches_a_whatsapp_run(
    crm_org: list[dict[str, Any]],
) -> None:
    _, wa = await _assemble("whatsapp")
    assert BLOCK_MARKER in wa and CRM_ROUTING_LINE in wa
    assert wa.index(CRM_ROUTING_LINE) > wa.index(BLOCK_MARKER)


def test_the_whatsapp_scope_rule_names_the_crm_and_email() -> None:
    from gateway.routes.whatsapp_channel import bot_run

    for rule in (bot_run.SCOPE_RULE, bot_run.SCOPE_RULE_NATIVE, bot_run.SCOPE_RULE_WRITES):
        assert "email, the CRM and the other data" in rule


# ── 3. The per-turn context ─────────────────────────────────────────────────


@pytest.fixture()
def bound_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The run boundary reads, as a bound run of ``ORG`` gives them."""
    import orchestrator.executor as executor

    blocks = {"org": "DEWiN", "kb": "## Company knowledge\n- Sales (3 pages)"}
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG)
    monkeypatch.setattr(executor, "_run_org_display_name", lambda: blocks["org"])
    monkeypatch.setattr(executor, "run_number_system", lambda: "indian")
    monkeypatch.setattr(executor, "_knowledge_index_block", lambda name: blocks["kb"])
    return blocks


async def _whatsapp_system_message(payload: dict[str, Any]) -> str:
    import orchestrator.executor as executor

    seen: list[Any] = []

    class _Agent:
        async def run(self, run_input: Any) -> Any:
            seen.append(run_input)

            class _R:
                text = "ok"

            return _R()

    await executor._run_with_maf_agent(
        [_Agent()], agent_name="orchestrator", run_id="r1",
        thread_id="wa-thread-1", event_payload=payload, integrations={"zoho": {}})
    (run_input,) = seen
    first = run_input[0]
    assert str(first.role) == "system"
    return str(first.text)


async def test_a_whatsapp_turn_carries_every_context_block_of_a_web_turn(
    bound_run: dict[str, str],
) -> None:
    """The web turn's blocks come from ``_context_preamble_parts`` (through
    ``_build_event_message``). The WhatsApp turn's system message must carry
    each of them, with its own scope rule as the current context."""
    import orchestrator.executor as executor
    from acb_common.number_system import prompt_line
    from gateway.routes.whatsapp_channel import bot_run

    payload = bot_run.build_payload(QUESTION, [], MEMBER, native=True)
    system = await _whatsapp_system_message(payload)
    web_payload = {**payload, "system_context": "Web persona."}
    web_blocks = executor._context_preamble_parts(web_payload, {"zoho": {}}, "orchestrator")
    wa_blocks = executor._context_preamble_parts(payload, {"zoho": {}}, "orchestrator")
    shared = [b for b in web_blocks if "Web persona." not in b]
    assert shared, "the web turn has no shared block"
    for block in shared + wa_blocks:
        assert block in system, block[:60]
    assert f"Organisation: {bound_run['org']}." in system
    assert bound_run["kb"] in system
    assert prompt_line("indian") in system
    assert "Connected integrations: zoho." in system
    assert bot_run.SCOPE_RULE_NATIVE in system


def test_the_known_gaps_are_still_open() -> None:
    """A ratchet. When a gap closes, this fails: delete its entry and its
    row in spec §17.3 in the same change."""
    import inspect

    import orchestrator.executor as executor
    from gateway.routes.whatsapp_channel import bot_run

    payload = bot_run.build_payload(QUESTION, [], MEMBER, native=True)
    assert "memory_context" in KNOWN_GAPS and "memory_context" not in payload
    batch = inspect.getsource(executor._run_agent_inner)
    assert "mcp_servers" in KNOWN_GAPS and "_inject_mcp_servers(" not in batch
