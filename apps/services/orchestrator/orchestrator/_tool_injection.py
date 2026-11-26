"""Platform tool injection + system-prompt addendum for loaded agents.

Extracted from ``executor.py`` (foundation maintainability refactor) — no
behaviour change (log event strings + the KV-cache-stable addendum text are
byte-preserved). This is the layer that, for any loaded agent (native MAF or
GitHub Copilot SDK), injects the Metorite platform tools (call_agent,
web_search, write_artifact, memory, todo, HITL, diagnostics, …), gates each
through the risk-aware permission policy, applies per-agent tool scoping
(``tool_scope`` / ``own_tool_scope``), appends the tools system-prompt
addendum, and merges MCP servers from the registry.

Public surface re-exported by ``executor`` (external importers/tests reach these
as ``orchestrator.executor.<name>``): ``_gate_injected_tool``, ``_tool_name``,
``_apply_own_tool_scope``, ``_count_agent_tools``,
``_log_agent_tools_resolved``, ``_build_registry_block``,
``_build_injected_tools_addendum``, ``_inject_agent_tools``,
``_inject_mcp_servers``.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import functools
import os
import threading
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import Any

from acb_common import get_logger

from orchestrator._copilot_session import _apply_copilot_infinite_sessions

_log = get_logger("orchestrator.tool_injection")


# ── Guaranteed standard toolset ────────────────────────────────────────────
# Every loaded agent ALWAYS receives this small essential baseline, regardless
# of a per-agent ``config.json: tool_scope``.  These are the tools any agent
# needs whatever its specialty — write a file, track a todo, search the web,
# ask the user a question, check its own code, keep working notes.  A
# ``tool_scope`` may still ADD specialist tools on top, but it can no longer
# silently strip the basics: a scope that omitted ``write_artifact`` is exactly
# why agents fell back to fragile shell heredocs to author files (burning their
# output budget on shell-escaping and truncating mid-write).  Kept deliberately
# SMALL so the Berkeley "too many tools degrades accuracy" finding still holds.
_CORE_STANDARD_TOOL_NAMES: frozenset[str] = frozenset({
    "web_search", "fetch_page",          # web access
    "write_artifact", "share_artifact",  # file writing / delivery
    # One zip of this chat's files, as one download card (2026-10-11). In the
    # floor, because the owner asked for it in EVERY assistant, and all eight
    # in-tree agents set a tool_scope that would narrow a family away.
    # No shell, no egress. Fence: ``tests/unit/test_package_artifacts.py``.
    "package_artifacts",
    "emit_generative_ui",                # rich/interactive HITL UI (genui_2)
    "load_design_system",                # on-demand design language (genui_2 §7)
    "load_artifact_kit",                 # on-demand @cc/ui component reference
    "manage_todo_list",                  # task tracking panel
    "ask_questions",                     # HITL clarification
    "run_diagnostics", "get_errors",     # code / file error checking
    "save_note", "recall_notes",         # cross-session working memory
    # Coding skill (agent_coding_skill.md): an agent can author scripts
    # via a bounded Copilot session (code_task) and cheaply re-run the durable
    # scripts it accumulated (run_script). Workspace-jailed + env-scrubbed;
    # list_integrations shows what a script may reach (names, never values).
    # ⚠️ D85 and D129: the floor NAMES the two shell tools, and no agent but
    # the root dev agent gets them. `_withheld_shell_tools` takes every
    # SHELL_TOOLS member away, a personal agent's too, until the sandbox
    # broker covers the agent.
    "run_script", "code_task", "list_integrations",
    # Inter-agent delegation (multi_agent_orchestration.md Phase 0.1): the
    # floor previously guaranteed every tool an agent needs to work ALONE and
    # not one it needs to HAND OFF — a scope that omitted call_agent silently
    # stripped delegation while the (then scope-blind) addendum still described
    # it, so the agent flailed through fallbacks instead of delegating (the
    # email hand-off failure). Blast radius is bounded: each target's own
    # request_confirmation gate still requires a human, and the delegation
    # cycle/depth guards in acb_skills.agent_tools already cap recursion.
    "call_agent", "call_agents_parallel", "call_agent_background",
    # Fast typed decisions (WS-31 CP-13d, D75, customer_console.md §6A.14):
    # a calibrated yes/no, pick or rating through the one facade,
    # `acb_llm.decide`. With DECIDE_ENABLED off the chain does not inject it
    # and the addendum does not name it, so the floor NAMES it but the box
    # pays nothing for it (`decide_tools.decide_tool_enabled`).
    "decide",
    # Chat attachments (H-229): every chat has the upload button, so every
    # agent must read what the member attaches. On 2026-10-09 task-manager
    # held no reader, and it spent 9 minutes on shell, scripts and the web
    # over a 38 KB .docx before the run failed. It is pure parsing, read-only
    # and no shell tool, so D85 and the egress rule both keep it.
    # Fence: ``tests/unit/test_chat_upload_every_agent.py``.
    "read_attachment",
    # An attached image to the model of `tier-vision` (WS-VOX VOX-3,
    # projects_ai_chat.md §31). Each call draws credits, so it is dark behind
    # IMAGE_ANALYSIS_ORGS, and it is NOT in `_FLOOR_KEEP`: the email assistant
    # and the worker agent opt out. Fence:
    # ``tests/unit/test_attachment_analyze_image.py``.
    "analyze_image",
})


# ── The per-agent floor opt-out (``config.json: floor_opt_out``) ───────────
# The floor is the same for every agent, and it is not free. Each tool schema
# goes into EVERY model request of a run, and a chat turn makes about six
# requests. An agent that never calls a floor tool pays for it on each one.
# So an agent may name, in its config, the floor tools it does not want.
#
# One mechanism, and it can only take away. The names leave the resolved
# SCOPE (so no prompt text offers them) and the final tool list (so a
# fallback branch cannot bring them back), exactly as the D85 withheld names
# do. The workflow trio is in the list too: it is appended for every agent
# after the scope, so a scope can never narrow it.
#
# Fence: ``tests/unit/test_floor_opt_out.py``.

#: The tools that ``load_workflow_tools`` appends for every agent.
_WORKFLOW_TOOL_NAMES: frozenset[str] = frozenset({
    "list_workflows", "run_workflow", "get_workflow_run",
})

#: Floor tools that no agent may opt out of. ``ask_questions`` is how an
#: agent asks the member, and ``emit_generative_ui`` carries every form,
#: picker and plan card that waits for the member (the HITL surface).
#: ``read_attachment`` reads what the member attaches, and every chat can
#: attach a file.
_FLOOR_KEEP: frozenset[str] = frozenset({
    "ask_questions", "emit_generative_ui", "read_attachment",
})

#: The names an agent may put in ``floor_opt_out``.
FLOOR_OPT_OUT_ALLOWED: frozenset[str] = (
    (_CORE_STANDARD_TOOL_NAMES | _WORKFLOW_TOOL_NAMES) - _FLOOR_KEEP
)


def _floor_opt_out(
    agent_name: str | None, agent_config: dict[str, Any] | None,
) -> frozenset[str]:
    """The platform tools that *agent_config* opts out of, or none.

    It reads ``config.json: floor_opt_out``, a list of tool names. It keeps
    only the names of :data:`FLOOR_OPT_OUT_ALLOWED`. A name outside that set
    is ignored with a warning, so a typo or a kept tool can never take a tool
    away in silence. A config without the key changes nothing.
    """
    raw = agent_config.get("floor_opt_out") if isinstance(agent_config, dict) else None
    trimmed = _floor_trim(agent_name)
    if not raw:
        return trimmed
    if not isinstance(raw, list):
        _log.warning("executor.floor_opt_out_not_a_list", agent=agent_name)
        return trimmed
    names = {str(n) for n in raw}
    refused = names - FLOOR_OPT_OUT_ALLOWED
    if refused:
        _log.warning(
            "executor.floor_opt_out_ignored", agent=agent_name,
            entries=sorted(refused),
            hint="only a floor or workflow tool, and never ask_questions or "
                 "emit_generative_ui",
        )
    return frozenset(names & FLOOR_OPT_OUT_ALLOWED) | trimmed


def _floor_trim(agent_name: str | None) -> frozenset[str]:
    """The floor tools that the orchestrator gives up while
    ``ORCHESTRATOR_FLOOR_TRIM`` is on (WS-67 OA-8f), or none.

    It rides the seam of :func:`_floor_opt_out`, so the estimate of the pack
    cap, the scope, the addendum and the final list all agree. Only the
    orchestrator trims. With the flag off it returns no name, and a run is
    byte-identical to one without this code. Fence:
    ``tests/unit/test_floor_trim.py``.
    """
    if agent_name != "orchestrator":
        return frozenset()
    from acb_skills.tool_packs import FLOOR_TRIM_TOOLS, floor_trim_enabled

    return FLOOR_TRIM_TOOLS & FLOOR_OPT_OUT_ALLOWED if floor_trim_enabled() else frozenset()


def _sandbox_covers(agent_name: str, organization_id: str) -> bool:
    """True when the sandbox broker runs this agent's shell TOOLS (D85).

    It answers one question: does the broker run ``code_task``,
    ``run_script`` and ``install_dependency`` for this agent in this org
    (``maf_coding_engine.md`` §7.7 condition 2)? Only then may the tool half
    of D85 lift. It never lifts the host half: see :func:`_host_shell_refused`.

    It asks ``sandbox_broker.lifts_shell_block`` (WS-43d), never ``covers()``
    alone. ⚠️ The ``projects`` target of D86 is NOT such a cover:
    ``covers()`` is true there while the three host shell tools stay withheld
    (§16.3), so ``lifts_shell_block`` answers ``False`` for it. For every
    other target it is ``covers()``, which is ``False`` until WS-43f. A
    broker that cannot answer is no cover, so the block stays.
    """
    try:
        from orchestrator import sandbox_broker

        return sandbox_broker.lifts_shell_block(agent_name, organization_id) is True
    except Exception:  # fail closed: the three tools stay withheld
        return False


def _copilot_cli_in_broker_sandbox(agent_name: str, organization_id: str) -> bool:
    """True when this agent's Copilot CLI itself runs in a broker sandbox.

    Only that may lift the host half of D85. No such path exists: the
    container of ``copilot_sandbox.py`` is not the broker's (it holds the
    model loop, a key and a network, §4.2), and WS-43h moves app-builder off
    the CLI instead of into it. So this is ``False`` for every agent, and
    the CLI shell of every agent in scope is always refused today.
    """
    del agent_name, organization_id
    return False


#: Agents that D85 leaves as they are, by name. ``metorite`` is the root dev
#: agent (repo-root ``agents.py``). It edits platform code and runs the tests
#: through its Copilot CLI shell, under the B6 handler of the run's mode (its
#: factory sets no handler since task 560, H-211). Its root
#: ``config.json`` has no ``sharing`` block, so D85 would read it as shared.
#: The owner decided on 2026-10-03 (``maf_coding_engine.md`` §15.4): only an
#: admin of the first-party organization may run it, through any path, and it
#: keeps its terminal for them. ``executor._assert_may_run_agent`` enforces
#: that at every run boundary, and it is what makes this exemption safe.
#: Fences: ``test_shared_agent_shell_tools.py`` pins this set to one name, and
#: ``test_root_agent_first_party.py`` pins it inside the gated set.
_D85_OWNER_PENDING: frozenset[str] = frozenset({"metorite"})


def _d85_leaves_alone(agent_name: str | None, agent_config: dict[str, Any] | None) -> bool:
    """True for an agent that D85 does not touch, in either half.

    Only a name in :data:`_D85_OWNER_PENDING` is left alone: the root dev
    agent, which only a first-party admin may run (owner, 2026-10-03, §15.4).

    Every other agent is in scope, whatever its config says: ``shared`` (the
    default when the config has no ``sharing`` block), ``team``, ``personal``,
    a config that does not parse, and ``agent_config=None``. D129 (task 808,
    H-225) put ``personal`` in scope on 2026-10-27, because a member reaches a
    personal agent too, and a shared agent can delegate to one.
    """
    del agent_config  # the scope reads the name only since D129
    return agent_name in _D85_OWNER_PENDING


def _run_org() -> str | None:
    """The tenant of the run on this frame, from the run binding only (R11)."""
    try:
        from orchestrator.executor import _current_run_org
        return _current_run_org()
    except Exception:  # no run binding means no cover
        return None


def _withheld_shell_tools(
    agent_name: str | None, agent_config: dict[str, Any] | None,
) -> frozenset[str]:
    """The shell TOOLS this run must not inject (D85, the tool half).

    ``acb_skills.manifest.SHELL_TOOLS`` (``code_task``, ``run_script`` and
    ``install_dependency``) run code on the HOST today. ``run_script`` has the
    network and the agent's integration credentials. A SHARED agent serves
    every member of an organization from one process, so the owner blocked
    these tools for it on 2026-10-03 until the sandbox covers it. D129 blocked
    them for a PERSONAL agent too on 2026-10-27.

    It withholds nothing for an agent that :func:`_d85_leaves_alone`, and all
    three for every other one, unless :func:`_sandbox_covers` says the broker
    runs them for this agent in the run's org. The org comes from the run
    binding, never from the request or a tool argument (R11). No org means
    no cover. The executor binds the answer as ``shell_tools_withheld``.
    """
    try:
        from acb_skills.manifest import SHELL_TOOLS
    except ImportError:
        # acb_skills is absent, so _collect_injectable_platform_tools() returns
        # [] and this run injects no tool at all.
        return frozenset()
    if _d85_leaves_alone(agent_name, agent_config):
        return frozenset()
    org = _run_org()
    if agent_name and org and _sandbox_covers(agent_name, org):
        return frozenset()
    return frozenset(SHELL_TOOLS)


def _host_shell_refused(
    agent_name: str | None, agent_config: dict[str, Any] | None,
) -> bool:
    """True when the Copilot CLI's OWN shell must be refused (D85, host half).

    The CLI runs on the HOST, beside the gateway. ``covers()`` never lifts
    this: the broker runs the shell TOOLS in a container, and the CLI is not
    in that container. Only :func:`_copilot_cli_in_broker_sandbox` could lift
    it, and it is ``False`` for every agent today. So it is ``True`` for every
    agent in scope, and ``False`` for one that :func:`_d85_leaves_alone`.

    The executor binds the answer as ``host_shell_refused``, and
    ``permission_policy.guard_shared_agent_shell`` reads it.
    """
    if _d85_leaves_alone(agent_name, agent_config):
        return False
    org = _run_org()
    return not (agent_name and org and _copilot_cli_in_broker_sandbox(agent_name, org))


class NoEgressRefused(RuntimeError):
    """H-236 cannot make this run safe, so the run must not start.

    It is a refusal and not a fault, so nothing retries it: the batch path
    answers it before the self-anneal and the self-mutation clauses.
    """


def _run_covered(agent_name: str, organization_id: str | None = None) -> bool:
    """True when a run of *agent_name* on this frame is, or may be, covered (H-236).

    It is true when ``covers()`` is true for the agent and the run's org. It is
    also true when the scope names the ``projects`` target for that agent and
    org while the broker is not healthy, so a health probe cannot clear the
    control. The org comes from the run binding, never from input (R5). A
    broker that answers with an error fails closed. Each run boundary asks it
    ONCE, at the start, and binds the answer (:func:`_run_no_egress`), so a
    change of the scope during the run cannot clear the flag of its delegations.

    *organization_id* is the org that the run boundary itself was handed
    (server-side, never input). A batch run decides its flag before it binds
    its tenant, so it passes its org here (fix round 2).
    """
    if not agent_name:
        return False
    org = organization_id or _run_org()
    if not org:
        return False
    try:
        from orchestrator import sandbox_broker as sb
    except ImportError:  # no broker in this process, so no run was covered
        return False
    try:
        if sb.covers(agent_name, org):
            return True
        return sb.target_for_agent(agent_name) == sb.PROJECTS_TARGET and (
            sb.maf_coding_scope_allows(sb.PROJECTS_TARGET, org)
        )
    except Exception:  # fail closed: a broker bug must not open the control
        _log.warning("executor.run_cover_check_failed", agent=agent_name)
        return True


#: Agents whose context holds text that a stranger wrote (WS-17 EM-G11d,
#: UMCC, ``email_app_master_plan.md`` §12.3.13.8). Their own run keeps its own
#: tools, because the email assistant's writes ask a card (EM-G11c1). But a
#: mail can steer the model, so H-236 holds what it reaches past its own tools:
#:
#: * each run that it delegates to binds ``no_egress``
#:   (:func:`_delegated_no_egress`), so ``call_agent`` cannot reach an agent
#:   that holds ``fetch_page``;
#: * it gets no registry MCP server (:func:`_inject_mcp_servers`).
#:
#: Its own ``web_search`` and ``fetch_page`` leave through ``config.json:
#: floor_opt_out``. Its answer UP is held too: a CHAT SESSION that read it
#: (``acb_skills.egress``, keyed in Redis, round 3) asks a card for each
#: egress call (``permission_policy.UNTRUSTED_ANSWER_CONFIRM``), and each run
#: that it delegates to binds ``no_egress``. The set lives in
#: ``acb_skills.egress``, because ``agent_tools`` reads it too. Fences:
#: ``tests/unit/test_delegation_no_egress.py`` and
#: ``tests/unit/test_untrusted_frame.py``.
try:
    from acb_skills.egress import (
        MAIL_STORE_WRITE_REFUSED,
        MARK_CLEAR,
        MARK_MARKED,
        STORE_WRITES,
        UNTRUSTED_INPUT_AGENTS,
        is_delegation_tool,
        read_untrusted_answer,
        run_holds_mail,
        session_is_marked,
        session_mark_state,
    )
except ImportError:  # pragma: no cover - acb_skills ships with the platform
    UNTRUSTED_INPUT_AGENTS = frozenset({"email-assistant"})
    MARK_CLEAR, MARK_MARKED = "clear", "marked"
    STORE_WRITES = frozenset({
        "save_memory", "save_episode", "save_agent_memory", "save_org_memory", "save_note",
    })
    MAIL_STORE_WRITE_REFUSED = "{tool} did not run."

    async def run_holds_mail(agent_name: Any = None, ctx: Any = None) -> bool:
        """Fail closed: with no reader, each run holds mail."""
        return True

    def is_delegation_tool(tool: Any) -> bool:
        return False

    async def session_mark_state(ctx: Any = None) -> str:
        """Fail closed: with no reader, each session reads marked."""
        return MARK_MARKED

    def read_untrusted_answer(ctx: Any = None) -> bool:
        """Fail closed (round 3): with no reader, each session reads marked."""
        return True

    async def session_is_marked(ctx: Any = None) -> bool:
        """Fail closed: with no reader, each session reads marked."""
        return True


def _delegated_no_egress(parent_ctx: Any, child: str | None = None) -> bool:
    """True when the PARENT of a run that starts on this frame was no_egress.

    *parent_ctx* is the artifact context of the frame, read BEFORE the new run
    binds its own. An empty context means no parent. A parent that holds
    ``no_egress`` passes it on, so a child can never clear it, and a value that
    is not an explicit ``False`` reads as set. A parent in
    :data:`UNTRUSTED_INPUT_AGENTS` passes it on to any OTHER agent *child*
    (EM-G11d). The streaming sub-agent path binds the child's own name before
    it starts the batch run, so that run reads itself as its parent, and the
    same agent holds the same tools in both. Else the cover of the parent's
    agent decides, asked again now: that can only ADD the flag, so a parent
    whose binding predates a scope change, or a parent that bound no flag,
    still passes it on. No request field and no tool argument reaches this
    function.
    """
    if not parent_ctx:
        return False
    if parent_ctx.get("no_egress", False) is not False:
        return True
    parent = str(parent_ctx.get("agent_name") or "")
    if parent in UNTRUSTED_INPUT_AGENTS and parent != (child or ""):
        return True
    # Round 2: a parent that read an untrusted agent's answer passes it on.
    if read_untrusted_answer(parent_ctx):
        return True
    return _run_covered(parent)


def _covered_chain(
    agent_name: str | None, parent_ctx: Any, organization_id: str | None = None,
) -> bool:
    """True when a covered run is the run that starts now or a run above it.

    Each run boundary binds the answer as ``egress.COVERED_CHAIN_KEY``, beside
    ``no_egress`` (RX-1, D169). The parent's key passes down, so a grandchild of
    a covered run keeps the answer. The cover of the parent is asked again, as
    in :func:`_delegated_no_egress`, and that can only add the key. The values
    come from the server-bound run, never from a request.
    """
    from acb_skills.egress import COVERED_CHAIN_KEY

    parent = parent_ctx or {}
    if parent.get(COVERED_CHAIN_KEY) is True:
        return True
    above = str(parent.get("agent_name") or "")
    if above and _run_covered(above, organization_id):
        return True
    return _run_covered(str(agent_name or ""), organization_id)


async def _chat_marked(ctx: Any, organization_id: str | None = None) -> bool:
    """True when the chat of the run that starts now read outside mail (RX-1).

    Each run boundary awaits it once and binds the answer as
    ``egress.CHAT_MARK_KEY``, because the injection is sync and cannot ask Redis.
    It reads the mark that FAILS CLOSED, so a fault reads as marked, and a mark
    that this process never saw still counts after a restart. A parent's True
    passes down. With the org off (``egress.held_card_writes_on``) it reads
    nothing and adds no Redis call to the run. *ctx* names the session, from the
    server-bound run only, and *organization_id* binds the tenant of a batch run
    that has not bound it yet.
    """
    from acb_common.db import bind_tenant, current_tenant, release_tenant
    from acb_skills.egress import CHAT_MARK_KEY, held_card_writes_on

    if not held_card_writes_on(organization_id):
        return False
    if (ctx or {}).get(CHAT_MARK_KEY) is True:
        return True
    token = None
    try:
        if organization_id and not current_tenant():
            token = bind_tenant(organization_id)
        return await session_mark_state(ctx, fail_closed=True) != MARK_CLEAR
    except Exception:  # fail closed: an unreadable mark reads as marked
        return True
    finally:
        if token is not None:
            release_tenant(token)


def _run_no_egress(
    agent_name: str | None, parent_ctx: Any, organization_id: str | None = None,
) -> bool:
    """The ``no_egress`` answer of a run of *agent_name* that starts now (H-236).

    One rule binds a covered run and every run under it. So the answer is set
    when the parent was ``no_egress`` (:func:`_delegated_no_egress`), or when
    this run's own agent is covered (:func:`_run_covered`), whatever its parent.
    Each run boundary computes it once, before anything can fail, and binds it.
    """
    return _delegated_no_egress(parent_ctx, str(agent_name or "")) or _run_covered(
        str(agent_name or ""), organization_id,
    )


def _is_egress(tool: Any) -> bool:
    """The injection seam's test for an egress tool (H-236).

    The rule decides (``acb_skills.egress.is_egress_tool``, which fails
    closed). The two host web tools of the sandbox
    (``sandbox_tools.HOST_NETWORK_TOOLS``) are egress tools whatever their
    annotation says, so a drift in the registry can never give them back to
    a ``no_egress`` run. It reuses that list.
    """
    try:
        from acb_skills.egress import is_egress_tool
        from acb_skills.sandbox_tools import HOST_NETWORK_TOOLS
    except ImportError:  # no acb_skills means no tool was injected at all
        return False
    name = tool if isinstance(tool, str) else _tool_name(tool)
    return name in HOST_NETWORK_TOOLS or is_egress_tool(tool)


def _is_held_off(tool: Any, owner: str | None = None) -> bool:
    """True for a Projects or CRM write that a held chat does not hold (RX-1,
    D169): the rule ``acb_skills.egress.is_held_off_write``. *owner* names the
    agent of the run, so its own tools count too."""
    try:
        from acb_skills.egress import is_held_off_write
    except ImportError:  # no acb_skills means no pack tool was built at all
        return False
    return is_held_off_write(tool, owner=owner)


def _is_held_off_pack(tool: Any, slug: str) -> bool:
    """True for a write of pack *slug* that a held chat holds off by pack slug
    (CAL-1a): ``acb_skills.egress.is_held_off_pack_write``."""
    try:
        from acb_skills.egress import is_held_off_pack_write
    except ImportError:
        return False
    return is_held_off_pack_write(tool, slug)


def _covered_hold(agent_name: str | None) -> bool:
    """True when a covered run alone holds this run (H-236, RX-1).

    A chat that read outside mail drops the writes of :func:`_is_held_off`. A
    covered run holds for another reason, the member data of a sandboxed turn,
    and it keeps ``COVERED_PROJECTS_TOOLS`` of
    ``tests/unit/test_delegation_no_egress.py``. So a run in a covered chain, in
    a chat with no mark, skips the drop. The org comes from the run binding
    (:func:`_run_covered`). A held run of any other kind drops them. The mark is
    the one that the run boundary read from Redis (:func:`_chat_marked`), so a
    restart does not lose it, and a fault keeps the drop.
    """
    from acb_skills.egress import chat_marked_now, covered_chain

    covered = covered_chain() or (bool(agent_name) and _run_covered(str(agent_name)))
    return covered and not chat_marked_now()


#: The tool lists an injection knows how to read, by attribute.
_TOOL_POOLS = ("tools", "_tools")


def _inspectable(agent: Any) -> bool:
    """True when H-236 can see every tool that *agent* can call.

    A MAF ``Agent`` (its tools, its ``mcp_tools`` and its providers, which
    the guard view covers), a Copilot-shaped agent (``_tools`` and the
    permission guard) and a plain object with a known tool list. Any other
    MAF agent, for example a workflow agent, holds tools that this module
    cannot read, so the run is refused.
    """
    try:
        from agent_framework import Agent, BaseAgent
    except ImportError:
        return False
    if isinstance(agent, Agent) or hasattr(agent, "_permission_handler"):
        return True
    if isinstance(agent, BaseAgent):
        return False
    _do = getattr(agent, "default_options", None)
    pools = [_do.get("tools") if isinstance(_do, dict) else None]
    pools += [getattr(agent, attr, None) for attr in _TOOL_POOLS]
    return any(isinstance(p, list) for p in pools)


def _withhold_egress_from_agent(
    agent: Any, *, chat_hold: bool = True, owner: str | None = None,
) -> list[str]:
    """Take every egress tool and MCP server out of ONE per-run agent (H-236).

    The factory built this agent object for this run, as every injection site
    assumes. The lists are filtered in place, as ``_apply_own_tool_scope``
    does. Returns the names it took out. The MCP servers of a MAF agent
    (``mcp_tools``) leave on the guarded view (:func:`_with_egress_guard`).
    *chat_hold* also takes the writes of :func:`_is_held_off` (RX-1). *owner*
    names the agent of the run.
    """
    removed: list[str] = []

    def _off(tool: Any) -> bool:
        return _is_egress(tool) or (chat_hold and _is_held_off(tool, owner))

    def _filter(pool: Any) -> None:
        if not isinstance(pool, list):
            return
        kept = [t for t in pool if not _off(t)]
        removed.extend(_tool_name(t) for t in pool if _off(t))
        pool[:] = kept

    _do = getattr(agent, "default_options", None)
    if isinstance(_do, dict):
        _filter(_do.get("tools"))
    for attr in _TOOL_POOLS:
        _filter(getattr(agent, attr, None))
    opts = getattr(agent, "_default_options", None)
    if isinstance(opts, dict):
        _filter(opts.get("tools"))
        if opts.pop("mcp_servers", None):
            removed.append("mcp_servers")
    elif opts is not None:
        _filter(getattr(opts, "tools", None))
    if getattr(agent, "mcp_tools", None):
        removed.append("mcp_tools")
    return removed


def _with_egress_guard(
    agent: Any, *, chat_hold: bool = True, owner: str | None = None,
) -> Any:
    """A per-run view of a native MAF agent that holds no egress tool (H-236).

    The view carries no MCP server (``mcp_tools``, which MAF expands into
    tools at run time) and it carries the egress middleware. A Copilot agent
    gets its refusal from the permission guard instead
    (``permission_policy.guard_shared_agent_shell``), so it is returned as it
    is. So is a plain object that is not a MAF agent.
    """
    if hasattr(agent, "_permission_handler"):
        return agent
    from acb_skills.egress import EgressGuardProvider
    from agent_framework import Agent

    if not isinstance(agent, Agent):
        return agent
    from orchestrator._native_run_context import agent_with_providers

    view = agent_with_providers(agent, [EgressGuardProvider(chat_hold=chat_hold, owner=owner)])
    view.mcp_tools = []
    return view


def _drop_withheld(tools: list[Any], withheld: frozenset[str]) -> list[Any]:
    """*tools* without the D85 withheld names. A no-op when none is withheld."""
    if not withheld:
        return tools
    return [fn for fn in tools if getattr(fn, "__name__", "") not in withheld]


def _load_disabled_skill_families(agent_name: str | None) -> frozenset[str]:
    """Family slugs an admin explicitly disabled for *agent_name* (WS-23 S2).

    Reads the ``agent_skill_setting`` table written by
    ``PUT /agent/{name}/skills`` — a best-effort SYNC Postgres lookup,
    mirroring how this injection path already resolves per-run settings
    (``app_tools._granted_live_apps`` for app grants): a plain blocking
    ``acb_graph`` call, resolved once at run start from ``_inject_agent_tools``.

    ANY failure — package missing, table not migrated yet, DB down — returns
    the empty set, i.e. today's behavior (skills_registry.md rule 3: no rows
    change nothing; settings must never be able to brick injection).

    WS-29 acb_graph slice 7: ``agent_skill_setting`` is FORCE-RLS'd after
    phase-4 promotion, so an UNBOUND read returns ZERO rows — no disabled family
    would ever be honoured. Behind ``ACB_GRAPH_TENANT_BIND`` this binds the run's
    tenant so the admin's disable toggles are actually read. The opener is
    resolved on the event-loop frame (this runs on the loop, no worker-thread
    hop). flag OFF → the unbound ``get_session`` (byte-identical); flag ON + no
    resolvable org → fail closed (empty set = today's no-rows behaviour), never an
    unbound read on a FORCE-RLS'd catalog.
    """
    if not agent_name:
        return frozenset()
    try:
        from sqlalchemy import text  # noqa: PLC0415

        from orchestrator.executor import _graph_session_opener_current
        _open = _graph_session_opener_current()
        if _open is None:
            return frozenset()
        with _open() as s:
            rows = s.execute(
                text(
                    "SELECT family FROM agent_skill_setting "
                    "WHERE agent_name = :name AND instance = '' "
                    "  AND enabled = false"
                ),
                {"name": agent_name},
            ).fetchall()
        return frozenset(str(r[0]) for r in rows)
    except Exception:  # noqa: BLE001 — best-effort, never blocks injection
        return frozenset()


def _skills_fail_closed() -> bool:
    """The OWNER-GATED fail-closed switch (WS-23 S3) — ships OFF.

    When ``SKILLS_FAIL_CLOSED`` is truthy, an agent WITHOUT a ``config.json:
    tool_scope`` no longer receives every platform tool (today's fail-open
    ``None`` sentinel) but the named ``DEFAULT_PROFILE`` from
    ``acb_skills.skill_families`` (core floor + the general families the
    scope-out justified). Scoped agents are unaffected either way.

    Flipping this is a behavior change for every unscoped agent and is an
    OWNER action (work_plan.md §6 owner-gate registry; skills_registry.md §5)
    — never set it from agent-driven work. Read per call so tests can cover
    both positions without process restarts.
    """
    return os.environ.get("SKILLS_FAIL_CLOSED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _skills_index_only() -> bool:
    """The OWNER-GATED skills-index switch (WS-23 successor / QM-2) — OFF.

    When ``SKILLS_INDEX_ONLY`` is truthy the system-prompt addendum becomes a
    one-line-per-family INDEX and the full guidance is materialized into the
    agent's workspace (``agent-data/skills/<family>.md``) to be read on demand
    with the core-floor ``recall_notes`` tool. Same shape and same discipline
    as :func:`_skills_fail_closed`: read per call, ships OFF, flipping it is an
    OWNER action (work_plan.md §6) because it changes what EVERY agent sees.

    The canonical reader is ``acb_skills.skill_index.skills_index_only`` (the
    renderer branches there); this is the injection-seam alias so both switches
    are discoverable in one place.
    """
    try:
        from acb_skills.skill_index import skills_index_only  # noqa: PLC0415
        return skills_index_only()
    except ImportError:
        return False


def materialize_skill_bodies_for_agent(
    agent_name: str | None,
    workspace_root: str | None,
    *,
    tool_scope: list[str] | None = None,
    agent_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Lay down this agent's on-demand skill bodies (QM-2) — no-op when OFF.

    Called by the executor once per run, AFTER the workspace has been
    rehydrated from the blob store, so a fresh body is never overwritten by a
    stale restore. Resolves the SAME effective scope injection used (declared
    ``tool_scope`` ∪ core floor, ∩ enabled families, minus the D85 withheld
    shell tools of ``agent_config``) so a body describes exactly the tools
    the agent actually received.

    Returns ``{family: "written"|"unchanged"}``; ``{}`` when the switch is off
    (the default) or on any failure — a missing body file degrades to "the
    agent cannot read that guide", never to a failed run.
    """
    if not workspace_root or not _skills_index_only():
        return {}
    try:
        from acb_skills.skill_index import (  # noqa: PLC0415
            materialize_skill_bodies,
        )
        scope = _resolve_injected_scope(
            tool_scope,
            disabled_families=_load_disabled_skill_families(agent_name),
            withheld=(
                _withheld_shell_tools(agent_name, agent_config)
                | _floor_opt_out(agent_name, agent_config)
            ),
        )
        return materialize_skill_bodies(
            workspace_root,
            effective_scope=(
                frozenset(scope) if scope is not None else None
            ),
            registry_block=_registry_block_for(agent_name),
        )
    except Exception:  # noqa: BLE001 — never block a run over a body file
        _log.warning(
            "executor.skill_bodies_materialize_failed", agent=agent_name,
        )
        return {}


def _registered_platform_surface() -> set[str]:
    """Every registered platform tool name, plus the core floor.

    The declared side of an unscoped agent's scope, once something must
    narrow it (a disabled family, or the D85 withheld shell tools). A tool
    pack's names are not platform tools (WS-67 OA-7a), so they are not here.
    """
    try:
        from acb_skills.skill_families import injected_tool_names
    except ImportError:
        return set(_CORE_STANDARD_TOOL_NAMES)
    return set(_CORE_STANDARD_TOOL_NAMES) | set(injected_tool_names())


def _resolve_injected_scope(
    tool_scope: list[str] | None,
    *,
    disabled_families: frozenset[str] | None = None,
    withheld: frozenset[str] | None = None,
) -> set[str] | None:
    """Resolve which injected tool names an agent should receive.

    The declared scope and the skill toggles resolve first
    (:func:`_resolve_declared_scope`). ``withheld`` then takes names away,
    the core floor included. It is the D85 interim block: the shell tools
    that :func:`_withheld_shell_tools` keeps from an agent. An unscoped
    agent with a withheld tool gets an explicit set, so the addendum, the
    skill bodies and the injection read ONE scope that lacks the tool. With
    no withheld names the result is byte-identical to the declared scope.
    """
    resolved = _resolve_declared_scope(
        tool_scope, disabled_families=disabled_families,
    )
    if not withheld:
        return resolved
    if resolved is None:
        resolved = _registered_platform_surface()
    return resolved - set(withheld)


def _resolve_declared_scope(
    tool_scope: list[str] | None,
    *,
    disabled_families: frozenset[str] | None = None,
) -> set[str] | None:
    """Resolve the declared scope and the skill toggles.

    Returns ``None`` when there is no ``tool_scope`` (inject everything), or the
    set of allowed names = the agent's ``tool_scope`` UNIONed with the
    guaranteed :data:`_CORE_STANDARD_TOOL_NAMES` floor.  Unioning the core in
    means a scope can add specialist tools and narrow the rest, but can never
    strip the baseline (file writing, todo, web search, clarify, …).

    ``disabled_families`` (WS-23 S2) applies the admin's per-agent skill
    toggles as an INTERSECTION on top — the spec's rule 1, in the one place
    both runtimes pass through::

        effective = (declared tool_scope ∪ CORE) ∩ (enabled-family tools ∪ CORE)

    A toggle can therefore only narrow within what the code declares. The core
    floor is on both sides of the intersection so it always survives (rule 2;
    a ``'core'`` slug in ``disabled_families`` is ignored defensively — the
    API already refuses it with 422). With no disabled families the function
    is byte-identical to its pre-S2 self, including the ``None`` sentinel for
    unscoped agents (rule 3 — the fail-open flip is prepared below behind
    :func:`_skills_fail_closed`, ships OFF, and is owner-gated).
    """
    if tool_scope:
        base: set[str] | None = (
            set(tool_scope) | set(_CORE_STANDARD_TOOL_NAMES)
        )
    elif _skills_fail_closed():
        # WS-23 S3 (owner-gated, OFF by default): unscoped agents get the
        # named DEFAULT_PROFILE instead of everything. The workflow trio and
        # app grants keep their own append sites; this governs the static set.
        try:
            from acb_skills.skill_families import (  # noqa: PLC0415
                default_profile_tools,
            )
            base = set(_CORE_STANDARD_TOOL_NAMES) | set(default_profile_tools())
        except ImportError:
            base = None  # registry unavailable — behave exactly as today
    else:
        base = None
    if not disabled_families:
        return base
    try:
        from acb_skills.skill_families import injected_families  # noqa: PLC0415
    except ImportError:
        return base  # registry unavailable — settings cannot apply safely
    # Only families that are real, not the core floor, and carry static tools
    # can narrow the injected set. ('apps' is dynamic — the API refuses it;
    # 'workflows' tools are appended per-agent, and _inject_agent_tools also
    # honors the toggle at that append site.) A tool pack is not an injected
    # family (WS-67 OA-7a): a disabled pack narrows nothing here. It stops
    # the pack itself from OA-7c (one_assistant_context.md §4.2.1).
    families = injected_families()
    relevant = {
        slug for slug in disabled_families
        if slug in families
        and not families[slug].get("core")
        and families[slug].get("tools")
    }
    if not relevant:
        return base  # nothing narrowing ⇒ byte-identical to today (rule 3)
    enabled_tools = set(_CORE_STANDARD_TOOL_NAMES)
    for slug, fam in families.items():
        if slug not in relevant:
            enabled_tools |= set(fam["tools"])
    if base is None:
        # Unscoped agent: the declared side of the intersection is the whole
        # registered platform surface (∪ CORE) — fail-open, then narrowed.
        base = _registered_platform_surface()
    return base & enabled_tools


def _build_output_discipline_block(
    *, compact: bool = False, design_system: bool = True,
) -> str:
    """The 'all generated files live under outputs/' + design-language rule.

    Prose moved to ``acb_skills.addendum`` (WS-23 S3 — one text source for the
    generated addendum AND this native-MAF instruction append); this seam keeps
    the historical name both injection paths call. ``design_system=False``
    leaves out the ``load_design_system`` pointer, for an agent that does not
    hold the tool.
    """
    from acb_skills.addendum import build_output_discipline_block  # noqa: PLC0415
    return build_output_discipline_block(compact=compact, design_system=design_system)


def _with_attachment_rule(instructions: str, holds_reader: bool) -> str:
    """*instructions* with the attachment failure rule and the files rule, once.

    For a native MAF agent, which reads no Copilot addendum. Only an agent
    that holds ``read_attachment`` gets it, and the rule itself is the
    marker, so a second injection adds nothing (KV-cache stable).
    """
    from acb_skills.addendum import (  # noqa: PLC0415
        ATTACHMENT_FAILURE_RULE,
        FILES_ON_TASK_RULE,
    )
    if not holds_reader or ATTACHMENT_FAILURE_RULE in instructions:
        return instructions
    # IMG-A3 (task 2489): the Projects pack has no room for the files rule.
    return instructions + "\n\n" + ATTACHMENT_FAILURE_RULE + " " + FILES_ON_TASK_RULE


# ── The house voice (WS-52, ``specs/agent_writing_voice.md``) ──────────────
# Every agent reads ``acb_llm.voice.voice_prompt(AGENT)`` once, right after
# its own instructions and before every platform block, on both runtimes.
# A sub-agent reads ``core_prompt()``, the core with no overlay, because it
# answers its parent agent and not a member.
# :func:`_apply_voice` is the ONE place that puts it there, and
# :func:`_inject_agent_tools` calls it first, before any tool work, so a box
# that collects no platform tool still gives the voice. The text is static,
# so the prefix stays byte-stable and prompt caching covers it. It is not a
# tool, so ``config.json: floor_opt_out`` cannot remove it. An agent may add
# a stricter rule in its own instructions. Fence: ``test_agent_voice.py``.


def _with_voice(
    text: str, *, sub_agent: bool = False, role: str | None = None,
) -> str:
    """*text* with the house voice after it, once (the heading is the marker).

    *sub_agent* gives the core alone (:func:`acb_llm.voice.core_prompt`).
    *role* (``acb_llm.voice.ROLES``) adds that agent's role to the agent
    block. A sub-agent never reads a role, because it answers its parent.
    """
    from acb_llm.voice import (  # noqa: PLC0415
        AGENT, VOICE_HEADING, core_prompt, voice_prompt,
    )
    if VOICE_HEADING in text:
        return text
    block = core_prompt() if sub_agent else voice_prompt(AGENT, role=role)
    return f"{text}\n\n{block}" if text else block


def _apply_voice(
    agents: list[Any], *, is_sub_agent: bool = False,
    agent_name: str | None = None,
) -> None:
    """Give every agent in *agents* the house voice, on each runtime shape.

    * GitHubCopilotAgent (``_tools`` list): ``_default_options.system_message``.
      The Copilot addendum is appended later, after the voice.
    * Native MAF ``Agent``: ``default_options["instructions"]``.
    * Older MAF shape: a string ``instructions`` attribute.

    Best-effort per agent, like the rest of the injection: a frozen agent
    keeps its prompt and logs a warning.

    The role comes from ``acb_llm.voice.role_for(agent_name)``, the
    registry name that every executor site passes. The object's own
    ``name`` never selects a role, so a repo agent cannot claim one. Only
    the orchestrator has one (``specs/agent_writing_voice.md`` §11).
    """
    from acb_llm.voice import role_for  # noqa: PLC0415
    for agent in agents:
        try:
            _role = role_for(agent_name)
            if hasattr(agent, "_tools") and isinstance(agent._tools, list):
                opts = getattr(agent, "_default_options", None)
                if not isinstance(opts, dict):
                    continue
                sys_msg = opts.get("system_message")
                if isinstance(sys_msg, dict):
                    sys_msg["content"] = _with_voice(
                        sys_msg.get("content") or "", sub_agent=is_sub_agent, role=_role,
                    )
                elif isinstance(sys_msg, str):
                    opts["system_message"] = {
                        "mode": "append",
                        "content": _with_voice(sys_msg, sub_agent=is_sub_agent, role=_role),
                    }
                else:
                    opts["system_message"] = {
                        "mode": "append",
                        "content": _with_voice("", sub_agent=is_sub_agent, role=_role),
                    }
                continue
            _do = getattr(agent, "default_options", None)
            if isinstance(_do, dict):
                _do["instructions"] = _with_voice(
                    _do.get("instructions") or "", sub_agent=is_sub_agent, role=_role,
                )
                continue
            _instr = getattr(agent, "instructions", None)
            if isinstance(_instr, str):
                agent.instructions = _with_voice(_instr, sub_agent=is_sub_agent, role=_role)
        except Exception as _exc:  # noqa: BLE001
            _log.warning(
                "executor.voice_injection_failed",
                agent=getattr(agent, "name", None) or type(agent).__name__,
                error=str(_exc)[:200],
            )


def _ui_first_directive(*, compact: bool = False) -> str:
    """The proactive 'render UI by default' rule (generative_ui_2 Phase 2).

    Prose moved to ``acb_skills.addendum`` (WS-23 S3); same seam rationale as
    :func:`_build_output_discipline_block` — the addendum renderer and the
    native-MAF instruction append must share one byte-stable text source.
    """
    from acb_skills.addendum import ui_first_directive  # noqa: PLC0415
    return ui_first_directive(compact=compact)


def _whatsapp_tool_call(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """``acb_skills.whatsapp_acts.tool_call`` for one tool call (WS-47 WAC-4).

    The WhatsApp profile parks an allowed act only when it knows WHICH tool
    asks for the card, and with which arguments. This wrapper is the one
    boundary every injected tool and every native MAF agent's own tool
    crosses, so the call is named here and nowhere else. Outside a WhatsApp
    run with writes on, it does nothing. A fault gives no name, so the card
    is denied: it fails closed. ``AGENT_PERMISSION_MODE=approve_all`` skips
    the wrapper, and so no act can park.
    """
    import contextlib

    try:
        from acb_skills.whatsapp_acts import tool_call
    except Exception:
        return contextlib.nullcontext()
    return tool_call(fn, args, kwargs)


def _card_call(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """``acb_skills.ask_tools.card_call`` for one tool call (task 3130).

    It binds the act key of each card that the call draws: the tool, and the
    arguments that name its targets. A later card takes the place of a card
    that a chat message closed only when both have the same key. A
    delegation tool opens a scope with no key, so a card of the child run
    never takes the key of the call that started it. With no key, a card
    takes the place of no card, so the "Not done" line stays.
    """
    import contextlib

    try:
        from acb_skills.ask_tools import card_call
    except Exception:
        return contextlib.nullcontext()
    return card_call(fn, args, kwargs, keyless=is_delegation_tool(fn))


def _forget_card_marks() -> None:
    """``acb_skills.ask_tools.forget_card_marks`` (task 3595): no title and
    no proof of an act carry into this call. A fault is no act, so it
    records nothing later either."""
    try:
        from acb_skills.ask_tools import forget_card_marks
    except Exception:
        return
    forget_card_marks()


def _note_approved_act() -> None:
    """``acb_skills.ask_tools.note_approved_act`` for one call that returned
    (task 3595, PARK-LINE). It records the act of the call's card only when
    the member approved it AND the tool said at its success site that the
    act ran (``ask_tools.act_ran``). It reads no result text. A park then
    names it. A delegation tool records nothing itself: each tool of its
    child run records its own act into the parent's record. It never
    raises, so the tool's result always reaches the model."""
    try:
        from acb_skills.ask_tools import note_approved_act

        note_approved_act()
    except Exception:  # a line is never worth a lost result
        _log.debug("park_line.note_failed", exc_info=True)


def _permission_gate(
    tool_name: str, call_kwargs: dict[str, Any] | None, *, surface: str,
    facts: dict[str, Any] | None = None,
    after_untrusted_answer: bool = False,
) -> tuple[bool, str]:
    """Return (allowed, reason) for one tool call. Never raises. Logs EVERY decision.

    The one copy of the B6 gate. :func:`_gate_injected_tool` calls it for an
    injected tool, and ``_maf_mcp.McpPermissionGate`` calls it for an MCP
    function of a native MAF agent (WS-8c). *surface* names which one.

    Logging approvals too (not just denials) is what makes audit mode
    actually observable — an operator running in audit needs the full
    decision stream to judge the policy before flipping to enforce, and it
    is how we confirm the gate is even on this tool's execution path.

    ``call_kwargs`` (BO-7 cheap win 1/3) is the tool's actual call
    arguments — LLM tool-calling always produces keyword args (a JSON
    object mapped onto the function signature), so this is effectively
    the full call. Passed to build_tool_call_context so tools with a real
    command/path shape (run_script) are checked against what they're
    actually about to do, not just their name.

    ``facts`` are fields that the CALLER knows and the arguments do not
    carry. The MCP gate passes ``mcp_tool``, the server's
    ``mcp_annotations`` (H-299) and ``mcp_member_present`` (MCP-NC1), so
    ``decide()`` applies its MCP rule. They are set after the argument
    mapping, so a tool argument cannot write them. A caller that needs a
    fail-closed answer must not trust ``gate_error``: the MCP gate shows a
    card for every code but ``mcp_read_only``, ``mcp_read_only_key`` (MCP-C2)
    and ``mcp_member_run`` (MCP-NC1).
    """
    try:
        from acb_skills.permission_policy import (  # noqa: PLC0415
            build_tool_call_context, decide,
        )
        request = build_tool_call_context(tool_name, call_kwargs or {})
        if facts:
            request.update(facts)
        # WS-17 EM-G11d: the session's mark, set last, so neither a tool
        # argument nor a caller fact writes it. An async caller passes what
        # Redis said (``session_is_marked``, which fails closed). The sync
        # reader adds what this process saw (round 3).
        # WS-17 EM-G11f3 round 2: a run of the mail agent itself ignores the
        # mark. Its own tools ask their own cards (EM-G11c1), as in a direct
        # pane run, and the untrusted card would be a second card.
        request["after_untrusted_answer"] = (
            bool(after_untrusted_answer) or read_untrusted_answer()
        ) and not _run_reads_mail_itself()
        ok, code, _ = decide(request)
        mode = os.environ.get(
            "AGENT_PERMISSION_MODE", "enforce"
        ).strip().lower()
        enforced = (not ok) and mode == "enforce"
        _log.info(
            "permission.decision", mode=mode, tool=tool_name,
            approved=(not enforced), would_deny=(not ok), reason=code,
            surface=surface,
        )
        return (not enforced), code
    except Exception:  # noqa: BLE001 — a gate bug must never brick a tool
        return True, "gate_error"


def _gate_injected_tool(fn: Any) -> Any:
    """Wrap an injected platform tool with the risk-aware permission gate (B6).

    The Copilot-SDK ``on_permission_request`` hook only fires for the SDK's
    BUILT-IN shell/file/fetch capabilities — our injected function-tools
    (web_search, write_artifact, …) are executed directly by the agent-framework
    on the streaming/BYOK path and bypass that hook entirely. Wrapping each tool
    here makes the gate universal: whichever runtime path invokes the tool, the
    decision runs + logs (``permission.decision``, auto-correlated to the run via
    the E2 contextvars). ``enforce`` mode blocks a denied call; ``audit`` /
    ``approve_all`` never block. Preserves the tool's name/signature (functools
    .wraps) so the SDK/MAF tool registration is unchanged.
    """
    import functools  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    tool_name = getattr(fn, "__name__", "tool")

    def _gate(
        call_kwargs: dict[str, Any] | None = None, marked: bool = False,
    ) -> tuple[bool, str]:
        return _permission_gate(tool_name, call_kwargs, surface="injected_tool",
                                after_untrusted_answer=marked)

    # Task 2494: a mid-run steer never joins a tool result. The model reads it
    # as a user turn of its next call (`steer.SteerTurns`, attached by
    # `_apply_steer_turns`), so this wrapper gives the tool's result unchanged.

    # H-236: the gate's wrapper is trusted exactly as far as the tool it wraps.
    try:
        from acb_skills.egress import _register_platform_wrapper
    except ImportError:  # pragma: no cover — acb_skills ships with the platform
        def _register_platform_wrapper(_original: Any, wrapper: Any) -> Any:
            return wrapper

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _agated(*args: Any, **kwargs: Any) -> Any:
            # EM-G11d round 3: an egress call asks Redis whether this chat
            # session read an untrusted agent's answer, on any turn. A call
            # that reaches nothing asks nothing. An UNKNOWN state (a Redis
            # fault) fails closed for an egress act, so it asks the card. A
            # delegation is not one: each egress tool of the run that it
            # starts asks its own card, so a blip never holds a send.
            # WS-17 EM-G11f3: a store write in a run that holds mail gives
            # the fixed refusal at once. It asks no card, because a hidden
            # store is not a copy that the member can see or delete. An
            # UNKNOWN mark counts as mail (``run_holds_mail`` fails closed).
            if tool_name in STORE_WRITES and await _run_holds_mail_here():
                _log.info("permission.mail_store_write_refused", tool=tool_name)
                return MAIL_STORE_WRITE_REFUSED.format(tool=tool_name)
            # WS-17 EM-G11f3 round 2: the mail agent's own run reads no mark
            # (:func:`_run_reads_mail_itself`). The executor marks the calling
            # session BEFORE the sub-run starts, so without this its own
            # writes would ask the untrusted card as well as their own card.
            state = (await session_mark_state()
                     if _is_egress(fn) and not _run_reads_mail_itself()
                     else MARK_CLEAR)
            marked = state == MARK_MARKED or (
                state != MARK_CLEAR and not is_delegation_tool(fn))
            allowed, reason = _gate(kwargs, marked)
            if not allowed:
                return f"[blocked by permission policy: {reason}]"
            if _after_untrusted_answer(reason, marked) and not _handoff_child_held(fn):
                held = await _untrusted_answer_hold(tool_name, kwargs)
                if held is not None:
                    return held
            # WS-47 WAC-4: name the call for the WhatsApp card gate. A no-op
            # outside a WhatsApp run with writes on. Task 3130: the act key
            # of each card that this call draws.
            with _whatsapp_tool_call(fn, args, kwargs), _card_call(fn, args, kwargs):
                _forget_card_marks()
                result = await fn(*args, **kwargs)
            # Task 3595: an approved card whose tool said "the act ran" at
            # its success site. A raise never reaches this line.
            if not is_delegation_tool(fn):
                _note_approved_act()
            return result
        return _register_platform_wrapper(fn, _agated)

    @functools.wraps(fn)
    def _sgated(*args: Any, **kwargs: Any) -> Any:
        allowed, reason = _gate(kwargs)
        if not allowed:
            return f"[blocked by permission policy: {reason}]"
        if _after_untrusted_answer(reason) and not _handoff_child_held(fn):
            # A sync tool cannot wait for a card, so it is refused. A
            # WhatsApp run waits for no card (WS-47 WAC-P4): it parks the
            # call, or runs the call that the member confirmed.
            held = _untrusted_answer_hold_sync(tool_name, kwargs)
            if held is not None:
                return held
        return fn(*args, **kwargs)
    return _register_platform_wrapper(fn, _sgated)


#: The answers of :func:`_whatsapp_untrusted_answer` (``whatsapp_acts``).
_WA_APPROVED = "approved"
_WA_PARKED = "parked"
_WA_TOO_LONG = "too_long"

#: The most characters of the call's view on the web card: the context limit
#: of ``ask_tools.request_confirmation``. Review P1-1.
WEB_CALL_VIEW_CHARS = 4000


def _whatsapp_hold_text(verdict: str | None, tool_name: str) -> str | None:
    """The tool's answer for a WhatsApp verdict that holds the call, or None
    when the verdict does not hold it (approved, or no WhatsApp form)."""
    if verdict == _WA_PARKED:
        return _whatsapp_parked_text()
    if verdict == _WA_TOO_LONG:
        from acb_skills.whatsapp_acts import EGRESS_TOO_LONG_TEXT

        return EGRESS_TOO_LONG_TEXT.format(tool=tool_name)
    return None


async def _untrusted_answer_hold(tool_name: str, call_kwargs: dict[str, Any]) -> str | None:
    """Why an egress call after an untrusted answer does not run, or None.

    WS-47 WAC-P4: a WhatsApp run asks with its Confirm buttons where the web
    asks with a card. The call that the member confirmed runs (None). A
    call that now waits for the buttons answers the parked text. Every other
    run asks the card (:func:`_untrusted_answer_card`), and a no refuses."""
    verdict = _whatsapp_untrusted_answer(tool_name, call_kwargs)
    if verdict == _WA_APPROVED:
        return None
    held = _whatsapp_hold_text(verdict, tool_name)
    if held is not None:
        return held
    if await _untrusted_answer_card(tool_name, call_kwargs):
        return None
    return UNTRUSTED_ANSWER_REFUSED.format(tool=tool_name)


def _untrusted_answer_hold_sync(tool_name: str, call_kwargs: dict[str, Any]) -> str | None:
    """:func:`_untrusted_answer_hold` for a sync tool, which cannot wait for
    a card: only the WhatsApp answer can let it run."""
    verdict = _whatsapp_untrusted_answer(tool_name, call_kwargs)
    if verdict == _WA_APPROVED:
        return None
    return (_whatsapp_hold_text(verdict, tool_name)
            or UNTRUSTED_ANSWER_REFUSED.format(tool=tool_name))


def _whatsapp_untrusted_answer(tool_name: str, call_kwargs: dict[str, Any]) -> str | None:
    """The WhatsApp form of the untrusted-answer card (WS-47 WAC-P4).

    ``whatsapp_acts.answer_egress``: "approved" for the one call that the
    member confirmed, "parked" for a call that now waits for the Confirm
    buttons, else None, and the caller refuses as before. Outside a
    WhatsApp run it is None at once. A fault is None, so it fails closed."""
    try:
        from acb_skills.whatsapp_acts import answer_egress

        verdict = answer_egress(tool_name, call_kwargs)
    except Exception:  # fail closed: the card path refuses
        return None
    if verdict is not None:
        _log.info("permission.untrusted_answer_whatsapp", tool=tool_name, outcome=verdict)
    return verdict


def _whatsapp_parked_text() -> str:
    from acb_skills.whatsapp_acts import EGRESS_PARKED_TEXT

    return EGRESS_PARKED_TEXT


def _run_reads_mail_itself() -> bool:
    """True when the run on this frame IS an agent of
    :data:`UNTRUSTED_INPUT_AGENTS` (WS-17 EM-G11f3, round 2).

    The agent name comes from the run's bound artifact context, never from a
    request field or a tool argument. Such a run ignores the session's
    untrusted-answer mark: the mark holds what the CALLER does after the
    answer, and each write of the mail agent asks its own card. A fault reads
    False, so the mark applies and the gate fails closed."""
    try:
        from acb_skills.write_artifact import artifact_context

        name = str((artifact_context() or {}).get("agent_name") or "")
    except Exception:
        return False
    return name in UNTRUSTED_INPUT_AGENTS


async def _run_holds_mail_here() -> bool:
    """``acb_skills.egress.run_holds_mail`` for the run on this frame. The
    agent and the session come from the run's bound artifact context. A
    fault reads as mail, so it fails closed."""
    try:
        from acb_skills.write_artifact import artifact_context

        agent = str((artifact_context() or {}).get("agent_name") or "")
        return await run_holds_mail(agent)
    except Exception:  # fail closed
        return True


#: The answer of an egress call that the member did not approve, in a run
#: that read an untrusted agent's answer (WS-17 EM-G11d round 2).
UNTRUSTED_ANSWER_REFUSED = (
    "{tool} did not run. This chat read an answer of an agent that reads "
    "outside email, so a call that reaches outside needs the member's "
    "approval, and the member did not approve it. Do not ask again for the "
    "same call. Answer with what you have."
)


def _handoff_child_held(fn: Any) -> bool:
    """True when *fn* hands work to a specialist whose run will be held (EM-CR).

    The owner asked on 2026-10-20 that the agent may read an email with no
    permission prompt. The untrusted-answer card on a hand-off guarded
    nothing in a marked session: the run that the hand-off starts binds
    ``no_egress`` there (:func:`_delegated_no_egress`), so it holds no send,
    and each write of it asks its own card. So a hand-off whose child will be
    held asks no card.

    The answer comes from :func:`_run_no_egress`, the ONE function that the
    executor binds the child's flag with (``_run_sub_agent_streaming`` and
    the batch run), with the same context, so the two cannot disagree. Only
    a specialist tool of the orchestrator (``__delegates_to__``) qualifies.
    A direct egress tool, such as ``fetch_page``, still asks its card, and so
    does a hand-off whose child would NOT be held. A fault reads False, so
    the card asks. Fence: ``tests/unit/test_delegation_no_egress.py``.
    """
    child = getattr(fn, "__delegates_to__", None)
    if not isinstance(child, str) or not child:
        return False
    try:
        from acb_skills.write_artifact import artifact_context

        held = _run_no_egress(child, artifact_context())
    except Exception:  # fail closed: the card asks
        return False
    if held:
        _log.info("permission.untrusted_answer_skipped", tool=getattr(fn, "__name__", ""),
                  reason="child_held")
    return bool(held)


def _after_untrusted_answer(reason: str, marked: bool = False) -> bool:
    """True when the decision was the untrusted-answer card. A gate fault in a
    marked session counts too, so a bug never opens the control."""
    try:
        from acb_skills.permission_policy import UNTRUSTED_ANSWER_CONFIRM
    except ImportError:  # pragma: no cover
        return True
    return reason == UNTRUSTED_ANSWER_CONFIRM or (
        reason == "gate_error" and (marked or read_untrusted_answer())
        and not _run_reads_mail_itself())


def _untrusted_answer_card_refusal() -> str | None:
    """Why this run cannot show the card, as a code, or None (round 3).

    The one rule of ``_maf_mcp._card_refusal``: a WhatsApp run, a run with
    no member, a batch run and a run with no card channel cannot show a card.
    Then the call is refused at once, and never parks for an hour on the
    relay. The member and the batch flag come from the run's bound context."""
    try:
        from acb_skills.write_artifact import artifact_context

        from orchestrator._maf_mcp import _card_refusal

        ctx = artifact_context()
        member_present = bool(ctx.get("member")) and ctx.get("batch_thread") is not True
        refusal = _card_refusal(member_present)
    except Exception:  # fail closed: a fault refuses
        return "card_check_failed"
    return refusal[1] if refusal is not None else None


async def _untrusted_answer_card(tool_name: str, call_kwargs: dict[str, Any]) -> bool:
    """The member's card for one egress call after an untrusted answer. True
    only when the member approved it. A run that cannot show a card, and a
    fault, are a no at once (round 3).

    WS-47 review P1-1: the card shows the call through
    ``egress.egress_call_view``, the one builder that WhatsApp uses too. The
    recipients, the subject and the attachments come first and whole. A call
    whose key fields do not fit gets no card, and so a no."""
    refusal = _untrusted_answer_card_refusal()
    if refusal is not None:
        _log.info("permission.untrusted_answer_refused", tool=tool_name, outcome=refusal)
        return False
    try:
        from acb_skills.ask_tools import request_confirmation
        from acb_skills.egress import egress_call_view

        view = egress_call_view(tool_name, call_kwargs, WEB_CALL_VIEW_CHARS)
        if view is None:
            _log.info("permission.untrusted_answer_refused", tool=tool_name,
                      outcome="view_too_long")
            return False
        answer = await request_confirmation(
            f"Run {tool_name}? It reaches outside Metorite",
            "This chat read an answer from the email assistant, which reads "
            "outside email. A mail could ask for this call.",
            view,
        )
        return answer is True
    except asyncio.CancelledError:
        raise
    except Exception:  # a fault is a no
        return False


def _gate_own_maf_tools(tools: list[Any]) -> int:
    """Wrap a NATIVE MAF agent's own tools in :func:`_gate_injected_tool`.

    The Copilot branch below has always gated an agent's repo-baked tools. The
    native branch gated only the injected ones, so on Tier 1 an own tool got
    no ``permission.decision`` audit line. Since task 2494 a mid-run
    steer reaches the model as a user turn (``steer.SteerTurns``), never
    through this wrapper.

    The agent built each entry as a ``FunctionTool`` at construction. Its
    name, description and JSON schema were fixed THEN, from the original
    function, so swapping ``func`` changes none of them. Each entry is
    replaced by a shallow copy, so a module-level ``FunctionTool`` that other
    agents share is never mutated. Plain callables and tools whose ``func`` is
    not a plain function are left alone. Returns the number wrapped.
    """
    if os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower() == (
        "approve_all"
    ):
        return 0
    import copy  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    wrapped = 0
    for i, tool in enumerate(tools):
        orig = getattr(tool, "func", None)
        if not (inspect.isfunction(orig) or inspect.ismethod(orig)):
            continue
        if getattr(orig, "__cc_gated__", False):
            continue
        try:
            gated = _gate_injected_tool(orig)
            gated.__cc_gated__ = True  # type: ignore[attr-defined]
            clone = copy.copy(tool)
            clone.func = gated
            tools[i] = clone
            wrapped += 1
        except Exception:  # noqa: BLE001 — a frozen tool keeps its old func
            continue
    return wrapped


@functools.lru_cache(maxsize=32)
def _build_injected_tools_addendum(
    *,
    is_sub_agent: bool = False,
    effective_scope: frozenset[str] | None = None,
    own_risk: frozenset[tuple[str, bool, bool, bool]] = frozenset(),
    agent_name: str | None = None,
    image_org_on: bool = False,
) -> str:
    """Return a system-prompt addendum describing the Metorite-injected tools.

    ``image_org_on`` is a CACHE KEY only (WS-VOX VOX-3 fix round). The renderer
    reads the run's org itself (``addendum._dark_tools``), so an unscoped
    agent of a dark org and one of a lit org need two cache entries.

    Appended to every GitHub Copilot agent's system message at run time so the
    LLM knows the injected tools exist, when to use them, and what valid agent
    names are.  MAF agents receive these instructions via the MAF instructions
    field directly; this is only needed for the Copilot SDK path where the agent's
    own ``instructions.md`` was written without knowledge of Metorite injection.

    WS-23 S3: the prose is GENERATED — assembled by
    ``acb_skills.addendum.render_injected_tools_addendum`` from its ordered,
    family-tagged section registries (one text source; the skills catalog's
    token-cost measurement wires this same wrapper, so measured cost = real
    cost). This function keeps the cache + the dynamic registry block and is
    the seam every consumer (injection, catalog, tests) calls.

    When ``is_sub_agent=True`` (the agent is called via call_agent from a parent),
    a compact version is returned — tool names only, no workspace/commit instructions.
    This saves ~700 tokens per sub-agent invocation.

    ``effective_scope`` is the agent's RESOLVED injected-tool set (its
    ``tool_scope`` UNIONed with the core floor, intersected with enabled
    families) — sections describing tools the agent does not actually have are
    omitted (multi_agent_orchestration.md Phase 0.2), and a disabled skill
    family's sections drop with its tools (skills_registry.md S3 acceptance).
    ``None`` means an unscoped agent and yields the full addendum.

    Cached per ``(is_sub_agent, effective_scope)`` variant so each agent's
    system-prompt prefix stays byte-stable across turns — required for KV-cache
    hits.  Call ``_build_injected_tools_addendum.cache_clear()`` alongside
    ``_build_registry_block.cache_clear()`` when the registry changes.

    ``agent_name`` is the agent that reads the addendum. Its own entry leaves
    the registry block (:func:`_registry_block_for`), because an agent that
    delegates to itself only spends a turn. ``None`` lists every agent.
    """
    try:
        from acb_skills.addendum import (  # noqa: PLC0415
            render_injected_tools_addendum,
        )
    except ImportError:
        # acb_skills absent means _collect_injectable_platform_tools() returned
        # [] and nothing was injected — an empty addendum is the truthful text.
        return ""
    from acb_skills.tool_annotations import risk_summary_block

    # H-236: the risk block is per agent. ``own_risk`` names THIS agent's own
    # annotated tools (:func:`_own_risk`), and it is part of the
    # cache key, so each agent keeps a byte-stable prefix of its own.
    return render_injected_tools_addendum(
        is_sub_agent=is_sub_agent,
        effective_scope=effective_scope,
        registry_block=_registry_block_for(agent_name),
        risk_block=risk_summary_block(own=own_risk),
        # The text depends on the cache key alone, never on a tenant read.
        image_on=image_org_on,
    )


def _own_risk(tools: list[Any]) -> frozenset[tuple[str, bool, bool, bool]]:
    """``(name, read_only, destructive, open_world)`` of each tool in *tools*.

    H-236 (fix round 3). *tools* is ONE agent's tool list after injection:
    its own tools, plus the app and workflow tools that it was given. Every
    annotated tool joins, on the line its annotation names, as the shared
    registry listed it before the block went per agent. A platform name of
    ``tool_annotations._PLATFORM_STATIC`` is left out, because the block lists
    those already. A tool with no annotation stays out, as it always did.
    """
    try:
        from acb_skills.egress import _risk_of, tool_name
        from acb_skills.tool_annotations import _PLATFORM_STATIC
    except ImportError:
        return frozenset()
    out: set[tuple[str, bool, bool, bool]] = set()
    for item in tools:
        name = tool_name(item)
        if not name or name in _PLATFORM_STATIC:
            continue
        hints = _risk_of(item, name)
        if not hints:
            continue
        out.add((
            name, bool(hints.get("read_only")), bool(hints.get("destructive")),
            hints.get("open_world") is True,
        ))
    return frozenset(out)


@functools.lru_cache(maxsize=1)
def _build_registry_block() -> str:
    """Build the agent registry listing, cached for the lifetime of the process.

    The registry only changes when a new agent is registered (gateway restart
    or dynamic registration endpoint).  Caching keeps the system-prompt prefix
    byte-stable across turns — required for KV-cache hits (10x cost reduction
    per Manus benchmarks).

    Call ``_build_registry_block.cache_clear()`` after registering a new agent.

    Each agent is ONE line: its name and the first sentence of its
    description (:func:`_registry_line`). The block goes into every model
    request of a run, and a description of a paragraph only repeats what
    the target agent's own tools say once it runs.
    """
    agent_lines: list[str] = []
    try:
        from acb_skills.loader import may_delegate_to
        from gateway.routes.agent import _AGENT_REGISTRY  # noqa: PLC0415
        from gateway.routes.agent import _load_dynamic_agents
        all_agents = _load_dynamic_agents() + _AGENT_REGISTRY
        for a in all_agents:
            name = a.get("name", "")
            # D84, D92: a Copilot agent does not run as a specialist
            # (loader.refuse_copilot_specialist), so no run is told to call it.
            # app-builder stays, by owner call (loader.COPILOT_SPECIALIST_ALLOWED).
            if name and may_delegate_to(name, a.get("agent_runtime")):
                agent_lines.append(_registry_line(name, a.get("description", "")))
    except Exception:  # noqa: BLE001
        pass
    return (
        "Registered agents:\n" + "\n".join(agent_lines)
        if agent_lines
        else "Registered agents: check with the orchestrator if unsure."
    )


#: The longest summary a registry line carries, in characters.
_REGISTRY_SUMMARY_MAX = 160
#: A first sentence shorter than this takes the next sentence too.
_REGISTRY_SUMMARY_MIN = 80


def _registry_line(name: str, description: Any) -> str:
    """One registry line: ``  - 'name': <summary>``.

    The first sentence of *description*, on one line. A first sentence
    shorter than :data:`_REGISTRY_SUMMARY_MIN` characters (often only the
    agent's name, as "Projects Assistant — the AI chat.") says too little to
    route on, so the next sentence joins it. The line is cut at a word to
    :data:`_REGISTRY_SUMMARY_MAX` characters. :func:`_registry_block_for`
    finds the agent's own line by this prefix, so both read one format.
    """
    text = " ".join(str(description or "").split())
    cut = text.find(". ")
    while cut != -1 and cut + 1 < _REGISTRY_SUMMARY_MIN:
        cut = text.find(". ", cut + 2)
    if cut != -1:
        text = text[: cut + 1]
    if len(text) > _REGISTRY_SUMMARY_MAX:
        text = text[:_REGISTRY_SUMMARY_MAX].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return f"  - {name!r}: {text}"


def _registry_block_for(agent_name: str | None, exclude: Iterable[str] = ()) -> str:
    """The registry block that *agent_name* reads: every agent but itself.

    It filters the cached :func:`_build_registry_block`, so a stub of that
    function in a test still applies. ``None`` returns the whole block.
    *exclude* names more agents to leave out: the agents that a tool pack
    of the run refuses (WS-67 OA-7c fix round 1, :data:`PACK_REFUSED_ATTR`).
    """
    block = _build_registry_block()
    names = [str(n) for n in exclude if n]
    if agent_name:
        names.append(str(agent_name))
    if not names:
        return block
    prefixes = [_registry_line(name, "").rstrip() for name in names]  # "  - 'name':"
    return "\n".join(
        line for line in block.splitlines()
        if not any(line.rstrip() == own or line.startswith(own + " ") for own in prefixes)
    )


def _tool_name(tool: Any) -> str:
    """Best-effort name of a tool in any of the shapes agents carry them
    (plain callables, MAF AIFunction wrappers, dict specs)."""
    name = getattr(tool, "__name__", None) or getattr(tool, "name", None)
    if not name and isinstance(tool, dict):
        name = (tool.get("function") or {}).get("name") or tool.get("name")
    return str(name or "")


def _apply_own_tool_scope(agents: list[Any], own_scope: list[str] | None) -> None:
    """Filter an agent's OWN (repo-baked) tools to ``config.json: own_tool_scope``.

    ``tool_scope`` governs which PLATFORM tools get injected; this is its
    counterpart for tools the agent ships itself (HH-5).  A large baked tool
    surface (email-assistant carries ~60) degrades accuracy exactly like an
    over-injected one, and previously could not be narrowed per deployment.

    Must run BEFORE ``_inject_agent_tools`` so platform-injected tools are
    never subject to the agent's own scope.  With no matches the full set is
    kept (fail open + warning), mirroring ``tool_scope`` semantics.

    It reads each pool of :func:`_own_tool_pools` (WS-8o). Until 2026-10-06 it
    read only ``tools`` and ``_tools``, so it did nothing for a native MAF
    ``Agent``, which keeps its tools in ``default_options["tools"]``.
    """
    if not own_scope:
        return
    scope_set = set(own_scope)
    for agent in agents:
        for attr, lst in _own_tool_pools(agent):
            if not lst:
                continue
            kept = [t for t in lst if _tool_name(t) in scope_set]
            if kept:
                lst[:] = kept
            else:
                _log.warning(
                    "executor.own_tool_scope_no_match",
                    agent=getattr(agent, "name", type(agent).__name__),
                    attr=attr,
                    requested=own_scope,
                    available=[_tool_name(t) for t in lst],
                )


#: The CRM record write tools that Mirror mode takes from a run (WS-53
#: CRM-C7 item 4). Zoho owns the records there, so each would answer 409.
#: ``log_activity`` and ``crm_update_task`` stay: a Metorite-only note or
#: task saves in Mirror mode (CRM-Z6), and the activity route has no Mirror gate.
MIRROR_RECORD_WRITES: frozenset[str] = frozenset({
    "create_lead", "update_deal_status", "convert_lead",
    "crm_update_record",
})

#: The CRM write that ``CRM_PIPELINES_ORGS`` keeps out of a run (WS-CRM
#: CRM-M3a). An org off the list gets 404 from the route, so the tool leaves
#: the run, and the model never offers it.
PIPELINE_WRITES: frozenset[str] = frozenset({"crm_create_pipeline"})


def _crm_pipelines_open() -> bool:
    """True when ``CRM_PIPELINES_ORGS`` holds the org of this run (CRM-M3a).

    The reader is ``gateway.routes.crm.flags.pipelines_allowed``, the one that
    the create route reads. The org is the run's own (:func:`_run_org_or_none`),
    never an argument (R5). It reads the settings only, and it fails closed:
    no org, no gateway or an error reads as closed. Each build of a run asks
    it (:func:`_drop_mirror_record_writes`, :func:`_drop_pack_mirror_writes`).
    """
    try:
        from gateway.routes.crm.flags import pipelines_allowed
        return bool(pipelines_allowed(_run_org_or_none()))
    except Exception as exc:  # fail closed
        _log.info("executor.crm_pipelines_flag_unread", error=str(exc)[:200])
        return False


async def _drop_mirror_record_writes(agents: list[Any]) -> None:
    """Take the CRM record writes from this run's agents in Mirror mode.

    It runs at assembly, after :func:`_apply_own_tool_scope`, and only for an
    agent that holds one of :data:`MIRROR_RECORD_WRITES`. It reads
    ``GET /crm/mode`` as the run's member (``acb_skills.crm_tools.crm_mode``),
    never a request field. If the read fails, the tools stay, and the 409
    relay of the write tools is the fallback. A change in place holds for
    this run only (:func:`_own_tool_pools`).

    RX-1: a held chat reads the mode too. The three writes are on
    ``egress.HELD_CARD_WRITE_TOOLS``, so a held chat keeps them, and in Mirror
    mode a write would reach Zoho. A run in a covered chain reads nothing: the
    egress control drops the writes there (``egress.covered_chain``), and a
    covered run sends no request that it does not need (H-236). With the org off
    (``egress.held_card_writes_on``), a held run reads nothing too, as before.

    CRM-M3a: first, an org that ``CRM_PIPELINES_ORGS`` leaves out loses
    :data:`PIPELINE_WRITES`, in every run (:func:`_crm_pipelines_open`).
    """
    from acb_skills.egress import covered_chain, held_card_writes_on, no_egress_for_this_run

    held = {
        _tool_name(t)
        for agent in agents for _attr, pool in _own_tool_pools(agent) for t in pool
    }
    # CRM-M3a: the flag of the org first. It reads the settings only, so it
    # runs in every run, a covered one too.
    if held & PIPELINE_WRITES and not _crm_pipelines_open():
        _drop_own_tools(agents, PIPELINE_WRITES)
        _log.info("executor.crm_pipeline_writes_dropped", tools=sorted(PIPELINE_WRITES))
    if covered_chain() or (no_egress_for_this_run() and not held_card_writes_on()):
        return
    if not held & MIRROR_RECORD_WRITES:
        return
    try:
        from acb_skills.crm_tools import crm_mode
        mode = await crm_mode()
    except Exception as exc:  # the 409 relay of the write tools is the fallback
        _log.info("executor.crm_mode_unread", error=str(exc)[:200])
        return
    if mode == "mirror":
        _drop_own_tools(agents, MIRROR_RECORD_WRITES)
        _log.info(
            "executor.crm_mirror_writes_dropped",
            tools=sorted(held & MIRROR_RECORD_WRITES),
        )


def _append_instructions(agent: Any, block: str, marker: str) -> bool:
    """Append *block* to the system prompt of *agent*, at the END, on each
    runtime shape (the shapes of :func:`_apply_voice`). *marker* guards a
    second append. Returns True when the block went in."""
    joined = "\n\n"
    opts = getattr(agent, "_default_options", None)
    if hasattr(agent, "_tools") and isinstance(agent._tools, list) and isinstance(opts, dict):
        sys_msg = opts.get("system_message")
        text = (sys_msg.get("content") if isinstance(sys_msg, dict) else sys_msg) or ""
        if marker in text:
            return False
        if isinstance(sys_msg, dict):
            sys_msg["content"] = text + joined + block
        else:
            opts["system_message"] = {"mode": "append", "content": text + joined + block}
        return True
    _do = getattr(agent, "default_options", None)
    if isinstance(_do, dict):
        text = _do.get("instructions") or ""
        if marker in text:
            return False
        _do["instructions"] = text + joined + block
        return True
    text = getattr(agent, "instructions", None)
    if isinstance(text, str) and marker not in text:
        agent.instructions = text + joined + block
        return True
    return False


async def _crm_facts_rows(facts: Any, read: Callable[[], Awaitable[list[Any]]]) -> list[Any]:
    """The rows of an early read (*facts*), else of *read*. A fault in the
    early read gives no rows, as a failed read does (SPD-1 part 2). A cancel
    still ends the run: ``CancelledError`` is a ``BaseException``, so the
    ``except`` below never takes it."""
    if facts is None:
        return await read()
    try:
        rows = await facts
    except Exception:  # the block is advice: a run never fails on it
        return []
    return list(rows) if isinstance(rows, list) else []


async def _add_crm_facts_block(
    agents: list[Any], run_tools: Any, *, no_egress: bool = False,
    mark_run: bool = False, facts: Any = None,
) -> frozenset[str]:
    """Add the CRM facts block to a run that holds a CRM tool (WS-53
    CRM-Q32-5, ``crm_platform.md`` §18.14.4): the answered facts of the money
    modules and of the stages, at most 13 lines with the CRM routing line
    (``org_facts_tools.CRM_ROUTING_LINE``), at the END of the prompt,
    so the stable prefix stays byte-stable.

    It reads ``GET /org/facts?app=crm`` as the run's member
    (``acb_skills.org_facts_tools.crm_facts_block_for_run``), never a
    request field, and keeps nothing between runs. So a run of one org never
    carries the block of another org. The gateway gives no CRM fact outside
    ``CRM_QUESTIONS_ORGS``, so the block is empty there, and a box with the
    list empty sends no request at all. A ``no_egress`` run
    reads nothing, as :func:`_drop_mirror_record_writes` does. A failed read
    adds no block. A change in place holds for this run only, because the
    executor builds the agents of each run again.

    Fix round P0: a CRM tool counts when the run injected it OR an agent
    holds it as its own tool (the CRM Assistant, ``own_tool_scope``), the
    pools of :func:`_drop_mirror_record_writes`. The tool ``org_facts`` goes
    with the facts: a run that read no fact row (an org outside the list, a
    box with the list empty, a run with no CRM tool, a failed read) loses
    ``org_facts`` too, so the dark cost stays 0 and no run holds a tool that
    can only answer "no facts". Returns *run_tools* less what it took away,
    for the executor's ``run_tools``.

    CRM-Q32-8 fix round: with *mark_run* (the streamed run, which opened the
    holder of its CRM questions), it tells the holder whether the run still
    holds ``crm_answer_question`` (``crm_tools.mark_answer_tool``). A run
    that lost the tool shows no question card, because no tool could send
    the pick. A sub-agent shares its parent's holder, so it never marks.

    Chat speed SPD-1 part 2: *facts* is a read of ``crm_facts_for_run`` that
    the streamed run started early (``executor._EarlyReads``). The rule below
    still decides whether the run USES facts. When it does, the value of
    *facts* takes the place of a second read. When it does not, the value is
    never used. The early read has a wider gate than this rule (it cannot
    see ``holds_crm``), so it may already have gone out by then, as the
    member, and the run's ``finally`` cancels it only when it still runs. A
    read that failed gives no rows, as a read here does.
    """
    held = frozenset(run_tools or ())
    try:
        from acb_skills.crm_tools import CRM_TOOL_NAMES, questions_org_on
        from acb_skills.org_facts_tools import (
            BLOCK_MARKER,
            FACTS_GATED_TOOLS,
            crm_facts_block,
            crm_facts_for_run,
        )
    except ImportError:
        return held
    # CRM-Q32-8: `crm_answer_question` goes with `org_facts`.
    facts_tools = frozenset(fn.__name__ for fn in FACTS_GATED_TOOLS)
    own = {_tool_name(t) for agent in agents for _a, pool in _own_tool_pools(agent)
           for t in pool}
    holds_crm = bool((held | own) & CRM_TOOL_NAMES)
    rows: list[Any] = []
    # Follow-up: the run's own org must be on the list, so a run of another
    # org on a box that lists one sends no request either.
    if holds_crm and not no_egress and questions_org_on(_run_org_or_none()):
        rows = await _crm_facts_rows(facts, crm_facts_for_run)
    if not rows:
        _drop_own_tools(agents, facts_tools)
        if mark_run:
            _mark_answer_tool(agents, held - facts_tools)
        return held - facts_tools
    if mark_run:
        _mark_answer_tool(agents, held)
    block = crm_facts_block(rows)
    added = 0
    if block:
        added = sum(1 for agent in agents if _append_instructions(agent, block, BLOCK_MARKER))
    _log.info("executor.crm_facts_block", facts=len(rows),
              lines=block.count("\n") + 1 if block else 0, agents=added)
    return held


def _mark_answer_tool(agents: list[Any], held: frozenset[str]) -> None:
    """Tell the holder of this run whether it holds ``crm_answer_question``,
    as an injected tool or an agent's own (CRM-Q32-8 fix round)."""
    try:
        from acb_skills.crm_tools import mark_answer_tool
    except ImportError:
        return
    own = {_tool_name(t) for agent in agents for _a, pool in _own_tool_pools(agent)
           for t in pool}
    mark_answer_tool("crm_answer_question" in (set(held) | own))


def _facts_withheld() -> frozenset[str]:
    """``org_facts`` when the run's org is not on ``CRM_QUESTIONS_ORGS``
    (WS-53 CRM-Q32 follow-up). The name leaves the SCOPE, so the addendum
    of a Copilot-runtime agent never names a tool the run does not hold, and
    the final list, so no fallback brings it back. The org is the run's own,
    from the run boundary (R11). No org withholds it.

    A box with the list empty withholds nothing here. Its addendum hides
    ``org_facts`` already (``addendum._dark_tools``), and
    :func:`_add_crm_facts_block` takes the tool from the run. So the
    injected set of a box with no list stays byte-identical to the
    collection chain (``skills_registry.md`` rule 3,
    ``test_skill_toggle_enforcement.py``).

    On a listed org, a run with no holder of its CRM questions (a batch
    run) loses ``crm_answer_question`` too: only a streamed chat run opens
    the holder (``executor.run_agent_stream``). CRM-Q33: a WhatsApp run of
    an org on ``CRM_ANSWER_FIRST_ORGS`` opens the holder of its one text
    follow-up (``crm_tools.open_text_asks``), so it keeps the reply tool,
    which there only names a question for the gateway to ask.
    ``crm_propose_rule`` needs ``CRM_ANSWER_FIRST_ORGS`` and a holder too."""
    try:
        from acb_skills.crm_tools import (
            CRM_QUESTION_TOOLS,
            CRM_RULE_TOOLS,
            answer_first_org_on,
            questions_box_on,
            questions_org_on,
            run_asks,
            run_text_asks,
        )
        from acb_skills.org_facts_tools import FACTS_GATED_TOOLS
    except ImportError:
        return frozenset()
    if not questions_box_on():
        return frozenset()
    org = _run_org_or_none()
    if not questions_org_on(org):
        # CRM-Q32-8: `crm_answer_question` is withheld wherever `org_facts` is.
        return frozenset(fn.__name__ for fn in FACTS_GATED_TOOLS)
    off: set[str] = set()
    if not answer_first_org_on(org):
        # CRM-Q33: the rule tool is dark off CRM_ANSWER_FIRST_ORGS.
        off.update(fn.__name__ for fn in CRM_RULE_TOOLS)
    if run_asks() is None and run_text_asks() is None:
        # CRM-Q32-8, security review: a run with no holder of its questions
        # (a batch run: a workflow, a dispatch, /agent/run/async) shows no
        # card and has no human to pick, so it never holds the reply tool.
        off.update(fn.__name__ for fn in (*CRM_QUESTION_TOOLS, *CRM_RULE_TOOLS))
    return frozenset(off)


def _image_withheld() -> frozenset[str]:
    """``analyze_image`` when the run's org is not on ``IMAGE_ANALYSIS_ORGS``.

    WS-VOX VOX-3 fix round. The tool costs about 250 schema tokens, and its
    addendum section more, in each chat. A dark org pays nothing: the name
    leaves the SCOPE and the final list, like :func:`_facts_withheld`. The
    org is the run's own (:func:`_run_org_or_none`), never request input. The
    answer is per org and not per thread, so the tool list stays the same from
    turn to turn and the prompt cache holds. No org withholds it too.
    Fence: ``tests/unit/test_attachment_analyze_image.py``.
    """
    try:
        from acb_common.assistant_profile import image_analysis_for
    except ImportError:
        return frozenset({"analyze_image"})
    if image_analysis_for(_run_org_or_none()):
        return frozenset()
    return frozenset({"analyze_image"})


#: SBX-19 fix round. Whether ``compute_run`` fits in the request of THIS turn.
#: The tool has the LOWEST priority for a place: the pack step
#: (:func:`_apply_tool_packs`) sets it after it counts the base and the packs,
#: and it does not count the tool itself. A turn with two big packs then
#: keeps both packs and drops the tool, and never the reverse. The base was at
#: the 110-tool cap (``tool_packs.MAX_REQUEST_TOOLS``), so a place in the
#: always-on base would push a mixed turn over it. Default True: with packs
#: off no cap logic runs. Fence: ``tests/unit/test_compute_run.py``.
_COMPUTE_ROOM: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "compute_room", default=True)


def _compute_withheld() -> frozenset[str]:
    """``compute_run`` when the compute target is off for the run's org (SBX-19).

    The org is the run's own (:func:`_run_org_or_none`), never request input.
    The answer is per org and not per thread, so the tool list holds from turn
    to turn. The tool checks the flag again on each call, so a flip between
    the list and the call refuses the late call. Fence:
    ``tests/unit/test_compute_run.py``.
    """
    try:
        from acb_skills.compute_run import compute_enabled_for
    except ImportError:
        return frozenset({"compute_run"})
    if compute_enabled_for(_run_org_or_none()) and _COMPUTE_ROOM.get():
        return frozenset()
    return frozenset({"compute_run"})


def _whatsapp_withheld() -> frozenset[str]:
    """The tools a WhatsApp run does not get, or nothing outside one (WAC-10a).

    ``bot_run`` opens ``acb_skills.whatsapp_ui.whatsapp_run`` around the run,
    and a nested run copies the context, so the answer holds for every agent
    of that run.
    """
    try:
        from acb_skills.whatsapp_ui import WITHHELD_TOOLS, current_run  # noqa: PLC0415
    except ImportError:
        return frozenset()
    run = current_run()
    out = WITHHELD_TOOLS if run is not None else frozenset()
    # WS-63 PZ-6a fix round 1: a WhatsApp run of an org with its writes off
    # holds no profile write. Its scope rule says that it cannot change
    # data from WhatsApp, and the tools agree. `resolve_person` stays.
    from acb_common.assistant_profile import (
        WRITE_TOOL_NAMES,
        channel,
        channel_writes,
    )
    wa_off = (run is not None and not getattr(run, "writes", False)) or (
        channel() == "whatsapp" and not channel_writes())
    return out | WRITE_TOOL_NAMES if wa_off else out


def _whatsapp_ui_tools(agent_name: str | None) -> list[Any]:
    """``[whatsapp_ui]`` for the WhatsApp run's own agent, else nothing."""
    try:
        from acb_skills.whatsapp_ui import gets_ui_tool, whatsapp_ui  # noqa: PLC0415
    except ImportError:
        return []
    return [whatsapp_ui] if gets_ui_tool(agent_name) else []


def _drop_own_tools(agents: list[Any], names: frozenset[str]) -> None:
    """Take the tools named *names* out of each agent's OWN tool pools.

    A change in place holds for this run only: ``build_agents()`` makes the
    agents again for each run (see :func:`_own_tool_pools`).
    """
    for agent in agents:
        for _attr, lst in _own_tool_pools(agent):
            lst[:] = [t for t in lst if _tool_name(t) not in names]


def _drop_spawn_tool(agents: list[Any]) -> None:
    """Take ``spawn_subagents`` out of every agent of a run of an org that
    ``SPAWN_SUBAGENTS_ORGS`` does not name (WS-67 OA-9b).

    The tool is the orchestrator's own and joins at build time for a named org
    only. This is the second layer, at the one injection seam: a run with no
    org, or with another org, holds no spawn tool whatever built the agent.
    The tool is in no injected set, so the drop looks at each agent's OWN tool
    pools (:func:`_drop_own_tools`). It fails closed on a fault.
    """
    try:
        from acb_skills.subagent_contract import SPAWN_TOOL, spawn_subagents_on

        if spawn_subagents_on(_run_org()):
            return
        names = frozenset({SPAWN_TOOL})
    except Exception:
        names = frozenset({"spawn_subagents"})
    _drop_own_tools(agents, names)


def _own_tool_pools(agent: Any) -> list[tuple[str, list[Any]]]:
    """The tool lists of ONE agent, each list once, with a label for the log.

    A native MAF ``Agent`` (1.19) keeps its tools in
    ``default_options["tools"]``. ``RawAgent.__init__`` puts them there, and
    each run copies that list again, so a change in place holds for the run.
    This is the idiom of :func:`_withhold_egress_from_agent`. A
    ``GitHubCopilotAgent`` keeps ``_tools``, and a plain object ``tools``.
    ``mcp_tools`` and the tools of a context provider are not the agent's own
    tools, so no pool here holds them.
    """
    _do = getattr(agent, "default_options", None)
    candidates: list[tuple[str, Any]] = [
        ('default_options["tools"]',
         _do.get("tools") if isinstance(_do, dict) else None),
    ]
    candidates += [(attr, getattr(agent, attr, None)) for attr in _TOOL_POOLS]
    pools: list[tuple[str, list[Any]]] = []
    seen: set[int] = set()
    for label, pool in candidates:
        if isinstance(pool, list) and id(pool) not in seen:
            seen.add(id(pool))
            pools.append((label, pool))
    return pools


def _count_agent_tools(agents: list[Any]) -> int:
    """How many tools the pools of :func:`_own_tool_pools` hold, for all agents.

    Read once after :func:`_apply_own_tool_scope` (``own``) and once after
    :func:`_inject_agent_tools` (``total``). It counts tools that an agent
    holds, not tool calls. ``run_trace.tool_count`` counts calls.
    """
    return sum(len(pool) for agent in agents for _, pool in _own_tool_pools(agent))


def _log_agent_tools_resolved(
    agent_name: str | None, agents: list[Any], own: int,
) -> None:
    """Log ``executor.agent_tools_resolved`` after the tool injection (WS-8o).

    ``own`` is the count after the own tool scope, and ``total`` the count
    after the injection. The live check of ``email_app_master_plan.md``
    §10.4.14 reads ``own=43`` for one email chat.
    """
    _log.info(
        "executor.agent_tools_resolved",
        agent=agent_name,
        own=own,
        total=_count_agent_tools(agents),
    )


# ── A retry holds no more than the first attempt (access is an intersection) ──
# The batch self-anneal builds the agents again. It assembles them through the
# same function as the first attempt, so it reads the same scope, toggles and
# withholdings. These two then narrow it to the names the first attempt held,
# so a toggle read again, or a wider fallback, cannot widen a retry.
# Fence: ``tests/unit/test_anneal_retry_scope.py``.


def _run_tool_pools(agent: Any) -> list[list[Any]]:
    """Every tool list of ONE agent that the model can call from, each once.

    The pools of :func:`_own_tool_pools`, and the legacy Copilot
    ``_default_options["tools"]``, which :func:`_inject_agent_tools` also
    fills and :func:`_own_tool_pools` does not read.
    """
    pools = [pool for _label, pool in _own_tool_pools(agent)]
    opts = getattr(agent, "_default_options", None)
    legacy = opts.get("tools") if isinstance(opts, dict) else getattr(opts, "tools", None)
    if isinstance(legacy, list) and all(legacy is not p for p in pools):
        pools.append(legacy)
    return pools


def _held_tool_names(agents: list[Any]) -> frozenset[str]:
    """The names of every tool that *agents* hold, after every narrowing."""
    return frozenset(
        _tool_name(t) for agent in agents for pool in _run_tool_pools(agent) for t in pool
    )


def _narrow_to_held(
    agents: list[Any], held: frozenset[str], agent_name: str | None = None,
) -> None:
    """Take out of *agents* each tool whose name is not in *held*, in place.

    *held* is what the first attempt of the run held (:func:`_held_tool_names`).
    A retry may only narrow it. A change in place holds for this run only,
    because ``build_agents()`` makes the agents again for each run.
    """
    dropped: set[str] = set()
    for agent in agents:
        for pool in _run_tool_pools(agent):
            dropped.update(_tool_name(t) for t in pool if _tool_name(t) not in held)
            pool[:] = [t for t in pool if _tool_name(t) in held]
    if dropped:
        _log.warning(
            "executor.retry_tools_narrowed", agent=agent_name, tools=sorted(dropped),
        )


def _collect_injectable_platform_tools(agent_name: str | None = None) -> list[Any]:
    """Import + return the statically importable platform tools, in injection order.

    Pure collection/introspection — no agent is touched. This is the exact
    import chain :func:`_inject_agent_tools` has always run (extracted
    verbatim, WS-23 S1): an empty list means the mandatory delegation tools
    are unavailable and injection would have bailed out entirely. Also
    consumed read-only by the skills catalog (``acb_skills.skill_families`` +
    ``GET /integrations/skills``) so the catalog can never drift from what
    injection actually offers. Per-agent additions (granted Custom-App action
    tools, the workflow trio) are NOT part of this static set.

    *agent_name* picks the ``decide`` engine, and nothing else (WS-45 S1,
    ``ai_tier_routing.md`` §6.1). With ``AI_TIER_ROUTING`` empty, or with no
    name, the list is exactly what it was before.
    """
    try:
        from acb_skills.agent_tools import call_agent  # noqa: PLC0415
        from acb_skills.agent_tools import (call_agent_background,
                                            call_agents_parallel)
        _all_tools = [call_agent, call_agents_parallel, call_agent_background]
    except ImportError:
        return []  # acb_skills not installed in this env — skip silently

    # Zero-credential web tools — always available, no integration config needed.
    try:
        from acb_skills.web_tools import fetch_page  # noqa: PLC0415
        from acb_skills.web_tools import web_search
        _all_tools = _all_tools + [web_search, fetch_page]
    except ImportError:
        pass  # duckduckgo-search / httpx not installed — skip gracefully

    # File-writing artifact tool — surfaces created files in the UI sidebar.
    # share_artifact surfaces a file the agent already wrote (via shell/editor)
    # as a download/preview card so it never hand-builds links.
    try:
        from acb_skills.write_artifact import (  # noqa: PLC0415
            emit_generative_ui, share_artifact, write_artifact,
        )
        _all_tools = _all_tools + [
            write_artifact, share_artifact, emit_generative_ui,
        ]
    except ImportError:
        pass

    # One zip of the files of THIS chat, shown as a download card through the
    # same card seam (`write_artifact.announce_artifact`). No code, no egress.
    try:
        from acb_skills.package_artifacts import package_artifacts
        _all_tools = [*_all_tools, package_artifacts]
    except ImportError:
        pass

    # A deck or a report as a PDF file in the chat (H-315), through the same
    # card seam and the one PDF seam (`gateway.pdf_render`).
    try:
        from acb_skills.documents import make_document
        _all_tools = [*_all_tools, make_document]
    except ImportError:
        pass

    # SBX-19: Python over the tool results of this chat, in the sealed `compute`
    # target. It is no finder and no shell tool (D85, D225). It is dark per org:
    # `_compute_withheld` takes it out of the scope and the final list.
    try:
        from acb_skills.compute_run import compute_run
        _all_tools = [*_all_tools, compute_run]
    except ImportError:
        pass

    # A report: one file the chat draws as a card, and its PDF, from one spec
    # (projects_ai_chat.md §14.10). The same family and the same PDF seam.
    try:
        from acb_skills.reports import make_report
        _all_tools = [*_all_tools, make_report]
    except ImportError:
        pass

    # Chat attachments (H-229) — the text of a file the member attached in
    # THIS chat, by pure parsing: no subprocess, no code. It is no shell
    # tool, so D85 does not withhold it (`_withheld_shell_tools`).
    # `analyze_image` (WS-VOX VOX-3) sends an attached image to `tier-vision`
    # through the Console, and decodes no pixel here.
    try:
        from acb_skills.attachment_tools import analyze_image, read_attachment
        _all_tools = [*_all_tools, read_attachment, analyze_image]
    except ImportError:
        pass

    # Memory tools — active read/write to Mem0 + Graphiti knowledge graph.
    try:
        from acb_skills.memory_tools import (
            remember,
            recall_timeline,
            save_memory,
            save_episode,
            recall_agent,
            save_agent_memory,
            recall_org,
            save_org_memory,
        )
        _all_tools = _all_tools + [
            remember, recall_timeline, save_memory, save_episode,
            recall_agent, save_agent_memory, recall_org, save_org_memory,
        ]
    except ImportError:
        pass  # acb_memory not installed — skip gracefully

    # Todo-list tracking — VS Code Copilot parity panel above chat input.
    try:
        from acb_skills.todo_tools import manage_todo_list  # noqa: PLC0415
        _all_tools = _all_tools + [manage_todo_list]
    except ImportError:
        pass

    # HITL elicitation — agent asks user clarifying questions mid-stream.
    try:
        from acb_skills.ask_tools import ask_questions  # noqa: PLC0415
        _all_tools = _all_tools + [ask_questions]
    except ImportError:
        pass

    # Code error checking — lint/syntax/type checks after edits.
    # get_errors + its clearer alias run_diagnostics (same behaviour); both are
    # injected into every agent shape so either name resolves on Copilot + MAF.
    try:
        from acb_skills.error_tools import (  # noqa: PLC0415
            get_errors,
            run_diagnostics,
        )
        _all_tools = _all_tools + [get_errors, run_diagnostics]
    except ImportError:
        pass

    # Runtime dependency install — agents can add a Python package mid-task
    # (installed into agent-site, WS-49 BH-7, importable immediately).
    try:
        from acb_skills.dep_tools import install_dependency  # noqa: PLC0415
        _all_tools = _all_tools + [install_dependency]
    except ImportError:
        pass

    # Coding skill — code_task (bounded Copilot coding session that authors /
    # edits scripts under agent-data/scripts/) + run_script (zero-LLM re-run of
    # a saved script). Scripts are blob-store durable across restarts/redeploys.
    try:
        from acb_skills.code_tools import code_task  # noqa: PLC0415
        from acb_skills.code_tools import run_script
        _all_tools = _all_tools + [run_script, code_task]
    except ImportError:
        pass

    # Integration discoverability — which registry services resolved for this
    # run and which env vars a script may read for each (names, never values).
    try:
        from acb_skills.integration_tools import list_integrations  # noqa: PLC0415
        _all_tools = _all_tools + [list_integrations]
    except ImportError:
        pass

    # On-demand design system — the full ~16KB design.md is no longer injected
    # into every prompt; agents load it only when writing a heavy report or
    # bespoke custom HTML (generative_ui_2 §7).
    try:
        from acb_skills.design_tools import load_design_system  # noqa: PLC0415
        _all_tools = _all_tools + [load_design_system]
    except ImportError:
        pass

    # Same progressive-disclosure trick for the @cc/ui React component kit: the
    # index is a few hundred tokens and per-component signatures load only for
    # the components an artifact actually uses.
    try:
        from acb_skills.artifact_kit import load_artifact_kit  # noqa: PLC0415
        _all_tools = _all_tools + [load_artifact_kit]
    except ImportError:
        pass

    # Repo-scoped notes — agents maintain durable working memory.
    try:
        from acb_skills.note_tools import save_note  # noqa: PLC0415
        from acb_skills.note_tools import recall_notes
        _all_tools = _all_tools + [save_note, recall_notes]
    except ImportError:
        pass

    # Session history query — recall past conversations.
    try:
        from acb_skills.history_tools import query_history  # noqa: PLC0415
        _all_tools = _all_tools + [query_history]
    except ImportError:
        pass

    # CRM reads (WS-53 CRM-C7) — the twelve read tools of the ``crm`` family.
    # Each one reads a ``/crm`` route as the member, so the gateway's kill
    # switch, ``feature:crm`` and access rule decide. A scoped agent gets
    # them when its tool_scope names them, as with query_history.
    try:
        from acb_skills.crm_tools import CRM_TOOLS
        _all_tools = [*_all_tools, *CRM_TOOLS]
    except ImportError:
        pass
    # WS-53 CRM-Q32-5: `org_facts` loads with the `crm` family only, so the
    # core floor does not change (crm_platform.md §18.14.4). CRM-Q32-8:
    # `crm_answer_question` loads with it, on the same terms.
    try:
        from acb_skills.org_facts_tools import FACTS_GATED_TOOLS
        _all_tools = [*_all_tools, *FACTS_GATED_TOOLS]
    except ImportError:
        pass

    # Fast typed decisions (WS-31 CP-13d) — one question through the ONE
    # tenant facade, `acb_llm.decide`. Never the Console client directly.
    # Injected ONLY while DECIDE_ENABLED is on. `decide_tool_enabled` is the
    # one switch, and `addendum.rendered_parts` asks the same function, so
    # the prompt never advertises a tool that is not here.
    # WS-45 S1 (D90): an agent that AI_TIER_ROUTING covers gets the System-1
    # engine of the SAME tool name instead, on our Router's `tier-fast`.
    # `decide_tool_for` is the one place that picks the engine.
    try:
        from acb_skills.decide_tools import decide_tool_for
        _decide = decide_tool_for(agent_name)
        if _decide is not None:
            _all_tools = [*_all_tools, _decide]
    except ImportError:
        pass

    # GitHub code search — lexical + semantic search across repos.
    try:
        from acb_skills.github_tools import github_search  # noqa: PLC0415
        from acb_skills.github_tools import github_repo_search
        _all_tools = _all_tools + [github_search, github_repo_search]
    except ImportError:
        pass

    # H-236: these ARE the platform's tools. Record each one by identity, so
    # the egress rule trusts their registry entries and nothing that only
    # borrows their names (``acb_skills.egress._register_platform_callable``).
    try:
        from acb_skills.egress import _register_platform_callable
    except ImportError:
        return _all_tools
    for _fn in _all_tools:
        _register_platform_callable(_fn)
    return _all_tools


def _inject_agent_tools(
    agents: list[Any], *, is_sub_agent: bool = False,
    tool_scope: list[str] | None = None, agent_name: str | None = None,
    agent_config: dict[str, Any] | None = None,
    no_egress: bool = False,
    paste_frame: bool = False,
) -> frozenset[str]:
    """Inject cross-agent delegation tools into every loaded agent.

    ``paste_frame`` (HRD-14b) is True only for a top-level streamed run, which
    the chat route can bind. A delegated run, a sub-agent and a batch run
    pass nothing, so they get no paste middleware (:func:`_apply_paste_frame`).

    Returns the names of the platform tools this run holds after every
    narrowing (scope, skill toggles, D85, H-236, the floor opt-out and
    WhatsApp). The executor binds them as ``run_tools`` in the artifact
    context, and ``write_artifact.run_holds_tool`` reads them, so a tool that
    reaches another tool in-process asks the same answer (§14.10 of
    ``projects_ai_chat.md``). Empty when nothing was injected.

    ``agent_config`` is the agent's parsed ``config.json``. The D85 interim
    block (:func:`_withheld_shell_tools`) gives no agent but the root dev
    agent a ``SHELL_TOOLS`` member until the sandbox broker covers it, a
    personal agent included (D129). Every executor call site passes the
    config, and ``tests/unit/test_shared_agent_shell_tools.py`` says so. Its
    ``floor_opt_out`` list takes floor and workflow tools away from this
    agent (:func:`_floor_opt_out`).

    ``no_egress`` (H-236) is the answer of :func:`_run_no_egress` for this
    run, and every executor call passes it by name (the WS43-F24 AST fence).
    ``True`` takes every egress tool (``acb_skills.egress``) out of the
    injected set, the scope and the agent's own tools, takes its MCP servers
    away, and puts a native MAF agent behind the egress middleware, as a
    per-run view in ``agents``. It raises :class:`NoEgressRefused` for an
    agent whose tools it cannot read. ``False`` changes nothing.

    Adds ``call_agent`` and ``call_agent_background`` from ``acb_skills.agent_tools``
    so that any agent — MAF or GitHub Copilot SDK — can delegate sub-tasks to
    other registered agents without any changes to the external agent repo.

    When ``is_sub_agent=True``, a compact addendum is appended to Copilot SDK
    agents instead of the full workspace/commit guidance (~700 token saving per
    sub-agent invocation).

    When ``tool_scope`` is provided (from ``config.json: tool_scope``), only the
    named tools are injected.  This prevents the Berkeley leaderboard failure mode
    where every additional tool degrades model accuracy — inject only what the
    agent actually needs.  ``None`` means inject all tools (default).

    When ``agent_name`` is provided, Custom Apps granted to this agent
    (``app_grants.subject`` = ``agents:*`` or ``agent:<agent_name>``, RFC
    §4.7) are also injected as tools — one per declared manifest action,
    named ``app_<slug>_<action>`` — via ``orchestrator.app_tools``. Unlike
    ``tool_scope``, this is unconditional: a grant is an explicit per-agent
    permission, not a platform tool a static scope narrows.

    Injection is best-effort: failures are silently swallowed so they never
    block the main agent execution path.

    Injection targets:
        MAF Agent                — appends to ``agent.tools`` (list)
        GitHubCopilotAgent       — appends to ``agent._tools`` (list built at init;
                                   merged into SessionConfig.tools at session creation)
                                   + appends tool guidance to ``_default_options.system_message``
        Legacy Copilot SDK path  — appends to ``agent._default_options.tools`` (list)

    The house voice (WS-52) goes in first, for every agent and every shape,
    right after the agent's own instructions (:func:`_apply_voice`). A
    sub-agent gets the core with no overlay.
    """
    _apply_voice(agents, is_sub_agent=is_sub_agent, agent_name=agent_name)
    _drop_spawn_tool(agents)
    _all_tools = _collect_injectable_platform_tools(agent_name)
    if not _all_tools:
        if no_egress:  # H-236 holds even when nothing else is injected
            _apply_no_egress(agents, agent_name)
        else:
            _apply_chat_hold(agents, agent_name)
        _apply_dry_run(agents)  # WS-STU F4b: a skill test fails closed here too
        return frozenset()  # acb_skills not installed in this env — skip silently

    # ── Tool scoping: a guaranteed core floor + optional per-agent scope ───
    # A config.json ``tool_scope`` narrows the injected set to only what the
    # agent needs (technique #3 — the Berkeley Function-Calling Leaderboard
    # shows every model degrades as the tool count grows).  BUT the scope is
    # UNIONED with ``_CORE_STANDARD_TOOL_NAMES`` first, so the essential
    # baseline (write a file, todo, web search, clarify, diagnostics, notes) is
    # ALWAYS present no matter how the scope was written.  This is what stops an
    # agent whose scope forgot ``write_artifact`` from having no clean file-write
    # path and resorting to fragile shell heredocs.
    #
    # WS-23 S2: admin skill toggles resolve ONCE here at run start (same
    # mechanism as app grants / MCP below — a best-effort DB read keyed by
    # agent name) and intersect the scope. No rows ⇒ frozenset() ⇒ the resolve
    # below is byte-identical to pre-S2 behavior (skills_registry.md rule 3).
    _disabled_families = _load_disabled_skill_families(agent_name)
    if _disabled_families:
        _log.info(
            "executor.skill_families_disabled",
            agent=agent_name, families=sorted(_disabled_families),
        )
    # D85 (the interim block) and D129: an agent gets no shell tool until the
    # sandbox broker covers it, a personal agent too. The withheld names leave
    # the SCOPE, so the addendum below never describes a tool this agent does
    # not hold.
    _withheld = _withheld_shell_tools(agent_name, agent_config)
    if _withheld:
        _log.info(
            "executor.shell_tools_withheld",
            agent=agent_name, tools=sorted(_withheld),
        )
    # H-236: a run that a covered run delegated to gets no egress tool. The
    # names leave the SCOPE as well, so the addendum never offers one.
    _egress = (
        frozenset(fn.__name__ for fn in _all_tools if _is_egress(fn))
        if no_egress else frozenset()
    )
    # WS-17 EM-G11f3: an agent that reads mail holds no store write, so no
    # mail text reaches a memory store or a note (§12.3.13.9.1 item 5). The
    # names leave the SCOPE and the final list, where H-236 drops its set.
    _mail_store = (
        STORE_WRITES if (agent_name or "") in UNTRUSTED_INPUT_AGENTS else frozenset()
    )
    # The agent's own floor opt-out (config.json: floor_opt_out). The names
    # leave the SCOPE, so no prompt text offers them, and the final list
    # below, so no fallback branch brings them back.
    _opted_out = _floor_opt_out(agent_name, agent_config)
    if _opted_out:
        _log.info(
            "executor.floor_tools_opted_out",
            agent=agent_name, tools=sorted(_opted_out),
        )
    # WS-47 WAC-10a: a WhatsApp run (`acb_skills.whatsapp_ui`). Its web-only
    # tools leave the SCOPE and the final list, like the opt-out above, so no
    # prompt text offers them. The agent's own copies leave too.
    _wa_withheld = _whatsapp_withheld()
    if _wa_withheld:
        _drop_own_tools(agents, _wa_withheld)
    # WS-53 CRM-Q32 follow-up: `org_facts` (and, since CRM-Q32-8,
    # `crm_answer_question`) only for a run of an org on CRM_QUESTIONS_ORGS,
    # so no prompt names them for another org.
    _facts_off = _facts_withheld() | _image_withheld() | _compute_withheld()
    _scope_names = _resolve_injected_scope(
        tool_scope, disabled_families=_disabled_families,
        withheld=_withheld | _egress | _mail_store | _opted_out | _wa_withheld | _facts_off,
    )
    if _scope_names is not None:
        # Scope-typo guard (multi_agent_orchestration.md Phase 0.3): an entry
        # matching no injectable platform tool silently no-ops — the exact bug
        # where a scope listed "ask_user" (the native SDK tool's name, not an
        # injected tool) and nothing warned. Surface each unknown entry.
        _known = {fn.__name__ for fn in _all_tools} | set(_CORE_STANDARD_TOOL_NAMES)
        for _entry in (tool_scope or []):
            if _entry not in _known:
                _log.warning(
                    "executor.tool_scope_unknown_entry",
                    entry=_entry,
                    hint="matches no injectable platform tool; it will no-op",
                )
        _extra_tools = [fn for fn in _all_tools if fn.__name__ in _scope_names]
        if not _extra_tools:
            # Neither the scope nor the core matched any known tool (e.g. every
            # optional import above failed) — fall back to the full set.
            _log.warning(
                "executor.tool_scope_no_match",
                requested=tool_scope,
                available=[fn.__name__ for fn in _all_tools],
            )
            _extra_tools = _all_tools
    else:
        _extra_tools = _all_tools

    # ── Custom Apps granted to this agent (RFC §4.7, Phase 3b) ─────────────
    # Independent of tool_scope above: a granted app is an explicit per-agent
    # permission (app_grants.subject = 'agents:*' or 'agent:<name>'), not a
    # platform tool a static scope list would name — so these are ALWAYS
    # added regardless of tool_scope, then gated through the SAME
    # _gate_injected_tool pipeline as everything else below. Best-effort: a
    # lookup failure must never block the rest of injection.
    if agent_name:
        try:
            from orchestrator.app_tools import load_app_action_tools  # noqa: PLC0415
            _extra_tools = _extra_tools + load_app_action_tools(agent_name)
        except Exception:  # noqa: BLE001
            _log.warning("executor.app_tools_injection_failed", agent=agent_name)

    # ── Published workflows as tools (workflows_app.md F13) ────────────────
    # Same stance as app tools: org-internal governed automations, always
    # offered (list/run/status trio, not one tool per workflow), gated
    # through the same _gate_injected_tool pipeline below. A `tool` node that
    # writes outward is broker-gated and approval-noded. A `pm_task` or
    # `pm_lifecycle` node has no such gate, so `run_workflow` refuses a
    # workflow that holds one (`service._member_blind_write_nodes`).
    #
    # The trio is appended AFTER scope filtering, so the S2 skill toggle is
    # honored HERE: an explicit admin disable of the 'workflows' family
    # removes it — that is narrowing, which rule 1 permits. (Granted
    # Custom-App action tools above are deliberately NOT toggle-governed: an
    # app grant is an explicit per-agent permission with its own management
    # surface — spec §2 decision note.)
    if agent_name and "workflows" not in _disabled_families:
        try:
            from orchestrator.workflow_tools import load_workflow_tools  # noqa: PLC0415
            _extra_tools = _extra_tools + load_workflow_tools(agent_name)
        except Exception:  # noqa: BLE001
            _log.warning("executor.workflow_tools_injection_failed", agent=agent_name)

    # D85, the last word: the no-match fallback above restores the WHOLE
    # chain, shell tools included. So the withheld names leave the final
    # list too, whatever branch built it. So do the opted-out names, which
    # covers the workflow trio appended above.
    _extra_tools = _drop_withheld(
        _extra_tools, _withheld | _opted_out | _wa_withheld | _facts_off)
    # H-236, the same last word: an app or workflow tool that reaches outside
    # the platform leaves too, whatever branch added it.
    if no_egress:
        _extra_tools = [fn for fn in _extra_tools if not _is_egress(fn)]
    if _mail_store:
        _extra_tools = _drop_withheld(_extra_tools, _mail_store)
    # WAC-10a: the WhatsApp UI tool goes to the run's own agent only. An agent
    # it calls answers in text.
    if _wa_withheld:
        _extra_tools = _extra_tools + _whatsapp_ui_tools(agent_name)

    # Gate every injected tool with the risk-aware permission policy (B6). This
    # closes the live gap where injected function-tools (web_search, …) executed
    # on the Copilot-BYOK/streaming path bypass the SDK's on_permission_request
    # hook. functools.wraps keeps __name__/signature so name-based special-cases
    # (call_agent, ask_questions) and SDK/MAF registration are unaffected.
    if os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower() != (
        "approve_all"
    ):
        _extra_tools = [_gate_injected_tool(fn) for fn in _extra_tools]
    # The run's tools after every narrowing above. ``functools.wraps`` keeps
    # each name through the gate.
    _run_tool_names = frozenset(getattr(fn, "__name__", "") for fn in _extra_tools)

    # Whether emit_generative_ui actually landed in this agent's toolset — the
    # gate above wraps the callables but functools.wraps preserves __name__.
    # Native MAF agents get NONE of the Copilot addendum (which is where the
    # proactive UI rule + genUI guidance live for Copilot), so when they DO
    # carry the tool we append the same directive to their instructions —
    # otherwise a MAF agent has the tool but no system-prompt nudge to use it
    # (generative_ui_2 Phase 2).
    _emit_injected = any(
        getattr(fn, "__name__", "") == "emit_generative_ui" for fn in _extra_tools
    )
    # Present in BOTH the full and compact directive variants, so the
    # idempotency guard holds whichever one a given agent/sub-agent receives.
    _UI_DIRECTIVE_MARKER = "Rich UI by default"
    # Heads both the full and compact Copilot addendum → guards re-injection.
    _ADDENDUM_MARKER = "## Metorite Platform Tools"
    # The output-discipline block opens with one of these (the full and the
    # compact variant) → the idempotency marker for the native-MAF append
    # below. It is no longer the load_design_system pointer, because an agent
    # that opts out of that tool gets the block without the pointer.
    from acb_skills.addendum import OUTPUT_DISCIPLINE_MARKERS  # noqa: PLC0415

    def _has_output_discipline(text: str) -> bool:
        return any(m in text for m in OUTPUT_DISCIPLINE_MARKERS)

    # The design-system pointer names a tool. Offer it only to an agent that
    # holds the tool (the floor opt-out can take it away).
    _design_injected = any(
        getattr(fn, "__name__", "") == "load_design_system" for fn in _extra_tools
    )
    # The attachment failure rule. The Copilot addendum carries it in its
    # "Chat attachments" section, and a native MAF agent reads no addendum.
    _reader_injected = any(
        getattr(fn, "__name__", "") == "read_attachment" for fn in _extra_tools
    )

    for agent in agents:
        injected = False
        _agent_label = getattr(agent, "name", None) or type(agent).__name__

        # ── GitHubCopilotAgent (agent-framework-ag-ui) ──────────────────────
        # Stores tools in self._tools (a list); fed into SessionConfig at run time.
        # Must be patched BEFORE the MAF path because GitHubCopilotAgent also
        # sets self.tools = [] (empty) via its base class — we don't want to
        # append to that empty list and skip the real _tools.
        #
        # IMPORTANT: _prepare_tools() (called at session creation) only converts
        # FunctionTool / CopilotTool / MutableMapping — plain async callables are
        # silently skipped.  We must wrap each function with normalize_tools() first
        # so the Copilot SDK actually registers them and the LLM can call them.
        try:
            if hasattr(agent, "_tools") and isinstance(agent._tools, list):
                # Try to import normalize_tools for FunctionTool wrapping.
                try:
                    from agent_framework._tools import \
                        normalize_tools as _norm  # noqa: PLC0415
                except ImportError:
                    _norm = None  # type: ignore[assignment]

                existing_names = {
                    getattr(getattr(t, "func", t), "__name__", None)
                    for t in agent._tools
                }
                # Gate the agent's OWN repo-baked tools too (B6): these execute
                # on the Copilot-BYOK path bypassing on_permission_request, and
                # they're NOT in _extra_tools, so wrapping only our injected set
                # left them ungated. Re-wrap each existing FunctionTool's .func
                # with the permission gate in place (no-op in approve_all mode).
                if _gate_on := (
                    os.environ.get("AGENT_PERMISSION_MODE", "enforce")
                    .strip().lower() != "approve_all"
                ):
                    for _t in agent._tools:
                        _orig = getattr(_t, "func", None)
                        if callable(_orig) and not getattr(
                            _orig, "__cc_gated__", False
                        ):
                            _gated = _gate_injected_tool(_orig)
                            _gated.__cc_gated__ = True  # type: ignore[attr-defined]
                            try:
                                _t.func = _gated
                            except Exception:  # noqa: BLE001 — some tools frozen
                                pass
                for fn in _extra_tools:
                    if fn.__name__ not in existing_names:
                        # Wrap as FunctionTool so _prepare_tools() picks it up.
                        wrapped = _norm([fn])[0] if _norm is not None else fn
                        agent._tools.append(wrapped)

                # Append tool guidance to the system message so the LLM knows
                # these tools exist, what they do, and what agent names are valid.
                # The agent's own instructions.md was written before Metorite
                # injection and has no mention of call_agent / web_search / etc.
                #
                # _default_options is a plain dict; system_message inside it has
                # the form {'mode': 'append', 'content': '<text>'}.  We must use
                # dict access (not setattr) and preserve the nested structure.
                try:
                    addendum = _build_injected_tools_addendum(
                        is_sub_agent=is_sub_agent,
                        # Scope-aware (Phase 0.2): describe only the tools this
                        # agent actually received; None = unscoped, full text.
                        effective_scope=(
                            frozenset(_scope_names)
                            if _scope_names is not None else None
                        ),
                        # H-236: this agent's own risky tools, from its own list.
                        own_risk=_own_risk(list(agent._tools)),
                        # The registry block leaves out the agent itself.
                        agent_name=agent_name,
                        image_org_on=not _image_withheld(),
                    )
                    opts = getattr(agent, "_default_options", None)
                    if isinstance(opts, dict):
                        existing_sys = opts.get("system_message")
                        # Idempotency guard (parity with the MAF path): a second
                        # injection on the SAME agent object must not append a
                        # duplicate addendum — that would bloat the prompt and
                        # break the KV-cache stability this module is built for.
                        _existing_txt = (
                            existing_sys.get("content")
                            if isinstance(existing_sys, dict)
                            else existing_sys if isinstance(existing_sys, str)
                            else ""
                        ) or ""
                        if _ADDENDUM_MARKER in _existing_txt:
                            pass
                        elif isinstance(existing_sys, dict):
                            # Preserve mode:'append'; extend content field.
                            existing_sys["content"] = _existing_txt + addendum
                        elif isinstance(existing_sys, str):
                            opts["system_message"] = {"mode": "append", "content": existing_sys + addendum}
                        else:
                            opts["system_message"] = {"mode": "append", "content": addendum}
                except Exception:  # noqa: BLE001
                    pass

                # Neutralise the Copilot backend's infinite-session compaction so
                # it doesn't false-trip "context length exceeded" on a wrongly-small
                # assumed window for our BYOK models (see
                # _copilot_infinite_session_config). Best-effort; no-op if opted out.
                try:
                    _apply_copilot_infinite_sessions(agent)
                except Exception:  # noqa: BLE001
                    pass

                injected = True
        except Exception as _exc:  # noqa: BLE001
            _log.warning(
                "executor.tool_injection_failed",
                agent=_agent_label, shape="copilot_tools",
                error=str(_exc)[:200],
            )

        if injected:
            continue

        # ── Native agent_framework Agent (current MAF API) ──────────────────
        # The current Agent (agent_framework._agents.Agent) has no top-level
        # `.tools`; its tools live in `default_options["tools"]` (a list). MAF
        # accepts plain async callables, so append without wrapping. Used by the
        # email-assistant (a native MAF agent).
        try:
            _do = getattr(agent, "default_options", None)
            if isinstance(_do, dict) and isinstance(_do.get("tools"), list):
                existing_names = {
                    getattr(getattr(t, "func", t), "__name__", None)
                    for t in _do["tools"]
                }
                # Gate the agent's OWN tools too, BEFORE the injected ones
                # join the list (they are gated above already). The same B6
                # gate that the Copilot branch gives its own tools.
                _gate_own_maf_tools(_do["tools"])
                for fn in _extra_tools:
                    if fn.__name__ not in existing_names:
                        _do["tools"].append(fn)
                # Give native MAF agents the LIVE agent registry so they can
                # discover and delegate to ALL registered specialists via
                # call_agent (Copilot-SDK agents get this through the
                # system-message addendum; native MAF agents otherwise only know
                # the agents hard-named in their instructions.md, so a newly
                # registered specialist — e.g. a product-planner — is invisible).
                if not is_sub_agent and any(
                    fn.__name__ == "call_agent" for fn in _extra_tools
                ):
                    try:
                        # The agent itself is not in the list: a hand-off
                        # to itself only spends a turn. WS-67 OA-7c: nor is
                        # an agent that a tool pack of this run refuses.
                        _reg = _registry_block_for(
                            agent_name,
                            exclude=getattr(agent, PACK_REFUSED_ATTR, ()) or (),
                        )
                        _prev = _do.get("instructions") or ""
                        if _reg and "Delegatable agents" not in _prev:
                            _do["instructions"] = (
                                _prev
                                + "\n\n## Delegatable agents (call_agent)\n"
                                "Hand off to one of these agents with "
                                "call_agent(agent_name, message), and use its "
                                "reply:\n" + _reg
                            )
                    except Exception:
                        pass
                # Proactive UI rule for native MAF agents (Phase 2): they never
                # receive the Copilot addendum, so without this a MAF agent that
                # carries emit_generative_ui (e.g. email-assistant) has zero
                # system-prompt guidance to render UI. Static + marker-guarded =
                # byte-stable across turns (KV cache safe).
                try:
                    _prev2 = _do.get("instructions") or ""
                    if _emit_injected and _UI_DIRECTIVE_MARKER not in _prev2:
                        _prev2 = _prev2 + "\n\n" + _ui_first_directive(
                            compact=is_sub_agent,
                        )
                        _do["instructions"] = _prev2
                    # Output discipline (files under outputs/ + the on-demand
                    # design pointer) — the Copilot path gets this in the
                    # addendum; native MAF agents need the same at system-prompt
                    # level so they know where files go and to load the design
                    # system before a report. Marker-guarded for idempotency.
                    # A WhatsApp run writes no file and draws no web card.
                    if not _wa_withheld and not _has_output_discipline(_prev2):
                        _do["instructions"] = (
                            _prev2 + "\n\n"
                            + _build_output_discipline_block(
                                compact=is_sub_agent,
                                design_system=_design_injected,
                            )
                        )
                    _do["instructions"] = _with_attachment_rule(
                        _do.get("instructions") or "", _reader_injected,
                    )
                except Exception:  # noqa: BLE001
                    pass
                continue
        except Exception as _exc:  # noqa: BLE001
            _log.warning(
                "executor.tool_injection_failed",
                agent=_agent_label, shape="native_maf",
                error=str(_exc)[:200],
            )

        # ── MAF Agent (agent-framework) ─────────────────────────────────────
        # agent.tools is a list[FunctionTool | callable].  MAF accepts plain
        # async functions directly, so we can append without wrapping.
        try:
            if hasattr(agent, "tools") and isinstance(agent.tools, list):
                existing_names = {
                    getattr(getattr(t, "func", t), "__name__", None)
                    for t in agent.tools
                }
                for fn in _extra_tools:
                    if fn.__name__ not in existing_names:
                        agent.tools.append(fn)
                # Proactive UI rule for this MAF shape too (Phase 2) — append to
                # a writable string `instructions` attr if present; best-effort.
                try:
                    _instr = getattr(agent, "instructions", None)
                    if isinstance(_instr, str):
                        if _emit_injected and _UI_DIRECTIVE_MARKER not in _instr:
                            _instr = _instr + "\n\n" + _ui_first_directive(
                                compact=is_sub_agent,
                            )
                            agent.instructions = _instr
                        if not _wa_withheld and not _has_output_discipline(_instr):
                            agent.instructions = (
                                _instr + "\n\n"
                                + _build_output_discipline_block(
                                    compact=is_sub_agent,
                                    design_system=_design_injected,
                                )
                            )
                except Exception:  # noqa: BLE001
                    pass
                continue
        except Exception as _exc:  # noqa: BLE001
            _log.warning(
                "executor.tool_injection_failed",
                agent=_agent_label, shape="maf_tools",
                error=str(_exc)[:200],
            )

        # ── Legacy: _default_options.tools (older Copilot SDK wrapper) ──────
        # Same wrapping requirement as _tools above: raw callables are silently
        # skipped by the SDK.  Wrap each function with normalize_tools() so they
        # appear in the session's tool schema and the LLM can call them.
        try:
            opts = getattr(agent, "_default_options", None)
            if opts is not None:
                try:
                    from agent_framework._tools import \
                        normalize_tools as _norm_legacy  # noqa: PLC0415
                except ImportError:
                    _norm_legacy = None  # type: ignore[assignment]

                existing = list(getattr(opts, "tools", []) or [])
                existing_names = {
                    getattr(getattr(fn, "func", fn), "__name__", None) for fn in existing
                }
                for fn in _extra_tools:
                    if fn.__name__ not in existing_names:
                        wrapped = _norm_legacy([fn])[0] if _norm_legacy is not None else fn
                        existing.append(wrapped)
                opts.tools = existing
                injected = True
        except Exception as _exc:  # noqa: BLE001
            _log.warning(
                "executor.tool_injection_failed",
                agent=_agent_label, shape="legacy_copilot",
                error=str(_exc)[:200],
            )

        # No agent shape matched — the agent silently got NONE of the injected
        # platform tools (call_agent / web_search / write_artifact / …). Surface
        # it so the symptom isn't just a mysteriously "missing" tool at run time.
        if not injected:
            _log.warning(
                "executor.tool_injection_no_shape_matched",
                agent=_agent_label, agent_type=type(agent).__name__,
            )

    # CTX-1: a large tool result as a preview and a handle. It goes FIRST, so
    # its middleware is the outermost and the number check below still reads
    # the full result.
    # SBX-19: it goes BEFORE the spill, so it wraps the spill and reads the
    # preview. It keeps a result for `compute_run` and never a second copy.
    _apply_keep_for_compute(agents, agent_name)
    _apply_tool_result_spill(agents, agent_name)
    # H-236: the agent's OWN egress tools and MCP servers leave as well, and
    # a native MAF agent gets the call-time refusal, as a per-run view.
    if no_egress:
        _apply_no_egress(agents, agent_name, injected=_egress)
    else:
        _apply_chat_hold(agents, agent_name)
    # WS-STU F4b: a skill test puts its hold first on each agent of the run.
    _apply_dry_run(agents)
    # Task 2494: a member's steer reaches each native agent as a user turn.
    _apply_steer_turns(agents)
    # HRD-14b: a paste is framed as data, in a copy of each model request.
    # Right after the steer turns, so its middleware sees them. Off for the
    # org, or a run that the route cannot bind, it attaches nothing.
    _apply_paste_frame(agents, attach=paste_frame)
    # RESUME-1a: the MAF call id of each tool call, for the stored call of a
    # card. Off for the org, it attaches nothing.
    _apply_resume_call_id(agents)
    # HRD-13: the orchestrator's run gets the claim check, in shadow. It reads
    # the whole run at its end and changes nothing. Off for the org, nothing.
    _apply_claim_check(agents, agent_name)
    # HRD-5: after 3 empty or refused answers from one read tool in a turn, its
    # next call gets a stop line. Dark behind TOOL_TURN_BUDGET_ORGS. It runs
    # before the notice, so a stopped call starts no timer.
    _apply_tool_turn_budget(agents)
    # HRD-6: a slow tool call gets one line for the member. Its middleware
    # wraps each tool call, and the number check's recorder sits inside it.
    _apply_progress_notice(agents)
    # CRM-Q29: a native MAF agent that holds a CRM tool gets the number
    # check, as a per-run view, after the H-236 step.
    _apply_answer_check(agents, agent_name)
    return _run_tool_names


def _apply_resume_call_id(agents: list[Any]) -> None:
    """The MAF call id of each tool call on ONE run (RESUME-1a, task 3260).

    It replaces each native MAF ``Agent`` in *agents* with a per-run view that
    carries ``acb_skills.stored_call.CallIdProvider``, as
    :func:`_apply_progress_notice` does. Its middleware holds the call id of a
    call while the call runs, so the stored call of a card names it
    (``chat_run_continuity.md`` §4.12.4 item 1, C4). With ``CHAT_RESUME_ORGS``
    off for the run's org it changes nothing, so the run is as before. The org
    is the run binding's (:func:`_run_org`), never request input. A Copilot SDK
    agent and a plain object stay as they are, and their stored call has no
    call id. Fence: ``tests/unit/test_resume_stored_call.py``.
    """
    from acb_skills.stored_call import CallIdProvider, chat_resume_on

    if not chat_resume_on(_run_org()):
        return
    try:
        from agent_framework import Agent
    except ImportError as exc:  # pragma: no cover — ships with the platform
        _log.warning("executor.resume_call_id_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if isinstance(agent, Agent):
            agents[i] = agent_with_providers(agent, [CallIdProvider()])


def _apply_claim_check(agents: list[Any], agent_name: str | None) -> None:
    """The claim check of the orchestrator's answer on ONE run (HRD-13, task 2992).

    It replaces the native MAF ``Agent`` of the agent ``orchestrator`` with a
    per-run view that carries ONE ``acb_skills.answer_claims.ClaimCheckProvider``,
    as :func:`_apply_progress_notice` does. The provider reads the whole run in
    ``after_run`` and logs one line. It never changes the answer (D-HRD-6).
    With ``CHAT_CLAIM_CHECK_ORGS`` off for the run's org it changes nothing.
    The org is the run binding's (:func:`_run_org`), never request input. The
    names of the agent's delegation tools go to the provider, because the
    calls of a sub-run stay inside the delegation. Any other agent, a Copilot
    SDK agent and a plain object stay as they are. Fence:
    ``tests/unit/test_answer_claims.py``.
    """
    from acb_skills.answer_claims import CHECKED_AGENT, claim_check_on

    if str(agent_name or "") != CHECKED_AGENT or not agents:
        return
    if not claim_check_on(_run_org()):
        return
    try:
        from acb_skills.answer_claims import ClaimCheckProvider
        from acb_skills.egress import is_delegation_tool
        from agent_framework import Agent
    except ImportError as exc:  # pragma: no cover — ships with the platform
        _log.warning("executor.claim_check_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    agent = agents[0]
    if not isinstance(agent, Agent):
        return
    delegations = frozenset(
        _tool_name(t) for _label, pool in _own_tool_pools(agent) for t in pool
        if _delegates_to(t) or is_delegation_tool(t)
    )
    # P1-a: the answer of a pack tool outside its run is a not-acted head.
    # It lives in this module, and acb_skills never imports the orchestrator.
    agents[0] = agent_with_providers(agent, [ClaimCheckProvider(
        CHECKED_AGENT, delegations, not_acted=(PACK_CALL_REFUSED,))])


def _apply_progress_notice(agents: list[Any]) -> None:
    """The line of a slow tool call on ONE run (HRD-6, task 2985).

    It replaces each native MAF ``Agent`` in *agents* with a per-run view that
    carries ``acb_skills.progress_notice.ProgressNoticeProvider``, as
    :func:`_apply_answer_check` does. With ``CHAT_PROGRESS_NOTICE_ORGS`` off for
    the run's org it changes nothing, so the run is as before. The org is the
    run binding's (:func:`_run_org`), never request input. A Copilot SDK agent
    and a plain object stay as they are. Fence:
    ``tests/unit/test_progress_notice.py``.
    """
    from acb_skills.progress_notice import ProgressNoticeProvider, progress_notice_on

    if not progress_notice_on(_run_org()):
        return
    try:
        from agent_framework import Agent
    except ImportError as exc:  # pragma: no cover — ships with the platform
        _log.warning("executor.progress_notice_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if isinstance(agent, Agent):
            agents[i] = agent_with_providers(agent, [ProgressNoticeProvider()])


def _apply_tool_turn_budget(agents: list[Any]) -> None:
    """The per-turn budget of each read tool on ONE run (HRD-5, task 2984).

    It replaces each native MAF ``Agent`` in *agents* with a per-run view that
    carries ``orchestrator.tool_turn_budget.ToolTurnBudgetProvider``, as
    :func:`_apply_progress_notice` does. With ``TOOL_TURN_BUDGET_ORGS`` off for
    the run's org it changes nothing. The org is the run binding's
    (:func:`_run_org`), never request input. A Copilot SDK agent and a plain
    object stay as they are. Fence: ``tests/unit/test_tool_turn_budget.py``.
    """
    from orchestrator.tool_turn_budget import ToolTurnBudgetProvider, tool_turn_budget_on

    if not tool_turn_budget_on(_run_org()):
        return
    try:
        from agent_framework import Agent
    except ImportError as exc:  # pragma: no cover — ships with the platform
        _log.warning("executor.tool_turn_budget_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if isinstance(agent, Agent):
            agents[i] = agent_with_providers(agent, [ToolTurnBudgetProvider()])


def _apply_steer_turns(agents: list[Any]) -> None:
    """A member's mid-run steer is a USER turn of the next model call (task 2494).

    It replaces each native MAF ``Agent`` in *agents* with a per-run view that
    carries ``orchestrator.steer.SteerTurnsProvider``, as
    :func:`_apply_answer_check` does. The provider puts a new ``SteerTurns``
    chat middleware on each ``agent.run``. That middleware takes words only
    from the steer buffer of the run's thread, and only the steer applier of
    ``stream_relay`` fills that buffer. A tool result never feeds it. A
    Copilot SDK agent and a plain object stay as they are. Their steer waits,
    and the follow-up turn answers it when the run ends
    (``steer.return_unconsumed``).
    """
    try:
        from agent_framework import Agent

        from orchestrator.steer import SteerTurnsProvider, _run_thread_id
    except ImportError as exc:  # pragma: no cover — both ship with the platform
        _log.warning("executor.steer_turns_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    if not _run_thread_id():
        # No relay thread: no steer applier, so no steer can come. The agent
        # stays as it is (a batch run, a test, a sandbox build).
        return
    for i, agent in enumerate(agents):
        if isinstance(agent, Agent):
            agents[i] = agent_with_providers(agent, [SteerTurnsProvider()])


def _apply_paste_frame(agents: list[Any], *, attach: bool) -> None:
    """A paste is framed as data on ONE run (HRD-14b, task 3667).

    Spec: ``chat_failure_modes.md`` §4.7b, "The seam". It replaces each
    native MAF ``Agent`` in *agents* with a per-run view that carries
    ``orchestrator.paste_turns.PasteFrameProvider``, as
    :func:`_apply_progress_notice` does. It runs right after
    :func:`_apply_steer_turns`, so the middleware sees each request after the
    steer turns. With ``CHAT_PASTE_FRAME_ORGS`` off for the run's org it
    attaches nothing, so the provider list, the first model request, the
    stored session and each golden stay byte-identical. The org is the run
    binding's (:func:`_run_org`), never request input. *attach* is False for
    a delegated run, a sub-agent and a batch run: they get no middleware. A
    Copilot SDK agent and a plain object stay as they are. Fence:
    ``tests/unit/test_paste_frame_seam.py``.
    """
    if not attach:
        return
    from acb_skills.paste_frame import paste_frame_on

    if not paste_frame_on(_run_org()):
        return
    try:
        from agent_framework import Agent

        from orchestrator.paste_turns import PasteFrameProvider
    except ImportError as exc:  # pragma: no cover — both ship with the platform
        _log.warning("executor.paste_frame_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if isinstance(agent, Agent):
            agents[i] = agent_with_providers(agent, [PasteFrameProvider()])


def _apply_answer_check(agents: list[Any], agent_name: str | None) -> None:
    """The number check of a CRM answer on ONE run (WS-53 CRM-Q29).

    It replaces each native MAF ``Agent`` in *agents* that holds a CRM tool
    with a per-run view that carries ``acb_skills.answer_check``'s provider,
    so the caller reads ``agents`` again, as every call site does. It is the
    one seam of the check (``crm_platform.md`` §18.11.2 item 9). With
    ``CRM_ANSWER_CHECK`` ``off`` it changes nothing. A Copilot SDK agent and a
    plain object are left as they are. The mode is read BEFORE the check is
    imported, so a box with no ``acb_skills`` runs as before while the flag
    is ``off``, and skips the check with a warning while it is on.

    WS-FIND FIND-7 (``find_engine.md`` §5.5): the same provider carries the
    "not found" check to an agent that holds ``search_company``, while
    ``FIND_CLAIM_CHECK`` is not ``off``. The flag is global, but the check
    acts only for an org of ``FIND_ORGS``, because pass 2 exists only there.
    The org is the run binding's (:func:`_run_org`), and the rule is
    ``gateway.retrieval.find.find_allowed``, the one reader of ``FIND_ORGS``
    that ``agents._find_enabled`` wraps for the tool list. No org, or a fault,
    reads as off. For an org off the list, and with the flag ``off``, this
    seam does what it did before.
    """
    from acb_common.settings import crm_answer_check_mode, find_claim_check_mode
    from acb_skills.subagent_contract import INTERNAL_AGENTS

    mode = crm_answer_check_mode()
    claim_mode = find_claim_check_mode()
    if claim_mode != "off":
        try:
            from gateway.retrieval.find import find_allowed

            org = _run_org()
            find_on = bool(org) and find_allowed(org)
        except Exception:  # a fault never turns the check on
            find_on = False
        if not find_on:
            claim_mode = "off"
    if mode == "off" and claim_mode == "off":
        return
    if str(agent_name or "") in INTERNAL_AGENTS:
        # WS-67 OA-9b hardening item 5: the check can retry, which adds a
        # model call to the fixed number of a spawn child.
        return
    try:
        from acb_skills.answer_check import (
            AnswerCheckProvider,
            holds_crm_tool,
            holds_search_tool,
        )
        from agent_framework import Agent
    except ImportError as exc:
        _log.warning("executor.answer_check_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if not isinstance(agent, Agent):
            continue
        held = [_tool_name(t) for _label, pool in _own_tool_pools(agent) for t in pool]
        numbers = mode if mode != "off" and holds_crm_tool(held) else "off"
        claims = claim_mode if claim_mode != "off" and holds_search_tool(held) else "off"
        if numbers == "off" and claims == "off":
            continue
        agents[i] = agent_with_providers(agent, [AnswerCheckProvider(numbers, claim_mode=claims)])
        extra = {"claim_mode": claims} if claims != "off" else {}
        _log.info("executor.answer_check_on", agent=agent_name, mode=numbers, **extra)


def _apply_keep_for_compute(agents: list[Any], agent_name: str | None) -> None:
    """The ``compute_run`` input marker on ONE run (SBX-19).

    It replaces each native MAF ``Agent`` that holds ``compute_run`` with a
    per-run view that carries ``KeepForComputeProvider``, as
    :func:`_apply_tool_result_spill` does. Nothing is attached for an org with
    the compute target off, and a run with no org attaches nothing. Call it
    BEFORE the spill step, so its middleware wraps the spill's.
    """
    org = _run_org_or_none()
    if not org:
        return
    try:
        from acb_skills.compute_run import compute_enabled_for
        from acb_skills.tool_result_spill import KeepForComputeProvider
        from agent_framework import Agent
    except ImportError as exc:
        _log.warning("executor.keep_for_compute_unavailable", error=str(exc)[:200])
        return
    if not compute_enabled_for(org):
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if not isinstance(agent, Agent):
            continue
        held = {_tool_name(t) for _label, pool in _own_tool_pools(agent) for t in pool}
        if "compute_run" not in held:
            continue
        agents[i] = agent_with_providers(agent, [KeepForComputeProvider()])
        _log.info("executor.keep_for_compute_on", agent=agent_name)


def _apply_tool_result_spill(agents: list[Any], agent_name: str | None) -> None:
    """The preview-and-handle view of a large tool result on ONE run (CTX-1).

    It replaces each native MAF ``Agent`` in *agents* with a per-run view that
    carries ``acb_skills.tool_result_spill``'s provider, as
    :func:`_apply_answer_check` does. With ``TOOL_RESULT_SPILL`` ``off`` it
    changes nothing. ``shadow`` attaches the logging middleware to each native
    agent. ``on`` attaches it only to an agent that holds ``read_tool_result``,
    because a handle that the agent cannot read is a dead end. Call it BEFORE
    the egress and number-check steps, so the middleware is outermost.
    """
    from acb_common.settings import (
        tool_result_preview_chars,
        tool_result_spill_chars,
        tool_result_spill_mode,
    )

    mode = tool_result_spill_mode()
    if mode == "off":
        return
    try:
        from acb_skills.tool_result_spill import READ_TOOL, ToolResultSpillProvider
        from agent_framework import Agent
    except ImportError as exc:
        _log.warning("executor.tool_result_spill_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if not isinstance(agent, Agent):
            continue
        if mode == "on":
            held = {_tool_name(t) for _label, pool in _own_tool_pools(agent) for t in pool}
            if READ_TOOL not in held:
                continue
        agents[i] = agent_with_providers(agent, [ToolResultSpillProvider(
            mode, tool_result_spill_chars(), tool_result_preview_chars())])
        _log.info("executor.tool_result_spill_on", agent=agent_name, mode=mode)


def _apply_no_egress(
    agents: list[Any], agent_name: str | None, *, injected: frozenset[str] = frozenset(),
) -> None:
    """The H-236 control on every agent of ONE run.

    It replaces each native MAF agent in *agents* with its guarded view, so
    the caller must read ``agents`` again after injection, as every call
    site does. *injected* is the platform tools that the run did not get.
    It fails closed. An agent whose tools it cannot read raises
    :class:`NoEgressRefused`, and so does a filter that fails, so the run
    ends before the agent can call an egress tool, and nothing retries it.
    """
    removed: set[str] = set(injected)
    chat_hold = not _covered_hold(agent_name)
    owner = str(agent_name or "")
    for i, agent in enumerate(agents):
        if not _inspectable(agent):
            _log.error(
                "executor.no_egress_refused", agent=agent_name,
                agent_type=type(agent).__name__,
            )
            raise NoEgressRefused(
                f"Agent {agent_name!r} cannot run here: it was called during a "
                "sandboxed turn, and its tools cannot be checked for a send."
            )
        try:
            removed.update(_withhold_egress_from_agent(agent, chat_hold=chat_hold, owner=owner))
            agents[i] = _with_egress_guard(agent, chat_hold=chat_hold, owner=owner)
        except Exception as exc:
            _log.error(
                "executor.no_egress_failed", agent=agent_name, error=str(exc)[:200],
            )
            raise NoEgressRefused(
                f"Agent {agent_name!r} cannot run here: its tools could not be "
                "checked for a send."
            ) from exc
    _log.info(
        "executor.egress_tools_withheld", agent=agent_name, tools=sorted(removed),
    )


def _apply_chat_hold(agents: list[Any], agent_name: str | None) -> None:
    """D169 on a run that binds ``no_egress=False`` (RX-1).

    A top-level run of an owner of ``egress.HELD_WRITE_OWNERS`` holds its own
    class C tools, and H-236 does not reach it, because the mark lives on the
    session. ``egress.ChatHoldProvider`` holds those writes back while the chat
    is marked, and only those writes. Each egress tool of the run keeps its card.
    Off for an org that the switch does not name, and for an agent that holds no
    such write, so every other run keeps its object. A Copilot agent has its
    own guard, and a plain object holds no middleware, so each stays as it is.
    """
    from acb_skills.egress import ChatHoldProvider, held_card_writes_on

    if not held_card_writes_on():
        return
    from agent_framework import Agent
    from orchestrator._native_run_context import agent_with_providers

    owner = str(agent_name or "")
    for i, agent in enumerate(agents):
        if not isinstance(agent, Agent) or hasattr(agent, "_permission_handler"):
            continue
        if any(_is_held_off(tool, owner) for _label, pool in _own_tool_pools(agent)
               for tool in pool):
            agents[i] = agent_with_providers(agent, [ChatHoldProvider(owner=owner)])


def _apply_dry_run(agents: list[Any]) -> None:
    """The dry-run door of a skill test on ONE run (WS-STU F4b).

    With no dry run on this frame it returns at once, so each other run is as
    before. Else ``skill_dry_run.attach`` puts ``HoldAndRecord`` and
    ``CapGuard`` FIRST in the ``middleware`` list of a per-run view of each
    agent, and arms the record. MAF runs that list before each provider
    middleware and before the MCP gate, so the hold sees each call first. A
    Copilot agent or a plain object raises ``DryRunRefused``, so a skill test
    never runs with no hold. Fence: ``tests/unit/test_skill_dry_run.py``.
    """
    from acb_skills.skill_dry_run import attach, current_dry_run

    record = current_dry_run()
    if record is None:
        return
    from agent_framework import Agent

    attach(agents, record, native=Agent,
           tools_of=lambda agent: [t for _label, pool in _own_tool_pools(agent) for t in pool])


def _note_dry_run_loaded(skill_slugs: Sequence[str]) -> None:
    """In a skill test, note whether the forced skill reached the run
    (WS-STU F4b). With no dry run on this frame it does nothing."""
    from acb_skills.skill_dry_run import current_dry_run

    if (dry := current_dry_run()) is not None:
        dry.loaded = dry.slug in skill_slugs


# ── Tool packs: the guarded builder (WS-67 OA-7b, D117) ─────────────────────
# ``one_assistant_context.md`` §4.2.3. A pack tool runs as its owner would run
# it in a sub-run of this chat. Three parts make that hold:
#
# 1. The held answer, at run start (:func:`_pack_held`): the answer that a
#    delegation to the owner binds now, from the server-side run context. A
#    call after the chat read mail in this turn runs held too (owner choice,
#    STRICT, OA-7b fix round 1).
# 2. The scope of the owner, per call (``tool_packs.owner_scope``), as the
#    OUTERMOST wrapper, with the permission gate inside it.
# 3. The held set, at load: a held pack drops each tool that :func:`_is_egress`
#    or :func:`_is_held_off` names (RX-1, D169). ⚠️ This drop is the ONLY
#    held-set control of a pack run. A pack run gets no
#    ``EgressGuardProvider``, so no ``WithholdEgressTools`` and no
#    ``RefuseEgressTools`` stand behind it (audit of 2026-10-26, §4.5).
#
# ONE seam: the builder calls ``_is_egress``, ``_run_no_egress``,
# ``_gate_injected_tool`` and ``artifact_context_scope`` (through
# ``owner_scope``). It copies none of their bodies. No live caller: OA-7c
# wires it into runs. Fence: ``tests/unit/test_pack_guards.py``.

#: The attribute that names the owner of a pack tool. ``_inject_mcp_servers``
#: reads it, so a run that holds a pack of an agent of
#: :data:`UNTRUSTED_INPUT_AGENTS` gets no registry MCP server.
PACK_OWNER_ATTR = "__cc_pack_owner__"

#: The attribute of an agent that names the agents its run may not hand off
#: to, because the run holds every pack of each (OA-7c fix round 1). The
#: "Delegatable agents" block of :func:`_inject_agent_tools` leaves them out.
PACK_REFUSED_ATTR = "_cc_pack_refused_delegates"

#: The answer of a pack tool that is called outside the run that built it,
#: or on a frame with no run. The held answer belongs to one run, so the tool
#: never acts in another.
PACK_CALL_REFUSED = (
    "{tool} did not run. It acts only in the run that loaded it. "
    "Answer with what you have."
)

#: The module of each pack owner, imported once for this process.
_PACK_OWNER_MODULES: dict[str, Any] = {}
#: TOF-1e. ONE lock for the two cold paths of the loader below. The catalog
#: warm-up (``gateway.studio.chat_tool_actions.warm``) runs on a thread, so
#: it and a run on the event loop must not import one owner twice.
_PACK_LOAD_LOCK = threading.RLock()

#: How long the loader keeps a failed import of an owner, in seconds. A build
#: in that time gets no module and imports nothing. After it, the next build
#: tries again, so a fixed owner loads with no restart.
PACK_OWNER_RETRY_S = 60.0

#: The clock time of the last failed import of each owner.
_PACK_OWNER_FAILED: dict[str, float] = {}


def _pack_clock() -> float:
    """The clock of the failure window. A seam, so a test sets the time."""
    import time

    return time.monotonic()


@dataclasses.dataclass(frozen=True)
class PackBuild:
    """What :func:`_pack_tools` built for one pack and one run."""

    slug: str
    owner: str
    #: The held answer of this run. OA-7c adds the hold note to the rules.
    held: bool
    #: The guarded tools, in the order of the family.
    tools: tuple[Any, ...]
    #: The names that the held drop took out (the held set).
    dropped: tuple[str, ...] = ()
    #: The names that the run already holds, so the pack left them out.
    skipped: tuple[str, ...] = ()


def _pack_owner_module(owner: str) -> Any | None:
    """The ``agents.py`` module of the in-repo agent *owner*, or None.

    THE one loader of a pack owner, for the run builder and for the skills
    catalog (``gateway/routes/integrations_skills.py``). It imports the
    module once for this process and keeps it. It keeps a failure for
    :data:`PACK_OWNER_RETRY_S`, so a broken owner costs one import in that
    window, never one for each build or each catalog read. The path comes
    from the two STATIC lists only, never from a dynamic registration,
    because a pack owner is an in-repo agent (:func:`_import_pack_owner`).
    """
    if owner in _PACK_OWNER_MODULES:
        return _PACK_OWNER_MODULES[owner]
    with _PACK_LOAD_LOCK:
        if owner in _PACK_OWNER_MODULES:
            return _PACK_OWNER_MODULES[owner]
        failed_at = _PACK_OWNER_FAILED.get(owner)
        if failed_at is not None and _pack_clock() - failed_at < PACK_OWNER_RETRY_S:
            return None
        module = _import_pack_owner(owner)
        if module is None:
            _PACK_OWNER_FAILED[owner] = _pack_clock()
        else:
            _PACK_OWNER_MODULES[owner] = module
            _PACK_OWNER_FAILED.pop(owner, None)
        return module


def _import_pack_owner(owner: str) -> Any | None:
    """Import the module of *owner* from its registry path, or None.

    The path comes from ``_PACK_OWNER_AGENTS`` first, then from
    ``_AGENT_REGISTRY``. An owner in the first list never runs as an agent
    (STU-G1), and this loader is the only code that reads that list.

    A module that raises at import gives None and a warning. ``SystemExit``
    counts as such a failure, so an owner that calls ``sys.exit`` cannot stop
    the process. ``KeyboardInterrupt`` and the other ``BaseException`` kinds
    still propagate.
    """
    import importlib.util
    import sys
    from pathlib import Path

    try:
        from gateway.routes.agent import _AGENT_REGISTRY, _PACK_OWNER_AGENTS
    except Exception as exc:  # no gateway in this process: no owner
        _log.warning("executor.pack_owner_unavailable", owner=owner,
                     error_type=type(exc).__name__)
        return None
    entry = next((a for a in (*_PACK_OWNER_AGENTS, *_AGENT_REGISTRY)
                  if a.get("name") == owner), None)
    local = str((entry or {}).get("local_path") or "")
    path = Path(local) / "agents.py"
    if not local or not path.is_file():
        _log.warning("executor.pack_owner_unavailable", owner=owner, error_type="no_path")
        return None
    name = "_tool_pack_owner_" + owner.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as exc:
        sys.modules.pop(name, None)
        _log.warning("executor.pack_owner_import_failed", owner=owner,
                     error_type=type(exc).__name__)
        return None
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


async def _pack_held(owner: str, run_ctx: Any) -> bool:
    """The held answer of a pack of *owner* in the run of *run_ctx*.

    It is the answer that a delegation to the owner binds now, and the run
    reads it when it builds the pack:

    * :func:`_run_no_egress` for the owner, with the run as the parent. That
      is the streaming seam of a specialist tool (``executor.py``,
      ``_run_sub_agent_streaming``).
    * The mark of the chat session in Redis (``session_mark_state``), as
      ``agent_tools.call_agent`` reads it. A pack FAILS CLOSED where a
      delegation does not: a Redis fault reads as held (fix round 1).

    It fails closed. An empty run context is held, because a pack acts only
    in a bound run. A run with no tenant or no session is held too, because
    no mark can be read or written for it. The org and the session come from
    the server-bound run, never from a request field or a tool argument (R5).
    """
    if not run_ctx:
        return True
    try:
        from acb_skills.egress import _session_of
    except ImportError:  # fail closed: no reader means held
        return True
    if _session_of(run_ctx) is None:
        return True
    if _run_no_egress(owner, run_ctx):
        return True
    return await session_mark_state(run_ctx, fail_closed=True) != MARK_CLEAR


async def _pack_tools(
    slug: str, run_ctx: Any, *, holds: Iterable[str] = (), module: Any = None,
) -> PackBuild | None:
    """The guarded tools of pack *slug* for the run of *run_ctx* (WS-67 OA-7b).

    *run_ctx* is the artifact context of the run that loads the pack, read
    on its own frame at run start. *holds* names the tools that the run
    already holds: the pack leaves each of them out, so a pack tool never
    shadows a tool of the run. ``run_tools`` of *run_ctx* counts too.
    *module* is for a test that patched the owner module. A run passes
    none, and the builder loads the owner once for the process
    (:func:`_pack_owner_module`).

    The tools are the owner's own function objects (``tool_packs.owner_tools``),
    each in :func:`_pack_call`. A held pack drops each tool that
    :func:`_is_egress` or :func:`_is_held_off` names, and that drop is the only
    held-set control of a pack run. A covered org builds no Projects pack
    (:func:`_drop_pack_covered`), so each held build is a held chat. None for a
    slug that is no pack. An owner that does not load,
    and a run context with no run key, give a held build with no tool.

    OA-7c (condition 3): the run check of :func:`_pack_call` keys on
    ``tool_packs.PACK_RUN_KEY``, a value that the executor mints for each run
    (:func:`_apply_tool_packs`). It never keys on ``run_id``, which a client
    can set (``gateway/routes/agent.py``, ``req.run_id``).
    """
    import inspect

    from acb_skills import tool_packs

    found = tool_packs.pack(slug)
    if found is None:
        return None
    owner = found.owner
    run_key = str((run_ctx or {}).get(tool_packs.PACK_RUN_KEY) or "")
    if not run_key:  # the run check needs a key, so no key builds nothing
        _log.warning("executor.pack_refused", pack=slug, reason="no_run_key")
        return PackBuild(slug=slug, owner=owner, held=True, tools=())
    source = module if module is not None else _pack_owner_module(owner)
    if source is None:
        return PackBuild(slug=slug, owner=owner, held=True, tools=())
    held = await _pack_held(owner, run_ctx)
    taken = {str(n) for n in holds} | {str(n) for n in (run_ctx or {}).get("run_tools") or ()}
    tools: list[Any] = []
    dropped: list[str] = []
    skipped: list[str] = []
    for fn in _owner_pack_tools(slug, owner, source):
        name = _tool_name(fn)
        # OA-8b: a tool is a coroutine function or a MAF ``FunctionTool`` whose
        # ``func`` is one (the Projects ``_StrictTool``). Any other shape skips.
        if name in taken or not inspect.iscoroutinefunction(_pack_func(fn)):
            skipped.append(name)
        elif held and (_is_egress(fn) or _is_held_off(fn) or _is_held_off_pack(fn, slug)):
            dropped.append(name)
        else:
            tools.append(_pack_call(fn, owner, held, run_key, slug))
    prefilter = _PACK_OWNER_FILTERS.get(owner)
    if prefilter is not None and tools:
        tools, dropped = await prefilter(tools, dropped, held=held)
    _log.info("executor.pack_built", pack=slug, owner=owner, held=held,
              tools=len(tools), dropped=sorted(dropped), skipped=sorted(skipped))
    return PackBuild(slug=slug, owner=owner, held=held, tools=tuple(tools),
                     dropped=tuple(dropped), skipped=tuple(skipped))


async def _drop_pack_mirror_writes(
    tools: list[Any], dropped: list[str], *, held: bool = False,
) -> tuple[list[Any], list[str]]:
    """Take the CRM record writes from a pack build in Mirror mode (OA-8a),
    and ``crm_create_pipeline`` from the build of an org that
    ``CRM_PIPELINES_ORGS`` leaves out (CRM-M3a, :func:`_crm_pipelines_open`).

    The drop is part of the builder, so each layer gets it: layer (a) runs
    :func:`_drop_mirror_record_writes` later in the executor, but a pack that
    ``load_skill_tools`` (OA-7e) or a turn-kind item (OA-7f) loads in the
    middle of a turn builds after that drop. It reads ``GET /crm/mode`` as
    the member (``acb_skills.crm_tools.crm_mode``) and reuses
    :data:`MIRROR_RECORD_WRITES`. RX-1: a held build reads it too, because the
    writes now stay in a held pack, and in Mirror mode a write would reach
    Zoho. With the org off (``egress.held_card_writes_on``), a held build reads
    nothing, as before. A failed read keeps the tools, and the 409 relay of the
    write tools is the fallback, as in :func:`_drop_mirror_record_writes`.
    """
    from acb_skills.egress import held_card_writes_on

    # CRM-M3a: an org off `CRM_PIPELINES_ORGS` builds no `crm_create_pipeline`.
    if any(_tool_name(t) in PIPELINE_WRITES for t in tools) and not _crm_pipelines_open():
        gone = [_tool_name(t) for t in tools if _tool_name(t) in PIPELINE_WRITES]
        tools = [t for t in tools if _tool_name(t) not in PIPELINE_WRITES]
        dropped = [*dropped, *gone]
        _log.info("executor.crm_pipeline_writes_dropped", tools=gone, source="pack")
    if ((held and not held_card_writes_on())
            or not any(_tool_name(t) in MIRROR_RECORD_WRITES for t in tools)):
        return tools, dropped
    try:
        from acb_skills.crm_tools import crm_mode
        mode = await crm_mode()
    except Exception as exc:  # the 409 relay of the write tools is the fallback
        _log.info("executor.crm_mode_unread", error=str(exc)[:200])
        return tools, dropped
    if mode != "mirror":
        return tools, dropped
    kept = [t for t in tools if _tool_name(t) not in MIRROR_RECORD_WRITES]
    gone = [_tool_name(t) for t in tools if _tool_name(t) in MIRROR_RECORD_WRITES]
    _log.info("executor.crm_mirror_writes_dropped", tools=sorted(gone), source="pack")
    return kept, [*dropped, *gone]


async def _drop_pack_covered(
    tools: list[Any], dropped: list[str], *, held: bool = False,
) -> tuple[list[Any], list[str]]:
    """Give a covered organization no Projects pack (WS-67 OA-8b, audit
    narrowing 6).

    For a covered organization (H-236, ``_run_covered``), the Projects
    assistant's own run is the one path that is pinned to a tool list
    (``COVERED_PROJECTS_TOOLS`` in ``tests/unit/test_delegation_no_egress.py``)
    and fenced by the sandbox broker. The pack would give the one assistant
    those tools outside that pin. So a covered run builds an empty pack, and
    the specialist keeps the work. The check reads the run binding, never a
    request field (R5), and ``_run_covered`` fails closed. It runs for the
    builds of every layer, as the Mirror filter does."""
    if not tools or not _run_covered("projects-assistant"):
        return tools, dropped
    _log.info("executor.projects_pack_covered", tools=len(tools), source="pack")
    return [], dropped


def _studio_open() -> bool:
    """True when ``STUDIO_ORGS`` holds the org of this run (STU-G1).

    The reader is ``gateway.routes.studio.flags.studio_allowed``, the one that
    the catalog route uses. The org is the run's own (:func:`_run_org_or_none`),
    never an argument (R5). It fails closed: no org, no gateway or an error
    reads as closed. The pack filter and the loader plan both ask it, so the
    loader never offers a pack that builds no tool.
    """
    try:
        from gateway.routes.studio.flags import studio_allowed
        return bool(studio_allowed(_run_org_or_none()))
    except Exception as exc:  # fail closed
        _log.info("executor.studio_flag_unread", error=str(exc)[:200])
        return False


def _chat_tools_open() -> bool:
    """True when ``STUDIO_CHAT_TOOLS_ORGS`` holds the org of this run (TOF-1b).

    The reader is ``gateway.routes.studio.flags.chat_tools_allowed``, the one
    that the catalog route uses. The org is the run's own
    (:func:`_run_org_or_none`), never an argument (R5). It fails closed: no
    org, no gateway or an error reads as closed. :func:`_turn_with_skills`
    asks it once a turn at most. It asks for a turn with a skill to build, so
    with the flag off a ``tool:`` action fails with ``chat_tools_off`` and
    brings no pack. TOF-1c: when no name matches, the description pass asks
    it first, and the build then uses that answer.
    """
    try:
        from gateway.routes.studio.flags import chat_tools_allowed
        return bool(chat_tools_allowed(_run_org_or_none()))
    except Exception as exc:  # fail closed
        _log.info("executor.studio_chat_tools_flag_unread", error=str(exc)[:200])
        return False


async def _studio_member_open() -> bool:
    """True when the org is open (:func:`_studio_open`) AND the member of the
    run holds ``feature:studio`` (STU-J1-1).

    The gateway refuses a Studio route to a guest and to a member with a deny
    override, but ``studio_draft``, ``studio_outline``, ``studio_describe``
    and ``studio_interview`` call no route. So the pack needs the same check.
    The member is the verified member of the run, through
    ``in_app_path.member_features`` (one cached ``resolve_access`` read). It
    fails closed: no member, an inactive member or a failed read is closed.
    A closed org reads no member. Fence: ``tests/unit/test_tool_packs_registry.py``.
    """
    if not _studio_open():
        return False
    try:
        from orchestrator import in_app_path

        features = await in_app_path.member_features()
    except Exception as exc:  # fail closed
        _log.info("executor.studio_member_unread", error=str(exc)[:200])
        return False
    return features is not None and "studio" in features


async def _drop_pack_unless_studio(
    tools: list[Any], dropped: list[str], *, held: bool = False,
) -> tuple[list[Any], list[str]]:
    """Build an empty ``studio`` pack for an org that ``STUDIO_ORGS`` lacks,
    and for a member without ``feature:studio`` (STU-G1 and J1-1,
    :func:`_studio_member_open`). For an open member, the second filter
    :func:`_drop_studio_writes_unless_requests` runs next (STU-B2c-2b), and
    then the third, :func:`_drop_studio_repair_unless_open` (TOF-3a F12c)."""
    if await _studio_member_open():
        tools, dropped = await _drop_studio_writes_unless_requests(tools, dropped, held=held)
        return _drop_studio_repair_unless_open(tools, dropped)
    return [], [*dropped, *(_tool_name(t) for t in tools)]


#: The pack of the two repair tools (TOF-3a F12c).
REPAIR_PACK = "studio-repair"


def _skill_repair_tools_open() -> bool:
    """True when the run's org is on ``STUDIO_ORGS``,
    ``STUDIO_SKILL_TESTS_ORGS`` and ``STUDIO_SKILL_REPAIR_ORGS``: the switches
    of the repair route (F12.md check 1). The org is the run's own, never an
    argument (R5). It fails closed."""
    try:
        from gateway.routes.studio.flags import skill_tests_allowed

        return _skill_repair_open() and bool(skill_tests_allowed(_run_org_or_none()))
    except Exception:  # fail closed
        return False


def _without_closed_repair(allow: frozenset[str]) -> frozenset[str]:
    """*allow* less :data:`REPAIR_PACK` while the org of the run is off the
    switches of the repair (TOF-3a F12c). Then ``tool_packs.choose`` and the
    loader plan never offer it, also where the owner pack list names it. An
    *allow* with no repair pack reads no flag."""
    if REPAIR_PACK in allow and not _skill_repair_tools_open():
        return allow - {REPAIR_PACK}
    return allow


def _drop_studio_repair_unless_open(
    tools: list[Any], dropped: list[str],
) -> tuple[list[Any], list[str]]:
    """Take the two repair tools out of a ``studio-repair`` pack while the org
    is off the switches of the repair (TOF-3a F12c,
    :func:`_skill_repair_tools_open`). A pack with no repair tool reads no
    flag. The names are ``acb_skills.studio_tools.STUDIO_REPAIR_NAMES``, the
    one list. Fence: ``tests/unit/test_skill_repair.py``."""
    from acb_skills.studio_tools import STUDIO_REPAIR_NAMES

    if not any(_tool_name(t) in STUDIO_REPAIR_NAMES for t in tools):
        return tools, dropped
    if _skill_repair_tools_open():
        return tools, dropped
    kept = [t for t in tools if _tool_name(t) not in STUDIO_REPAIR_NAMES]
    gone = [_tool_name(t) for t in tools if _tool_name(t) in STUDIO_REPAIR_NAMES]
    return kept, [*dropped, *gone]


def _studio_requests_open() -> bool:
    """True when ``APP_REQUESTS_ORGS`` holds the org of this run AND agent
    access is on (STU-B2c-2b, fix round 1).

    ``gateway.app_requests.requests_allowed`` is the reader that the door
    reads before it opens a request. ``acb_auth.agent_oauth.enabled`` is the
    second flag of the member's list, Integrations, AI agents, Waiting for
    you (``routes.agent_requests._require_access`` needs both). Without it,
    the member cannot see a request to approve it, so a write tool would only
    ask in vain. The org is the run's own (:func:`_run_org_or_none`), never
    an argument (R5). It fails closed: no org, no gateway or an error reads
    as closed.
    """
    try:
        from acb_auth import agent_oauth
        from gateway.app_requests import requests_allowed
        return bool(agent_oauth.enabled()) and bool(requests_allowed(_run_org_or_none()))
    except Exception as exc:  # fail closed
        _log.info("executor.studio_requests_unread", error=str(exc)[:200])
        return False


async def _drop_studio_writes_unless_requests(
    tools: list[Any], dropped: list[str], *, held: bool = False,
) -> tuple[list[Any], list[str]]:
    """Take the three record write tools out of a ``studio`` pack while the
    org has no approval store (STU-B2c-2b, :func:`_studio_requests_open`).

    In a dark org, the door answers each write from chat 403
    ``approval_required``, so a write tool there only fails. The read tools
    stay. A pack with no write tool reads no flag. The names are
    ``acb_skills.studio_tools.STUDIO_RECORD_WRITES``, the one list.
    Fence: ``tests/unit/test_tool_packs_registry.py``.
    """
    from acb_skills.studio_tools import STUDIO_RECORD_WRITES

    if not any(_tool_name(t) in STUDIO_RECORD_WRITES for t in tools):
        return tools, dropped
    if _studio_requests_open():
        return tools, dropped
    kept = [t for t in tools if _tool_name(t) not in STUDIO_RECORD_WRITES]
    gone = [_tool_name(t) for t in tools if _tool_name(t) in STUDIO_RECORD_WRITES]
    _log.info("executor.studio_writes_dropped", tools=sorted(gone), source="pack")
    return kept, [*dropped, *gone]


#: The one pre-filter slot of :func:`_pack_tools`, by owner. A filter takes the
#: built tools, the dropped names and the held answer, and gives both lists
#: back, less what the owner's rules take out. It runs for the builds of every
#: layer (a, b and c), so a rule of an owner lives here and nowhere else.
_PACK_OWNER_FILTERS: dict[str, Callable[..., Awaitable[tuple[list[Any], list[str]]]]] = {
    "crm-assistant": _drop_pack_mirror_writes,
    "studio-builder": _drop_pack_unless_studio,
    "projects-assistant": _drop_pack_covered,
}

#: The tools that an owner built, by owner: ``(module, its _agent_tools, {slug:
#: tools})``. The Projects owner builds 120 ``FunctionTool`` objects, each with
#: an input model, so a build for each run would pay for 120 models again
#: (OA-8b). The entry holds only while the owner's module and builder are the
#: same objects, so a patched or reloaded owner builds again. An owner with a
#: ``_narrow_tool`` is not cached, because that tool depends on a setting.
_PACK_OWNER_TOOLS: dict[str, tuple[Any, Any, dict[str, list[Any]]]] = {}


def _owner_pack_tools(slug: str, owner: str, source: Any) -> list[Any]:
    """The tools of pack *slug* from *source* (``tool_packs.owner_tools``),
    built once for each owner module (:data:`_PACK_OWNER_TOOLS`)."""
    from acb_skills import tool_packs

    if callable(getattr(source, "_narrow_tool", None)):
        return tool_packs.owner_tools(slug, source)
    builder = getattr(source, "_agent_tools", None)
    hit = _cached_pack_tools(slug, owner, source)
    if hit is not None:  # TOF-1e: a cache hit takes no lock, so a run never waits on the warm-up
        return hit
    with _PACK_LOAD_LOCK:  # a miss: the warm-up thread and a run share the dicts
        entry = _PACK_OWNER_TOOLS.get(owner)
        if entry is None or entry[0] is not source or entry[1] is not builder:
            entry = (source, builder, {})
            _PACK_OWNER_TOOLS[owner] = entry
        if slug not in entry[2]:
            entry[2][slug] = tool_packs.owner_tools(slug, source)
        return list(entry[2][slug])


def _cached_pack_tools(slug: str, owner: str, source: Any = None) -> list[Any] | None:
    """The tools of pack *slug* already built for *owner*, or None. Never builds
    and never takes the lock. With no *source*, it uses the loaded module of the
    owner. An owner with ``_narrow_tool`` keeps no cache, so it gives None."""
    source = source if source is not None else _PACK_OWNER_MODULES.get(owner)
    if source is None or callable(getattr(source, "_narrow_tool", None)):
        return None
    entry = _PACK_OWNER_TOOLS.get(owner)
    if entry is None or entry[0] is not source or entry[1] is not getattr(source, "_agent_tools", None):
        return None
    built = entry[2].get(slug)
    return list(built) if built is not None else None


def _pack_func(fn: Any) -> Any:
    """The callable that a pack tool runs: ``fn.func`` for a MAF
    ``FunctionTool`` (WS-67 OA-8b, the Projects ``_StrictTool``), else *fn*.

    ``_gate_own_maf_tools`` reads ``func`` the same way."""
    try:
        from agent_framework import FunctionTool
    except ImportError:  # pragma: no cover - MAF ships with the platform
        return fn
    return fn.func if isinstance(fn, FunctionTool) else fn


def _pack_call(fn: Any, owner: str, held: bool, run_key: str, slug: str = "") -> Any:
    """ONE pack tool: the scope of the owner outside, the gate inside.

    Each call, in this order:

    1. It refuses a frame of another run, or of no run (the held answer
       belongs to one run). The check compares ``tool_packs.PACK_RUN_KEY`` of
       the frame with *run_key*, which the server minted (OA-7c condition 3).
    2. STRICT (owner, fix round 1): when the chat read mail since the build
       (``read_untrusted_answer``), this call is held although the build was
       open. An egress tool, or a write of :func:`_is_held_off` (RX-1), then
       answers the held answer and does not run.
    2a. OA-8i: a write tool after a hand-off to the owner started in this run
       refuses, and else the write is recorded on the run's record
       (``tool_packs.pack_write_refusal``). No await comes between the run
       check and the record, so a hand-off in the same model step sees it.
       G2 (task 3494): before it, a tool that is not read-only refuses in a
       run with a read-only record (``tool_packs.batch_write_refusal``).
    3. It marks the chat session BEFORE the tool body runs (EM-G11f3, as
       before an email sub-run), for a call that ``tool_packs.marks_chat``
       names: a tool of an owner of :data:`UNTRUSTED_INPUT_AGENTS` whose
       answer can bring outside text (``tool_packs.PACK_OUTSIDE_TEXT``, task
       2492). A send that reads nothing leaves the chat clear.
    4. It runs the gated tool inside ``tool_packs.owner_scope``. The gate reads
       the owner, so an owner's write asks its own card and never the
       untrusted card too.
    5. It closes the run's profile writes, also after a raise (PZ-6a).
    6. It writes the email working set from the call (§7.1.1), one call of
       the chat at a time (OA-7c condition 4).
    7. It makes pack *slug* sticky (``tool_packs.note_used``, OA-7c). A run
       that bound no ``tool_packs.PACKS_KEY`` writes nothing.
    8. HRD-3: ``acb_skills.pack_tool_guard.guard`` is the outermost layer. It
       answers any exception of steps 1 to 7 as text with a next step, and
       it reads a JSON string argument as the shape of its parameter.

    ``functools.wraps`` keeps the name, the docstring, ``__tool_risk__`` and
    ``__wrapped__``, so the signature and the card-bound pin read the owner's
    own function. The wrapper carries ``__cc_gated__``, so a later assembly
    (``_gate_own_maf_tools``) never gates it a second time and never shows a
    second card. :data:`PACK_OWNER_ATTR` names the owner.

    OA-8b: *fn* may be a MAF ``FunctionTool`` (the Projects ``_StrictTool``).
    Its function takes the steps above, and the tool comes back as a shallow
    copy with that function swapped, the idiom of :func:`_gate_own_maf_tools`.
    The class, the input model and so ``additionalProperties: false`` and the
    unknown-argument answer stay as the owner built them, and a copy leaves
    the cached tool of the owner as it was.
    """
    inner = _pack_func(fn)
    if inner is not fn:
        import copy  # noqa: PLC0415

        clone = copy.copy(fn)
        clone.func = _pack_call(inner, owner, held, run_key, slug)
        # HRD-3: the arguments of the copy are read before MAF checks them.
        from acb_skills import pack_tool_guard

        if pack_tool_guard.arg_shapes_enabled():
            pack_tool_guard.install_tool_shapes(clone)
        return clone
    from acb_skills.egress import _register_platform_wrapper
    from acb_skills.tool_packs import (
        PACK_RUN_KEY,
        PACK_SLUG_ATTR,
        batch_write_refusal,
        marks_chat,
        note_used,
        owner_scope,
        pack_tool_runs,
        pack_write_refusal,
    )

    gate_on = os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower() != (
        "approve_all"
    )
    gated = _gate_injected_tool(fn) if gate_on else fn
    name = _tool_name(fn)

    @functools.wraps(gated)
    async def _pack_tool(*args: Any, **kwargs: Any) -> Any:
        from acb_skills.write_artifact import artifact_context

        ctx = artifact_context()
        if not ctx or not run_key or str(ctx.get(PACK_RUN_KEY) or "") != run_key:
            _log.warning("executor.pack_call_refused", tool=name, owner=owner)
            return PACK_CALL_REFUSED.format(tool=name)
        held_now = held or read_untrusted_answer(ctx)
        if held_now and not held and (
                _is_egress(fn) or _is_held_off(fn) or _is_held_off_pack(fn, slug)):
            return _held_in_turn(owner, name)
        # G2 (task 3494): a WhatsApp turn holds read tools only. Its own check,
        # before any await, and it fails closed.
        no_card = batch_write_refusal(name, ctx)
        if no_card is not None:
            return no_card
        twice = pack_write_refusal(owner, name, ctx)  # OA-8i, before any await
        if twice is not None:
            return twice
        pack_tool_runs(slug, ctx)  # RX-1, before any await: its pack stays
        call_args = _call_args(fn, args, kwargs)
        if marks_chat(owner, name, call_args):  # task 2492: only a call that can bring outside text
            await _mark_pack_session(owner, ctx)
        # TOF-3a F12b: a tool of a loaded skill notes its outcome on the run.
        notes = _skill_notes_of(ctx, run_key, name)
        _clear_card_outcome(notes)
        try:
            with owner_scope(owner, held_now):
                result = await gated(*args, **kwargs)
        except Exception as exc:
            _note_skill_call(notes, name, fn, args, kwargs, raised=exc)
            raise
        finally:
            _close_pack_profile_writes()
        _note_skill_call(notes, name, fn, args, kwargs, result=result)
        await _record_pack_call(owner, ctx, name, call_args, result)
        if slug:
            await note_used(slug, ctx)
        return result

    _pack_tool.__cc_gated__ = True  # type: ignore[attr-defined]
    setattr(_pack_tool, PACK_OWNER_ATTR, owner)
    setattr(_pack_tool, PACK_SLUG_ATTR, slug)
    # HRD-3 (chat_failure_modes.md B3, B4): the ONE guard of a pack tool, on the
    # outside. An exception becomes text with a next step, and a JSON string
    # becomes the shape of its parameter (behind TOOL_ARG_SHAPES). The inner
    # wrapper stays at ``__wrapped__``, and so does the owner's function.
    from acb_skills import pack_tool_guard

    trusted = _register_platform_wrapper(gated, _pack_tool)
    guarded = pack_tool_guard.guard(
        trusted, name=name, shapes=pack_tool_guard.arg_shapes_enabled())
    return _register_platform_wrapper(trusted, guarded)


def _held_in_turn(owner: str, name: str) -> str:
    """The answer of an egress pack tool after the chat read mail in this
    turn (STRICT, fix round 1). The build was open, so the tool is on the
    list, and it does not run. The text is the held answer of a pack call
    (``egress.EGRESS_HELD_PACK_ANSWER``), read in the scope of the owner."""
    from acb_skills.egress import egress_held_answer
    from acb_skills.tool_packs import owner_scope

    _log.info("executor.pack_call_held", tool=name, owner=owner)
    with owner_scope(owner, True):
        return egress_held_answer(name)


async def _mark_pack_session(owner: str, ctx: Any) -> None:
    """Mark the chat session before a call of a pack of *owner* (EM-G11f3).

    The executor's own helper, which marks before an email sub-run starts.
    It marks only for an owner of :data:`UNTRUSTED_INPUT_AGENTS`, keys on the
    bound tenant and the session of *ctx*, and never raises. The pack call
    asks ``tool_packs.marks_chat`` first, so only a call that can read
    outside text comes here (task 2492)."""
    from orchestrator.executor import _mark_untrusted_session

    await _mark_untrusted_session(owner, ctx)


def _close_pack_profile_writes() -> None:
    """Close the run's profile writes after a pack call. Never raises."""
    from orchestrator.executor import _close_profile_writes

    _close_profile_writes()


def _call_args(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """The arguments of one call by name. A model passes keywords, and a
    positional call gets the names of the owner's signature."""
    import inspect

    try:
        return dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        return dict(kwargs)


#: One lock for each chat, held while a pack call reads and writes the email
#: working set (OA-7c condition 4). MAF can run two tool calls at once. Each
#: read the set before the other wrote it, so one update was lost. A weak map,
#: so a lock leaves when no call holds it.
_PACK_WS_LOCKS: weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
    weakref.WeakValueDictionary())


def _pack_ws_lock(ctx: Any) -> asyncio.Lock:
    """The lock of the chat of *ctx*: its bound tenant and its session."""
    try:
        from acb_skills.egress import _session_of

        slot = _session_of(ctx)
    except ImportError:
        slot = None
    if slot is None:
        slot = ("", str((ctx or {}).get("session_id") or ""))
    lock = _PACK_WS_LOCKS.get(slot)
    if lock is None:
        lock = asyncio.Lock()
        _PACK_WS_LOCKS[slot] = lock
    return lock


async def _record_pack_call(
    owner: str, ctx: Any, name: str, args: dict[str, Any], result: Any,
) -> None:
    """Write the email working set of the chat after one pack call (§7.1.1).

    The executor's two functions, which the delegated path calls after an
    email sub-run. An owner other than the email assistant writes nothing.

    OA-7c condition 4: the read and the write run under the lock of the chat
    (:func:`_pack_ws_lock`), so two pack calls at once each see the other's
    update, and none is lost. Condition 5: the mailbox event "Now working on
    <mailbox>" goes to the run's own queue, and a run with no queue gets it on
    its relay stream (``executor._emit_to_run_or_relay``).

    HRD-11a: then, under the same lock, the item in focus of the chat
    (``executor._record_chat_focus``), for an email or a Projects owner. Only
    a run with its own binding writes it (``CHAT_FOCUS_ORGS``). Never raises."""
    try:
        from orchestrator import executor as ex

        emit = ex._emit_to_run_or_relay(str(ctx.get("session_id") or "") or None)
        call = {"id": "", "name": name, "args": args, "result": str(result), "success": True}
        async with _pack_ws_lock(ctx):
            before, _preamble = await ex._email_working_set_for(owner, ctx)
            if before is not None:
                await ex._record_email_working_set(owner, ctx, before, [call], emit)
            await ex._record_chat_focus(owner, ctx, [call])
    except Exception as exc:  # the working set never costs the call
        _log.warning("executor.pack_ws_unwritten", tool=name, error_type=type(exc).__name__)


# ── Tool packs in a live run: layer (a) (WS-67 OA-7c, D117) ─────────────────
# ``one_assistant_context.md`` §4.2.2 and §4.2.8. The executor reads the
# sticky state beside the load (``executor._start_pack_turn``), and calls
# :func:`_apply_tool_packs` after ``_apply_agent_md_overrides`` and BEFORE
# :func:`_inject_agent_tools`. Each pack tool carries ``__cc_gated__``, so
# :func:`_gate_own_maf_tools` never gates it twice. Each run builds its packs
# again: no ``PackBuild`` and no pack tool lives past its run (condition 1).
# Fences: ``tests/unit/test_tool_packs_choice.py`` and
# ``tests/unit/test_tool_packs_off.py``.


@dataclasses.dataclass(frozen=True)
class PackApply:
    """What :func:`_apply_tool_packs` put in one run."""

    builds: tuple[PackBuild, ...]
    #: ``tool_packs.PackRun``: the record that the run binds.
    record: Any
    #: The value that keys the run check of each pack call. The server mints
    #: it for each run (condition 3).
    run_key: str
    #: TOF-3a F12b: the notes of the skills of this run
    #: (:class:`SkillRunNotes`), or None. Only an org on
    #: ``STUDIO_SKILL_REPAIR_ORGS`` with a loaded skill gets notes.
    skill_notes: Any = None

    def context(self) -> dict[str, Any]:
        """The keys that the executor derives into the run binding, after
        ``bind_artifact_context``. A delegated run drops each of them. The
        notes key is there only when the run has notes."""
        from acb_skills.tool_packs import PACK_RUN_KEY, PACKS_KEY

        out = {PACK_RUN_KEY: self.run_key, PACKS_KEY: self.record}
        if self.skill_notes is not None:
            out[SKILL_RUN_NOTES_KEY] = self.skill_notes
        return out


def _as_agent_tools(tools: list[Any]) -> list[Any]:
    """*tools* in the shape of the agent's own list: a MAF ``FunctionTool``
    for each, as ``Agent.__init__`` makes them. The ``func`` of each is the
    pack wrapper, so it keeps ``__cc_gated__`` and :func:`_gate_own_maf_tools`
    skips it: the mark, not the shape, stops a second gate. With no MAF the
    plain functions come back."""
    try:
        from agent_framework._tools import normalize_tools
    except ImportError:  # pragma: no cover - MAF ships with the platform
        return list(tools)
    return list(normalize_tools(list(tools)))


def _delegates_to(tool: Any) -> str | None:
    """The agent that a specialist tool hands work to (``__delegates_to__``,
    set in ``orchestrator.agents``), or None."""
    target = getattr(getattr(tool, "func", None), "__delegates_to__", None)
    if target is None:
        target = getattr(tool, "__delegates_to__", None)
    return str(target) if target else None


def _facts_removed() -> frozenset[str]:
    """The CRM facts tools that :func:`_add_crm_facts_block` will take from a
    run of this org (WS-67 OA-8f), for the count of the pack cap.

    An org off ``CRM_QUESTIONS_ORGS`` loses all of them, and so does a box with
    the list empty: :func:`_facts_withheld` names none there, but the block
    still removes them when it reads no fact row. A listed org loses what
    :func:`_facts_withheld` names. The block may still remove more when its
    read returns no row, and that read needs the network, so the estimate
    keeps those tools and errs on the safe side."""
    try:
        from acb_skills.crm_tools import questions_org_on
        from acb_skills.org_facts_tools import FACTS_GATED_TOOLS
    except ImportError:
        return frozenset()
    if not questions_org_on(_run_org_or_none()):
        return frozenset(fn.__name__ for fn in FACTS_GATED_TOOLS)
    return _facts_withheld()


def _pack_base_count(
    agent: Any, *, agent_name: str | None, tool_scope: list[str] | None,
    agent_config: dict[str, Any] | None, disabled: frozenset[str],
) -> int:
    """The tools of the run before its packs, for the cap of layer (a).

    It counts the tools that *agent* holds now, and the platform tools that
    :func:`_inject_agent_tools` adds. It reads the same inputs in the same
    order: the static chain within the resolved scope, the no-match fallback,
    the workflow trio, and the same withheld names (D85, the floor opt-out,
    WhatsApp and the CRM facts). So with no app grant it equals the count
    after the injection (fix round 1, Q1). A granted app tool is not
    counted, because its read needs the database, and an MCP tool is not
    counted either (the log line ``executor.request_tools`` shows both).

    OA-8f: the CRM facts tools that ``_add_crm_facts_block`` removes for an
    org off ``CRM_QUESTIONS_ORGS`` are not counted (:func:`_facts_removed`).
    The loader is not counted here, because a context provider holds it:
    :func:`_apply_tool_packs` adds it when the run offers it. The executor
    logs the true count after the injection (``executor.pack_request_tools``)
    and warns past the cap. Fence: ``tests/unit/test_exact_tool_count.py``.
    """
    return len(_pack_base_names(agent, agent_name=agent_name, tool_scope=tool_scope,
                                agent_config=agent_config, disabled=disabled))


def _pack_base_names(
    agent: Any, *, agent_name: str | None, tool_scope: list[str] | None,
    agent_config: dict[str, Any] | None, disabled: frozenset[str],
) -> set[str]:
    """The names of the tools that :func:`_pack_base_count` counts. WS-STU F3
    reads them too: a skill pack loads only when the run holds each of its
    tools (:func:`_skills_present`). TOF-1b: a chat tool that these names
    hold brings no pack (``skill_packs.pack_needs``)."""
    from acb_skills.skill_families import SKILL_FAMILIES

    names = {_tool_name(t) for _label, pool in _own_tool_pools(agent) for t in pool}
    gone = _facts_removed()
    names -= gone  # `_add_crm_facts_block` drops an agent's own copy too
    withheld = (_withheld_shell_tools(agent_name, agent_config)
                | _floor_opt_out(agent_name, agent_config)
                | _whatsapp_withheld() | _image_withheld() | gone
                # SBX-19: never in the base. It takes a place only after the
                # packs (`_COMPUTE_ROOM`), so it cannot cost a pack its place.
                | frozenset({"compute_run"}))
    scope = _resolve_injected_scope(tool_scope, disabled_families=disabled, withheld=withheld)
    chain = [_tool_name(fn) for fn in _collect_injectable_platform_tools(agent_name)]
    injected = set(chain) if scope is None else {name for name in chain if name in scope}
    if scope is not None and not injected:  # the no-match fallback of the injection
        injected = set(chain)
    if "workflows" not in disabled:
        injected |= set(SKILL_FAMILIES["workflows"]["tools"])
    return names | (injected - withheld)


def _read_only_build(build: PackBuild | None) -> PackBuild | None:
    """G2 (task 3494): *build* with its read tools only
    (``tool_packs.batch_read_tools``, which fails closed), or None."""
    if build is None:
        return None
    from acb_skills import tool_packs

    return dataclasses.replace(build, tools=tuple(tool_packs.batch_read_tools(build.tools)))


def _turn_persona(
    persona: str, loaded: Sequence[tuple[str, bool]], bullets: Mapping[str, str],
    via: Iterable[str], build_ctx: Mapping[str, Any], *, skills: Sequence[Any],
    builds: Sequence[PackBuild], read_only: bool,
) -> str | None:
    """The persona of a pack turn, or None when the swap fails.

    ``tool_packs.persona_for`` for the web chat. G2 (task 3494): a WhatsApp
    turn (*read_only*) swaps out no delegation text, so the persona keeps
    its bytes, and ``tool_packs.batch_rules`` follows it. That one line names
    the packs and no write tool."""
    from acb_skills import tool_packs

    if read_only:
        rules = tool_packs.batch_rules(b.slug for b in builds)
        return f"{persona.rstrip(chr(10))}\n\n{rules}\n" if rules else persona
    try:
        return tool_packs.persona_for(persona, loaded, bullets, via, build_ctx, skills=skills)
    except RuntimeError as exc:
        _log.error("executor.packs_refused", reason="persona_swap", error=str(exc)[:200])
        return None


def _specialist_counts(
    pool: Sequence[Any], only: Iterable[str] | None = None,
) -> dict[str, int]:
    """How many specialist tools of each owner the *pool* holds (OA-8f).

    *only* narrows the answer to those owners. ``tool_packs.choose`` reads it
    to price the tools that a chosen pack takes out of the request."""
    keep = None if only is None else frozenset(only)
    out: dict[str, int] = {}
    for item in pool:
        target = _delegates_to(item)
        if target and (keep is None or target in keep):
            out[target] = out.get(target, 0) + 1
    return out


async def _apply_tool_packs(
    agents: list[Any],
    turn: Any,
    run_ctx: Mapping[str, Any],
    *,
    allowed: Iterable[str],
    agent_name: str,
    tool_scope: list[str] | None,
    agent_config: dict[str, Any] | None,
    bullets: Mapping[str, str],
    offer_loader: bool = False,
    is_authorized: Any = None,
    read_only: bool = False,
) -> PackApply | None:
    """Put the packs of this turn in the run (layer (a), §4.2.2).

    G2 (task 3494): *read_only* is the WhatsApp turn. It loads no published
    skill, keeps the read tools of each build only
    (``tool_packs.batch_read_tools``) and skips a build with none. The record
    is read-only: no owner leaves, no hand-off refuses, and the persona keeps
    its bytes with ``tool_packs.batch_rules`` after it. The executor passes
    no loader with it.

    *turn* is the ``tool_packs.PackTurn`` that the executor read beside the
    load. *allowed* is ``tool_packs.packs_on`` of the bound tenant. The
    admin's skill toggles narrow it here (``skills_registry.md`` rule 1), so
    a pack that an admin turned off for the orchestrator never loads.

    In order: the choice and the cap (``tool_packs.choose``), the eviction of
    the decayed and capped sticky packs, a fresh run key, the build of each
    pack (:func:`_pack_tools`, with the run's own tool names as *holds*), the
    plan of the loader (``tool_packs.plan_loader``, OA-7i), the persona
    (``tool_packs.persona_for``), and last the change of the agent: the
    persona, the specialist tool of each owner that a pack or the loader
    replaces, and the pack tools after the core tools, in load order.

    *run_ctx* is the server-side run context: the tenant is bound, and it
    names the session, the member and ``no_egress`` (condition 2). A run with
    no bound tenant loads no pack at all, so it holds no pack send, and
    :func:`_pack_held` holds a build with no tenant too. Returns None, and
    changes nothing, when no pack loads, when the agent is not a native MAF
    ``Agent``, or when the persona does not hold the owner's text exactly
    once.

    OA-7e: *offer_loader* also offers layer (b), ``load_skill_tools``, for
    the allowed packs that the run does not hold (:func:`_offer_pack_loader`).
    Then a turn that loads no pack still returns a ``PackApply``, with no
    build, so that the executor binds the run key and the record that a load
    needs. The executor passes True. With False the apply is layer (a) only.

    OA-7i: with the loader offered, ONE plan decides the offer and the
    replacement of a specialist (:func:`_plan_pack_loader`). *is_authorized*
    is the run's grant check, which the MCP lookahead reads.

    WS-67 TS-2a (D222 decision 13): with *offer_loader* and
    ``tool_packs.tool_search_on`` of the bound tenant, the run also holds
    ``find_tools`` on every turn. It counts in the base before the first
    choice, as the load tool does. The candidates come from ONE guarded
    build of each pack to search (:func:`_find_candidates`), and the apply
    then returns a ``PackApply`` also with no build and no offer, so that
    the executor binds the run key and the record. With the flag off,
    nothing of this runs.
    """
    import secrets

    from acb_common.db import current_tenant
    from acb_skills import tool_packs

    agent = agents[0] if agents else None
    opts = getattr(agent, "default_options", None)
    pool = opts.get("tools") if isinstance(opts, dict) else None
    if not isinstance(pool, list):
        _log.warning("executor.packs_refused", reason="agent_shape")
        return None
    org = current_tenant()
    if not org:
        _log.warning("executor.packs_refused", reason="no_tenant")
        return None
    # PJP-2 fix round 3 (P3-1): a pack of FOLLOWS is off where an admin turned
    # its parent off, as it is on where the pack flag names its parent.
    disabled = tool_packs.with_followers(_load_disabled_skill_families(agent_name))
    allow = frozenset(allowed) - disabled
    # TOF-3a F12c: the repair pack is off for an org off the switches of the
    # repair, so neither ``choose`` nor the loader offers it there.
    allow = _without_closed_repair(allow)
    # WS-STU F3: the published skills that the words of this turn name. G2: a
    # WhatsApp turn loads none.
    # TOF-3a F12b: the facts of each skill that the turn reads, for the record
    # of its run. ``_turn_with_skills`` fills them only for an org on the flag.
    facts_token = _SKILL_FACTS.set({})
    try:
        turn = turn if read_only else await _turn_with_skills(turn)
        facts = _SKILL_FACTS.get() or {}
    finally:
        _SKILL_FACTS.reset(facts_token)
    # CSY-4 (chat_connected_systems.md §3.4): the words of the servers that may
    # attach, and the mail mark, read once before ``choose``. Dark behind
    # MCP_MAIL_PRECEDENCE_ORGS. G2: a WhatsApp turn keeps the choice of main.
    turn = await _with_server_words(turn, agent, agent_name, run_ctx, is_authorized,
                                    read_only=read_only)
    base = _pack_base_count(agent, agent_name=agent_name, tool_scope=tool_scope,
                            agent_config=agent_config, disabled=disabled)
    _COMPUTE_ROOM.set(base + 1 <= tool_packs.MAX_REQUEST_TOOLS)  # a no-pack exit keeps it
    # WS-67 TS-2a (D222 decision 13): ``find_tools`` rides in each request of
    # a run where the search is on, so the cap counts it before the first
    # choice. The org is the bound tenant, never a request field (R5). At the
    # cap, this count can make ``choose`` drop the last pack, and the owner
    # accepted that trade. With the flag off the count does not change. The
    # setup needs the choice, so it runs later: when it gives no finder (an
    # agent that is no MAF ``Agent``, or a fault), the count is one high,
    # which errs on the safe side.
    search_on = offer_loader and tool_packs.tool_search_on(org)
    if search_on:
        base += 1
    # G2: in a read-only turn the specialist of each owner stays.
    specialists = {} if read_only else _specialist_counts(pool)
    # TOF-1b: the code packs that each skill of the turn brings. A turn with no
    # skill makes no map, and ``choose`` then gives the choice of F3.
    skill_map = _skill_pack_map(turn, agent, agent_name=agent_name, tool_scope=tool_scope,
                                agent_config=agent_config, disabled=disabled)
    choice = tool_packs.choose(
        turn, allowed=allow, base_tools=base, specialists=specialists,
        skill_packs=skill_map)
    loader_counted = False
    if offer_loader and tool_packs.loadable(allow, choice.slugs):
        # OA-7e: the load tool rides in the request too, so the cap counts
        # it. OA-8f: only when it will be offered, which is when an allowed
        # pack stays unloaded after layer (a). The choice then runs again with
        # the load tool in the count. When layer (a) loads each allowed pack,
        # the run offers no load tool and the count has none. The plan can
        # still refuse the offer when no pack fits, and the count then errs
        # by one on the safe side.
        base += 1
        loader_counted = True
        choice = tool_packs.choose(
            turn, allowed=allow, base_tools=base, specialists=specialists,
            skill_packs=skill_map)
    _log_skills_refused(choice.skills_refused)
    await tool_packs.drop_sticky(turn, choice.evicted)
    # CSY-4: the reason of an eviction rides on the log line only.
    reason = _server_named_field(choice)
    if not choice.slugs:
        _log.info("executor.packs_chosen", packs=[], evicted=list(choice.evicted),
                  skipped=list(choice.skipped), base_tools=base, **reason)
        if not offer_loader and not choice.skills:
            return None
    run_key = secrets.token_urlsafe(24)
    build_ctx = {**dict(run_ctx), tool_packs.PACK_RUN_KEY: run_key}
    holds = {_tool_name(t) for t in pool}
    builds: list[PackBuild] = []
    for slug in choice.slugs:
        build = await _pack_tools(slug, build_ctx, holds=holds)
        # G2: the guarded build, then its read tools only. A pack with no read
        # tool is skipped.
        build = _read_only_build(build) if read_only else build
        if build is None or not build.tools:
            continue
        builds.append(build)
        holds |= {_tool_name(t) for t in build.tools}
    skills = _skills_present(
        choice.skills, holds, agent, agent_name=agent_name, tool_scope=tool_scope,
        agent_config=agent_config, disabled=disabled)
    # TOF-1b: a skill that failed here leaves the run as it was without it.
    builds, skills, holds = _keep_skill_packs(
        builds, skills, holds, skill_map=skill_map, layers=choice.layers, pool=pool,
        agent=agent, agent_name=agent_name, tool_scope=tool_scope,
        agent_config=agent_config, disabled=disabled)
    choice = await _evict_unheld_skill_packs(turn, choice, builds)
    if not builds and not offer_loader and not skills:
        return None
    loaded = [(build.slug, build.held) for build in builds]
    added = _as_agent_tools([tool for build in builds for tool in build.tools])
    # WS-STU F3, review fixes P1 and P2: a skill pack takes a place of the 3,
    # so the plan and the loader count it. A code pack that holds a tool of a
    # skill never leaves for a load (``PackRun.needs``).
    skill_slugs = [s.slug for s in skills]
    named = {name for s in skills for name in s.tools}
    needs = [b.slug for b in builds if named & {_tool_name(t) for t in b.tools}]
    draft = tool_packs.PackRun.of(turn, choice, loaded, skills=skill_slugs, needs=needs,
                                  read_only=read_only)
    kept = [t for t in pool if _delegates_to(t) not in draft.replaced]
    current = base + len(added) - (len(pool) - len(kept))
    plan = tool_packs.LoaderPlan()
    # TS-2a: the candidates of ``find_tools``, from the ``allow`` of this apply
    # (the admin's toggles widened by FOLLOWS), never a fresh flag read, and
    # the ONE test of the owners that the plan reads too. Both None when the
    # search is off. A sticky pack that left at the start of the turn
    # (``choice.evicted``) counts as held, so it is no candidate: the line of
    # the turn tells the model not to call its tools (``left_line``).
    available, candidates = await _search_setup(
        search_on, agent, allow, (*choice.slugs, *choice.evicted), frozenset(holds), build_ctx,
        persona=str(opts.get("instructions") or ""), bullets=bullets)
    if offer_loader:
        # RX-1: a spare pack gives way to a load, so the plan counts it free:
        # an old sticky pack, and (task 1747, D169 item 8 as amended) a pack
        # that came in only by the words. For the Projects owner the plan
        # also reaches the owner through it.
        built = {b.slug: len(b.tools) for b in builds}
        plan = await _plan_pack_loader(
            agent, allow, [*(b.slug for b in builds), *skill_slugs], builds=builds,
            current=current,
            pool=kept, persona=str(opts.get("instructions") or ""), bullets=bullets,
            agent_name=agent_name, no_egress=bool(run_ctx.get("no_egress")),
            is_authorized=is_authorized,
            spare={slug: built[slug] for slug in draft.spare() if slug in built},
            available=available)
    if not builds and not plan.offered and not skills and candidates is None:
        return None
    via = plan.via_loader
    persona = _turn_persona(
        str(opts.get("instructions") or ""), loaded, bullets, via, build_ctx,
        skills=skills, builds=builds, read_only=read_only)
    if persona is None:
        return None
    # Task 1747: the record keeps the sticky packs that left at the start of
    # this turn, and which of them the loader offers. The context of the turn
    # names them (``executor._pack_left_block``, ``tool_packs.left_line``).
    record = tool_packs.PackRun.of(turn, choice, loaded, via, offered=plan.offered,
                                   skills=skill_slugs, needs=needs, read_only=read_only)
    replaced = frozenset(record.replaced)
    # OA-8f: the count that the run sends once this apply is done: the base
    # (with the load tool only when the plan offers it, and with
    # ``find_tools`` when the search is on, TS-2a), the pack tools, less
    # each specialist tool that leaves (a pack's, and an owner the loader
    # reaches). The inject step after this one adds nothing to the count.
    planned_tools = (base - int(loader_counted) + int(bool(plan.offered))
                  + len(added) - sum(_specialist_counts(pool, replaced).values()))
    # SBX-19: ``compute_run`` takes the place that is left, and no other. It
    # leaves room for the largest pack that the loader OFFERS and that fits
    # without it, so a load never misses by the one place of the tool.
    from acb_skills.skill_families import SKILL_FAMILIES

    cap = tool_packs.MAX_REQUEST_TOOLS
    reserve = max(
        (n for n in (len(SKILL_FAMILIES[slug]["tools"]) for slug in plan.offered
                     if slug in SKILL_FAMILIES) if planned_tools + n <= cap),
        default=0)
    _COMPUTE_ROOM.set(planned_tools + reserve + 1 <= cap)
    opts["instructions"] = persona
    pool[:] = [t for t in pool if _delegates_to(t) not in replaced]
    pool.extend(added)
    # Fix round 1: the "Delegatable agents" block of the injection leaves out
    # an owner that the run may not hand off to, and only such an owner.
    # OA-7i: an owner that the loader replaces is one of them.
    with contextlib.suppress(Exception):
        setattr(agent, PACK_REFUSED_ATTR, frozenset(record.refusals) | via)
    # OA-7h counts each ``executor.packs_loaded`` line before the first model
    # call as a pack load, so only a turn that built a pack logs it. The facts
    # of the plan ride on their own event (OA-7i fix round 1).
    if builds:
        _log.info(
            "executor.packs_loaded", packs=list(record.slugs),
            layers={slug: choice.layers.get(slug, "") for slug in record.slugs},
            held=dict(record.held), replaced=sorted(replaced), via_loader=sorted(via),
            refused=sorted(record.refusals),
            evicted=list(choice.evicted), skipped=list(choice.skipped),
            base_tools=base, pack_tools=sum(len(b.tools) for b in builds), **reason,
        )
    _log.info(
        "executor.pack_plan", offered=list(plan.offered), via_loader=sorted(via),
        packs=list(record.slugs), base_tools=base, current_tools=current,
        planned_tools=planned_tools,
    )
    _log_skills_loaded(skills, choice.skills_skipped)
    _note_dry_run_loaded(skill_slugs)  # WS-STU F4b
    applied = PackApply(builds=tuple(builds), record=record, run_key=run_key,
                        skill_notes=_skill_run_notes(
                            facts, skills, run_ctx, run_key,
                            words=str(getattr(turn, "words", "") or ""),
                            pack_tools={_tool_name(t) for b in builds for t in b.tools}))
    if offer_loader and not _offer_pack_loader(agents, applied, plan, candidates) and via:
        _log.error("executor.pack_loader_stranded", owners=sorted(via))
    # TS-2a: with the search on, the run holds ``find_tools`` also with no
    # build, no offer and no candidate, so the run key and the record bind.
    return applied if builds or plan.offered or skills or candidates is not None else None


# ── Published skills as packs (WS-STU F3) ───────────────────────────────────
# ``ai_studio.md`` §7 "Runtime", §11.1 and the §14 F3 row. Dark: the read
# runs only inside :func:`_apply_tool_packs`, so only for an org that
# the pack gate of ``tool_packs`` names, and only while
# ``STUDIO_ORGS`` names the org and the member holds ``feature:studio``
# (:func:`_studio_member_open`). Fences: ``tests/unit/test_skill_packs.py``
# and ``tests/unit/test_skill_packs_r8.py`` (R8).
# TOF-1b (``ai_studio_rows/TOF-1b.md``): a ``tool:`` action of a skill brings
# the pack of its tool when the run base does not hold the tool. The action
# loads only while ``STUDIO_CHAT_TOOLS_ORGS`` names the org
# (:func:`_chat_tools_open`). ``tool_packs.choose`` adds the pack last, and
# :func:`_apply_tool_packs` logs each skill that cannot bring its packs.
# TOF-1c (``ai_studio_rows/TOF-1c.md``): when no name matches, the head
# descriptions of the loadable skills give at most one skill
# (``tool_search.match_skill_descriptions``). Only while
# ``STUDIO_CHAT_TOOLS_ORGS`` names the org.


async def _turn_with_skills(turn: Any) -> Any:
    """*turn* with the published skills that its words name, or *turn* as it
    was. Never raises: a fault loads no skill.

    The org is the bound tenant of the run, and the member is the verified
    member of the run (``executor._verified_run_member``). Both must agree
    with the turn, so a turn of another chat reads nothing (R5). The read
    opens ``tenant_session()`` on the bound tenant, so RLS binds it. The
    store gives only the skills that the member may load
    (``skills_store.list_loadable``). The match reads the names, and only the
    matched skills read a body, at most ``tool_packs.MAX_PACKS`` of them.

    TOF-1c: when no name matches, the description pass reads the head
    descriptions of those same skills, and it names one skill at most. It
    runs only when a head has a description and :func:`_chat_tools_open` is
    True. That one read of the flag also serves the build. The pass writes
    one ``executor.skill_matched`` line with the id and the hit count, and
    never a word of the text. The description adds nothing to the prompt:
    ``skill_packs.build`` makes the same pack as for a match by name.

    WS-STU F4b: in a skill test, the turn holds the forced pack of the test
    and only that pack. The words choose nothing, and no store read runs."""
    from acb_common.db import current_tenant
    from acb_skills import skill_packs, tool_packs, tool_search
    from acb_skills.skill_dry_run import current_dry_run

    if (dry := current_dry_run()) is not None:
        return dataclasses.replace(turn, skills=(dry.pack,))
    words = str(getattr(turn, "words", "") or "")
    if not words.strip() or not _studio_open():
        return turn
    try:
        from orchestrator.executor import _verified_run_member

        member = _verified_run_member()
        org = str(current_tenant() or "").strip().lower()
        if (not member or not org or org != str(getattr(turn, "org", "")).strip().lower()
                or tool_packs._member_part(member) != getattr(turn, "member_part", None)):
            return turn
        if not await _studio_member_open():
            return turn
        from acb_common.db import tenant_session
        from gateway.studio import skills_store

        flag: bool | None = None
        async with tenant_session() as db:
            heads = await skills_store.list_loadable(db, member)
            named = [h.id for h in heads if skill_packs.matches(h.name, h.slug, words)]
            # TOF-1c: a name wins. The pass needs a description and the flag.
            if not named and any(h.description for h in heads):
                flag = _chat_tools_open()
                found = tool_search.match_skill_descriptions(
                    words, {h.id: h.description for h in heads}) if flag else None
                if found is not None:
                    named = [found[0]]
                    _log.info("executor.skill_matched", skill=found[0], hits=found[1])
            pairs = (await skills_store.loadable_versions(
                db, member, named[:tool_packs.MAX_PACKS]) if named else [])
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("executor.skills_unread", error_type=type(exc).__name__)
        return turn
    packs: list[Any] = []
    bodies: list[tuple[Any, Any]] = []
    # TOF-1b: one read of the flag a turn, and only for a turn with a skill to
    # build. TOF-1c: the description pass may have read it already.
    chat_tools = (flag if flag is not None else _chat_tools_open()) if pairs else False
    for skill, version in pairs:
        built = skill_packs.build(
            skill.id, skill.name, skill.description, version.version,
            version.content_hash, version.body, max_tools=tool_packs.MAX_PACK_TOOLS,
            chat_tools=chat_tools)
        if isinstance(built, str):
            _log.info("executor.skill_refused", skill=skill.id, version=version.version,
                      reason=built)
            continue
        packs.append(built)
        bodies.append((built, version.body))
    _note_skill_facts(bodies)  # TOF-3a F12b
    return dataclasses.replace(turn, skills=tuple(packs)) if packs else turn


# ── The record of skill runs (WS-TOF TOF-3a F12b) ───────────────────────────
# ``ai_studio_rows/F12.md`` "The writers (F12b)". Dark behind
# ``STUDIO_SKILL_REPAIR_ORGS`` (and ``STUDIO_ORGS``). Three hooks, and no new
# chat hook:
#
# 1. The skill list of :func:`_turn_with_skills`. For an org on the flag it
#    keeps the facts of each built skill (:func:`_note_skill_facts`): the id,
#    the version, the repair key of the body, and the action of each tool.
#    :func:`_apply_tool_packs` keeps the facts of the skills that the run
#    holds at the end (:func:`_skill_run_notes`), in ``PackApply.skill_notes``.
# 2. The pack tool guard. ``_pack_call`` hands each call of a tool of a
#    loaded skill to :func:`_note_skill_call`: a raise or a refused answer
#    is ``tool_error``, and a card that the member declined
#    (``ask_tools.last_card_outcome``) is ``card_declined``.
# 3. The end of the run. ``executor.run_agent_stream`` calls
#    :func:`schedule_skill_runs` in its ``finally``. It starts ONE task that
#    writes one ``live`` row for each loaded skill (:func:`_write_skill_runs`),
#    so the write never slows the turn. A fault logs its class only.
#
# ``member_said_wrong`` is F12c: the repair route writes it.
# The notes keep the words of the turn in memory only, to hash them. No log
# line, no repr and no row holds them. Fence: ``tests/unit/test_skill_repair.py``
# (``TestTheWriters``) and ``tests/unit/test_skill_run_writers_r8.py`` (R8).

#: The key of the run context that holds the :class:`SkillRunNotes` of a run.
SKILL_RUN_NOTES_KEY = "skill_run_notes"

#: The ``detail`` of a passed row of a skill whose tools no pack tool of the
#: run holds. The hook of the pack tool guard cannot see those calls, so the
#: row did not observe the run. A code word, never a value.
UNOBSERVED = "unobserved"

#: The facts of the skills that :func:`_turn_with_skills` built, by pack slug.
#: :func:`_apply_tool_packs` binds an empty dict around the call, so a call
#: from any other place keeps no fact. The default is None, never a mutable
#: object.
_SKILL_FACTS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "acb_skill_run_facts", default=None)

#: The live tasks of :func:`schedule_skill_runs`, so that no task is lost
#: before it ends.
_SKILL_RUN_TASKS: set[asyncio.Task[Any]] = set()


@dataclasses.dataclass(frozen=True)
class _LoadedSkill:
    """The facts of one loaded skill that its row needs. Names and ids only."""

    skill_id: str
    version: int
    body_key: str
    #: The action of the skill for each chat tool of the skill.
    actions: Mapping[str, str]


def _skill_repair_open() -> bool:
    """True when ``STUDIO_ORGS`` and ``STUDIO_SKILL_REPAIR_ORGS`` both hold
    the org of this run. The org is the run's own (:func:`_run_org_or_none`),
    never an argument (R5). It fails closed, and it logs nothing."""
    try:
        from gateway.routes.studio.flags import skill_repair_allowed, studio_allowed

        org = _run_org_or_none()
        return bool(studio_allowed(org) and skill_repair_allowed(org))
    except Exception:  # fail closed
        return False


def _skill_actions(body: Any) -> dict[str, str]:
    """The action of the skill for each chat tool of *body*. A tool that two
    actions serve, or an action that is no plain name, takes ``tool:<name>``."""
    from acb_skills import skill_packs
    from acb_skills.hidden_text import strip_hidden

    raw = body.get("actions") if isinstance(body, Mapping) else None
    seen: dict[str, list[str]] = {}
    for action in raw if isinstance(raw, list) else []:
        clean = strip_hidden(str(action)).strip()
        tool = skill_packs.action_tool(clean)
        if tool:
            seen.setdefault(tool, []).append(clean)
    return {tool: (names[0] if len(names) == 1 and " " not in names[0] else f"tool:{tool}")
            for tool, names in seen.items()}


def _note_skill_facts(bodies: Sequence[tuple[Any, Any]]) -> None:
    """Keep the facts of each built skill pack of *bodies* in
    :data:`_SKILL_FACTS`. Only inside :func:`_apply_tool_packs`, only with a
    skill, and only for an org on the flag. It never raises."""
    facts = _SKILL_FACTS.get()
    if facts is None or not bodies or not _skill_repair_open():
        return
    try:
        from gateway.studio.skill_repair import body_key
    except Exception:  # pragma: no cover - the gateway ships with the platform
        return
    for built, body in bodies:
        try:
            facts[str(built.slug)] = _LoadedSkill(
                skill_id=str(built.skill_id), version=int(built.version),
                body_key=body_key(body), actions=_skill_actions(body))
        except Exception as exc:  # the class only: one bad body skips its row
            _log.info("executor.skill_run_unnoted", skill=str(getattr(built, "skill_id", "")),
                      error_type=type(exc).__name__)


class SkillRunNotes:
    """What one run did with each loaded skill (F12b). One object for each
    run, shared by each tool call of the run, as ``assistant_profile``'s
    steer holder is.

    It holds the org, the member, the run and the chat of the run, the facts
    of each loaded skill, the first failure of each skill, and the write
    actions with their key names. The words of the turn stay in a private
    slot, only to hash them at the write, and the repr never shows them."""

    __slots__ = ("_words", "failures", "member", "org", "run_id", "run_key", "skills",
                 "thread_id", "unobserved", "writes")

    def __init__(self, *, org: str, member: str, run_id: str, thread_id: str | None,
                 run_key: str, skills: Mapping[str, _LoadedSkill], words: str,
                 unobserved: Iterable[str] = ()) -> None:
        self.org = org
        self.member = member
        self.run_id = run_id
        self.thread_id = thread_id
        self.run_key = run_key
        self.skills = dict(skills)
        self._words = words
        #: The slugs of the skills with no tool that is a pack tool of the run.
        #: The hook cannot see their calls, so a passed row of one says
        #: :data:`UNOBSERVED` in its detail.
        self.unobserved = frozenset(unobserved) & frozenset(self.skills)
        #: The slug of a skill to its reason and detail, as the row keeps them.
        self.failures: dict[str, tuple[str, str]] = {}
        #: The slug of a skill to each write action and its key names.
        self.writes: dict[str, dict[str, set[str]]] = {}

    def __repr__(self) -> str:
        return f"SkillRunNotes(run_id={self.run_id!r}, skills={sorted(self.skills)!r})"

    def holds(self, tool: str) -> bool:
        """True when *tool* is a tool of a loaded skill of the run."""
        return any(tool in s.actions for s in self.skills.values())

    def saw(self, tool: str, *, error: str | None = None, declined: bool = False,
            keys: Iterable[str] | None = None) -> None:
        """Note one call of *tool*. *error* is the class of a raise, or the
        kind of a refused answer. *declined* is a card that the member
        declined. *keys* is the key names of a write call, or None for a
        read. ``tool_error`` wins over ``card_declined``, and the first of
        each kind stays."""
        for slug, skill in self.skills.items():
            action = skill.actions.get(tool)
            if action is None:
                continue
            if error:
                if self.failures.get(slug, ("",))[0] != "tool_error":
                    self.failures[slug] = ("tool_error", f"{tool}: {error}")
            elif declined and slug not in self.failures:
                self.failures[slug] = ("card_declined", tool)
            if keys is not None:
                self.writes.setdefault(slug, {}).setdefault(action, set()).update(keys)

    def row_of(self, slug: str) -> tuple[bool, str, str, list[dict[str, Any]]]:
        """The ``passed``, the reason, the detail and the writes of the row
        of the skill *slug*."""
        reason, detail = self.failures.get(
            slug, ("passed", UNOBSERVED if slug in self.unobserved else ""))
        writes = [{"action": a, "keys": sorted(k)}
                  for a, k in sorted(self.writes.get(slug, {}).items())]
        return reason == "passed", reason, detail, writes

    def words(self) -> str:
        """The words of the turn, for the input hash only."""
        return self._words


def _skill_run_notes(facts: Mapping[str, _LoadedSkill], skills: Sequence[Any],
                     run_ctx: Mapping[str, Any], run_key: str, *,
                     words: str, pack_tools: Iterable[str] = ()) -> SkillRunNotes | None:
    """The notes of the run, or None. Only the skills that the run holds at
    the end of the apply count (*skills*, after ``_skills_present`` and
    ``_keep_skill_packs``). With no fact (the flag is off, or no skill
    loaded), it reads nothing and gives None.

    The org is the bound tenant, and the member is the verified member of
    the run (``executor._verified_run_member``), never a request field (R5).
    The run id and the chat come from the server-side run context. *words*
    is ``PackTurn.words``: the pack read of the executor takes it from
    ``assistant_profile.sent_words`` at the start of the run, before any
    steer, so it is the sent text of the member. That is the ``content`` of
    the member's ``chat_message`` row (``routes/agent.py`` saves the same
    text). A turn with no sent text, such as a Continue, gets no notes.

    *pack_tools* is the names of the pack tools of the run. A kept skill with
    no tool among them is ``unobserved`` (:data:`UNOBSERVED`). It never
    raises: a fault gives None, and the turn goes on with no notes."""
    if not facts:
        return None
    try:
        from acb_common.db import current_tenant

        from orchestrator.executor import _verified_run_member

        kept = {str(s.slug): facts[str(s.slug)] for s in skills if str(s.slug) in facts}
        org = str(current_tenant() or "").strip().lower()
        member = _verified_run_member()
        run_id = str(run_ctx.get("run_id") or "").strip()
        if not kept or not org or not member or not run_id or not str(words).strip():
            return None
        thread = str(run_ctx.get("session_id") or "").strip() or None
        held = frozenset(pack_tools)
        blind = [slug for slug, skill in kept.items() if not held & set(skill.actions)]
        return SkillRunNotes(org=org, member=member, run_id=run_id, thread_id=thread,
                             run_key=run_key, skills=kept, words=words, unobserved=blind)
    except Exception as exc:  # the class only
        _log.info("executor.skill_run_unnoted", error_type=type(exc).__name__)
        return None


def _skill_notes_of(ctx: Any, run_key: str, tool: str) -> SkillRunNotes | None:
    """The notes of the run of *ctx* when *tool* is a tool of a loaded skill
    of this run, else None. The notes of another run key never count, so a
    pack tool of a child run notes nothing on the parent."""
    notes = ctx.get(SKILL_RUN_NOTES_KEY) if isinstance(ctx, Mapping) else None
    if isinstance(notes, SkillRunNotes) and notes.run_key == run_key and notes.holds(tool):
        return notes
    return None


def _note_skill_call(notes: SkillRunNotes | None, tool: str, fn: Any, args: tuple[Any, ...],
                     kwargs: dict[str, Any], *, raised: BaseException | None = None,
                     result: Any = None) -> None:
    """Note one call of a pack tool on the notes of its run (F12b). It never
    raises and never changes the call.

    A raise gives its class. A refused answer (``tool_refusals.is_refusal``)
    gives ``Refused``. A card of this call that the member declined
    (``ask_tools.last_card_outcome``) gives ``card_declined``. A write tool
    (no read-only mark in the risk registry) adds its key names: the names
    of the arguments of the owner's signature, never a value.

    ⚠️ The limit. The hook sees only calls that go through ``_pack_tool``.
    Some calls leave no signal, so the row reads passed:

    * a call of a tool of the run base: a workflow action,
      ``tool:crm_change_history``, ``tool:crm_search_notes``, or a door-op
      tool that the member holds;
    * a call that a pack refusal answers before the hook: held in the turn,
      the batch write refusal, the pack write refusal, or the refusal of an
      argument shape.

    A run that ends in an error or a stop also writes passed rows. A RESUME
    run that keeps its run_id can write passed rows twice. F12c reads failed
    rows only, so a passed row never blocks a repair. A skill with no tool
    among the pack tools of the run gets :data:`UNOBSERVED` in the detail of
    its passed row."""
    if notes is None:
        return
    try:
        from acb_common.tool_refusals import is_refusal
        from acb_skills.ask_tools import CARD_DECLINED, last_card_outcome
        from acb_skills.subagent_contract import DISPLAY_TOOLS, annotated_read_only

        error = type(raised).__name__ if raised is not None else (
            "Refused" if is_refusal(result) else None)
        declined = raised is None and last_card_outcome() == CARD_DECLINED
        keys = (None if tool in DISPLAY_TOOLS or annotated_read_only(tool)
                else tuple(str(k) for k in _call_args(fn, args, kwargs)))
        notes.saw(tool, error=error, declined=declined, keys=keys)
    except Exception as exc:  # the class only: the call answers as before
        _log.debug("executor.skill_run_note_failed", error_type=type(exc).__name__)


def _clear_card_outcome(notes: SkillRunNotes | None) -> None:
    """Before a call of a tool of a loaded skill, forget the outcome of an
    earlier card of this frame, so a call with no card reads none."""
    if notes is None:
        return
    with contextlib.suppress(Exception):
        from acb_skills.ask_tools import clear_card_outcome

        clear_card_outcome()


async def _write_skill_runs(notes: SkillRunNotes) -> int:
    """Write one ``live`` row for each loaded skill of the run (F12b), and
    answer the count. Best effort: it never raises.

    It reads the flag again, so a flip to off during the run writes nothing.
    It opens ``tenant_session()`` on the bound tenant, as
    :func:`_turn_with_skills` does, and the bound tenant must be the org of
    the notes. ``skill_runs.record_live`` hashes the words with the org of
    the session and keeps the hash only. A refused row (``SkillRunError``,
    ``DuplicateFailure``) skips that row. Any other fault ends the write. A
    log line names a class or a count, never a word of the turn."""
    written = 0
    try:
        from acb_common.db import current_tenant, tenant_session
        from gateway.studio import skill_runs

        if str(current_tenant() or "").strip().lower() != notes.org or not _skill_repair_open():
            return 0
        async with tenant_session() as db:
            for slug, skill in notes.skills.items():
                passed, reason, detail, writes = notes.row_of(slug)
                try:
                    await skill_runs.record_live(
                        db, notes.member, skill.skill_id, version=skill.version,
                        passed=passed, reason=reason, body_key=skill.body_key,
                        input_text=notes.words(), run_id=notes.run_id,
                        thread_id=notes.thread_id, detail=detail,
                        writes=skill_runs.safe_writes(writes))
                    written += 1
                except skill_runs.SkillRunError as exc:
                    _log.info("executor.skill_run_skipped", skill=skill.skill_id,
                              error_type=type(exc).__name__)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("executor.skill_run_unwritten", error_type=type(exc).__name__)
        return written
    _log.info("executor.skill_run_written", rows=written)
    return written


def schedule_skill_runs(applied: Any) -> asyncio.Task[Any] | None:
    """Start the write of the rows of the run of *applied* (a
    :class:`PackApply`), and answer its task, or None. A run with no notes
    starts nothing. The task runs on a copy of this context, so the bound
    tenant stays. It never raises, so the end of a run never fails here."""
    notes = getattr(applied, "skill_notes", None)
    if not isinstance(notes, SkillRunNotes):
        return None
    try:
        task = asyncio.get_running_loop().create_task(_write_skill_runs(notes))
    except Exception as exc:  # no loop: no row
        _log.info("executor.skill_run_unwritten", error_type=type(exc).__name__)
        return None
    _SKILL_RUN_TASKS.add(task)
    task.add_done_callback(_SKILL_RUN_TASKS.discard)
    return task


def _skills_present(
    skills: Sequence[Any], holds: Iterable[str], agent: Any, *, agent_name: str | None,
    tool_scope: list[str] | None, agent_config: dict[str, Any] | None,
    disabled: frozenset[str],
) -> tuple[Any, ...]:
    """The skill packs of *skills* whose every tool the run holds.

    A skill pack adds no tool of its own. Its tools are the agent's own
    tools, the platform tools that the injection adds
    (:func:`_pack_base_names`), or the tools of a code pack of this turn
    (*holds*). TOF-1b: a code pack that a skill brings is one of them. A
    skill that names a tool the run does not hold fails to load, so Mira
    gets no rule for a tool that she cannot call."""
    if not skills:
        return ()
    present = set(holds) | _pack_base_names(
        agent, agent_name=agent_name, tool_scope=tool_scope,
        agent_config=agent_config, disabled=disabled)
    kept: list[Any] = []
    for found in skills:
        missing = sorted(set(found.tools) - present)
        if missing:
            _log.info("executor.skill_refused", skill=found.skill_id,
                      version=found.version, reason="tool_absent", tools=missing)
            continue
        kept.append(found)
    return tuple(kept)


def _skill_pack_map(
    turn: Any, agent: Any, *, agent_name: str | None, tool_scope: list[str] | None,
    agent_config: dict[str, Any] | None, disabled: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    """TOF-1b. The code packs that each skill of *turn* brings, by the slug of
    the skill (``skill_packs.pack_needs``), or an empty map. The held tools
    are the run base (:func:`_pack_base_names`), so a tool that the base
    holds brings no pack. A turn with no skill reads nothing."""
    skills = tuple(getattr(turn, "skills", ()) or ())
    if not skills:
        return {}
    from acb_skills import skill_packs

    return skill_packs.pack_needs(skills, _pack_base_names(
        agent, agent_name=agent_name, tool_scope=tool_scope,
        agent_config=agent_config, disabled=disabled))


def _keep_skill_packs(
    builds: list[PackBuild], skills: tuple[Any, ...], holds: set[str], *,
    skill_map: Mapping[str, Sequence[str]], layers: Mapping[str, str],
    pool: Sequence[Any], agent: Any, agent_name: str | None,
    tool_scope: list[str] | None, agent_config: dict[str, Any] | None,
    disabled: frozenset[str],
) -> tuple[list[PackBuild], tuple[Any, ...], set[str]]:
    """TOF-1b. The builds, the kept skills and the held names, less each build
    of the layer "skill" (``Choice.layers``) that no kept skill needs.

    :func:`_skills_present` can refuse a skill with ``tool_absent``, for
    example when the guarded build dropped its tool. Then a pack that came
    only for that skill leaves, so the run is as it was without the skill.
    The need is the full map of ``skill_packs.pack_needs`` (*skill_map*). The
    kept skills are checked again after each removal, because a removed pack
    may hold a tool of one. With no map, the inputs come back as they were."""
    while skill_map:
        needed = {p for s in skills for p in skill_map.get(str(s.slug), ())}
        gone = {b.slug for b in builds
                if layers.get(b.slug) == "skill" and b.slug not in needed}
        if not gone:
            break
        builds = [b for b in builds if b.slug not in gone]
        holds = {_tool_name(t) for t in pool} | {
            _tool_name(t) for b in builds for t in b.tools}
        skills = _skills_present(
            skills, holds, agent, agent_name=agent_name, tool_scope=tool_scope,
            agent_config=agent_config, disabled=disabled)
    return builds, skills, holds


async def _evict_unheld_skill_packs(turn: Any, choice: Any, builds: Sequence[PackBuild]) -> Any:
    """TOF-1b. *choice*, with each pack of the layer "skill" that came out of
    the sticky state and that the run holds no tool of back in ``evicted``.

    ``tool_packs.choose`` takes a pack that a skill brings back out of
    ``evicted``, so ``drop_sticky`` keeps its state. When its build then gives
    no tool, or :func:`_keep_skill_packs` removes it, the run is as it was
    without the skill: the pack leaves the sticky state, and
    ``PackRun.gone`` names it, so ``left_line`` tells the model not to call
    its tools. A pack of the layer "skill" that is in the sticky state of the
    turn came out of ``evicted``, because a live sticky pack keeps the layer
    "sticky". With no such pack, *choice* comes back as it was."""
    from acb_skills import tool_packs

    built = {b.slug for b in builds}
    stuck = {str(getattr(e, "slug", "")) for e in getattr(turn, "sticky", ()) or ()}
    lost = tuple(slug for slug in choice.slugs if choice.layers.get(slug) == "skill"
                 and slug in stuck and slug not in built)
    if not lost:
        return choice
    await tool_packs.drop_sticky(turn, lost)
    return dataclasses.replace(choice, evicted=(*choice.evicted, *lost))


def _log_skills_loaded(skills: Sequence[Any], skipped: Sequence[str]) -> None:
    """One ``executor.skill_loaded`` line for each skill pack of the run, in
    load order (§11.1, "model-visible means logged"). It names the skill,
    the version, its hash, its tools, the token cost and the prefix effect,
    and never the text of the skill."""
    for place, found in enumerate(skills):
        _log.info(
            "executor.skill_loaded", skill=found.skill_id, version=found.version,
            content_hash=found.content_hash, tools=list(found.tools),
            token_cost=found.token_cost, changes_prefix=found.changes_prefix,
            place=place)
    if skipped:
        _log.info("executor.skills_skipped", skills=list(skipped), reason="cap")


def _log_skills_refused(refused: Sequence[tuple[Any, str, Sequence[str]]]) -> None:
    """TOF-1b. One ``executor.skill_refused`` line for each skill that could
    not bring its packs (``Choice.skills_refused``). It names the skill, the
    version, the reason (``pack_cap`` or ``pack_off``) and the packs that it
    lacked, and never the text of the skill."""
    for found, reason, packs in refused:
        _log.info("executor.skill_refused", skill=getattr(found, "skill_id", ""),
                  version=getattr(found, "version", None), reason=reason,
                  packs=list(packs))


def _loader_agent_ok(agent: Any) -> bool:
    """True when *agent* is a native MAF ``Agent``, the only shape that
    :func:`_offer_pack_loader` serves."""
    try:
        from agent_framework import Agent
    except ImportError:  # pragma: no cover - MAF ships with the platform
        return False
    return isinstance(agent, Agent)


async def _mcp_may_attach(
    agent: Any, agent_name: str, *, no_egress: bool, is_authorized: Any, held_untrusted: bool,
) -> bool:
    """True when :func:`_inject_mcp_servers` can put an MCP tool in this run
    (OA-7i lookahead). The injection runs AFTER the packs, so the plan of the
    loader asks first. The same early exits, then the registry rows, listed
    with no token refresh and no header read (``enabled_meta``). A row that the run could use counts, so the answer
    errs toward True. A fault reads as True (fail closed): then the
    specialist of a mail owner stays, as it did before OA-7i. The rows and
    their filter are those of :func:`_attachable_rows`, and the row rule runs
    inside the ``try``, so a fault of the rule (a grant that raises) also
    reads as True. It reads the whole list: the grant is a pure test, and
    the answer is the answer of a stop at the first row."""
    from acb_common.db import current_tenant

    if no_egress or held_untrusted or agent_name in UNTRUSTED_INPUT_AGENTS:
        return False
    try:
        rows = await _attachable_rows(agent, agent_name, current_tenant(), is_authorized)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("executor.pack_mcp_lookahead_failed", error=type(exc).__name__)
        return True
    return bool(rows)


def _row_may_attach(srv: Any, agent_name: str, is_authorized: Any, *,
                    log_stdio: bool = False, parked: bool = False) -> bool:
    """THE one row rule of a lookahead on the registry metadata (CSY-4 §3.4,
    HRD-1d, task 3866): a row that is enabled, in scope for *agent_name*, not
    stdio (D85), on an HTTP transport with a URL, and that the run's grant
    allows. It is the filter that :func:`_inject_mcp_servers` applies through
    :func:`_keep_mcp_server` and :func:`_maf_servers`. Each ``enabled_meta``
    lookahead reads it through :func:`_attachable_rows`, and
    :func:`_reconnect_rows` reads it with *parked*. No other code repeats it.
    With *log_stdio* the scope and the stdio test go through
    :func:`_keep_mcp_server`, which logs ``mcp.stdio_refused``. It logs
    nothing else.

    A falsy ``enabled`` refuses the row, as both rules before the merge did
    for ``False``. A row with no ``enabled`` field counts as enabled, because
    ``StoredServer`` always carries the field. *parked* is a row of
    ``needs_reconnect_meta``: its ``enabled`` is False by construction
    (``StoredServer.enabled`` is ``status == connected``), and it is named,
    never attached. So *parked* skips the ``enabled`` test, and every other
    test applies."""
    from orchestrator import _maf_mcp

    if not parked and not getattr(srv, "enabled", True):
        return False
    if log_stdio:
        if not _keep_mcp_server(srv, agent_name):
            return False
    elif not _in_scope(list(srv.agent_scope), agent_name) or srv.transport == "stdio":
        return False
    if srv.transport not in _MAF_HTTP_TRANSPORTS or not srv.url:
        return False
    return is_authorized is None or bool(is_authorized(_maf_mcp.mcp_service(str(srv.name))))


async def _attachable_rows(agent: Any, agent_name: str, org: Any, is_authorized: Any, *,
                           log_stdio: bool = False) -> list[Any]:
    """THE one lookahead of the registry rows that may attach to this run.

    The early exits of :func:`_inject_mcp_servers` (no org, not a native MAF
    agent, ``MAF_MCP_ORGS`` off) give an empty list and read nothing. Else
    one ``enabled_meta`` read (no header, no token refresh, inside
    ``tenant_session(org)``), less each row that :func:`_row_may_attach`
    refuses. It raises on a read fault, so each caller picks its own fail
    side. Four callers: the OA-7i lookahead (:func:`_mcp_may_attach`), the
    HRD-1d held-back servers (:func:`_note_withheld_servers`), the CSY-5
    ``mail`` line (:func:`_note_mail_skipped`) and the CSY-4 server words
    (:func:`_with_server_words`). The lookahead and the server words check
    their own exits first: ``no_egress``, an agent that reads stranger text,
    a held mail pack. The two notes run only at the skip of a held mail pack,
    and they attach nothing."""
    from orchestrator import _maf_mcp

    if not org or not _maf_mcp.is_native_maf_agent(agent):
        return []
    if not _maf_mcp.maf_mcp_org_allowed(org):
        return []
    from gateway.connections import mcp as mcp_registry

    rows = await mcp_registry.enabled_meta(str(org))
    return [srv for srv in rows
            if _row_may_attach(srv, agent_name, is_authorized, log_stdio=log_stdio)]


async def _mail_mark_set(run_ctx: Mapping[str, Any]) -> bool:
    """CSY-4: True only when the chat's mail mark reads ``MARK_MARKED``.

    The eviction FAILS CLOSED on the mark: clear, unknown (a Redis fault) and
    any raise read False, and then no pack leaves. So it reads
    ``session_mark_state`` and compares to ``MARK_MARKED``. Never use a reader
    that counts unknown as marked here: such a reader fails OPEN for an
    eviction. *run_ctx* is the server-bound run context (R5)."""
    try:
        return await session_mark_state(run_ctx, fail_closed=True) == MARK_MARKED
    except Exception as exc:  # the class only
        _log.warning("executor.pack_mail_mark_unread", error=type(exc).__name__)
        return False


def _server_named_field(choice: Any) -> dict[str, list[str]]:
    """CSY-4: the log field of ``Choice.server_named``, or no field when it is
    empty, so a line of a run with no such eviction keeps its bytes."""
    named = list(getattr(choice, "server_named", ()) or ())
    return {"server_named": named} if named else {}


async def _with_server_words(
    turn: Any, agent: Any, agent_name: str, run_ctx: Mapping[str, Any], is_authorized: Any,
    *, read_only: bool = False,
) -> Any:
    """*turn* with the words of the servers that may attach and the mail mark
    (CSY-4, ``chat_connected_systems.md`` §3.4), or *turn* as it was.

    It acts only while ``MCP_MAIL_PRECEDENCE_ORGS`` names the bound tenant,
    the turn holds a sticky pack of an owner that reads stranger text, and
    the run could get a server at all (not ``no_egress``, not an agent that
    reads stranger text, no mail tool held). Then ONE read of the attachable
    rows (:func:`_attachable_rows`), and, only when the words of the turn
    name one of them, ONE read of the mark (:func:`_mail_mark_set`). A fault
    of the read gives *turn* as it was. ``tool_packs.choose`` applies the
    rule. With the flag off it awaits nothing. *read_only* is the WhatsApp
    turn (G2), which keeps the choice of main."""
    from acb_common.db import current_tenant
    from acb_skills import server_match, tool_packs
    from acb_skills.egress import mcp_mail_precedence_on

    org = current_tenant()
    if read_only or not org or not mcp_mail_precedence_on(org):
        return turn
    if (bool(run_ctx.get("no_egress")) or agent_name in UNTRUSTED_INPUT_AGENTS
            or _holds_untrusted_pack(agent)):
        return turn
    if not any(str(tool_packs.SKILL_FAMILIES.get(str(getattr(e, "slug", "")), {}).get("owner"))
               in UNTRUSTED_INPUT_AGENTS for e in getattr(turn, "sticky", ()) or ()):
        return turn
    try:
        rows = await _attachable_rows(agent, agent_name, org, is_authorized)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("executor.pack_server_words_failed", error=type(exc).__name__)
        return turn
    servers = tuple(sorted(
        (str(srv.name), words) for srv in rows
        if (words := server_match.server_words(str(getattr(srv, "label", "") or ""),
                                               str(srv.name)))))
    if not servers or not server_match.named_servers(getattr(turn, "words", ""), dict(servers)):
        return turn
    return dataclasses.replace(turn, servers=servers,
                               mail_marked=await _mail_mark_set(run_ctx))


async def _pack_available(
    allow: frozenset[str], *, persona: str, bullets: Mapping[str, str],
) -> Callable[[str], bool]:
    """THE one test of the owner of a pack that a run may still add: the test
    of the loader plan (OA-7i) and of the ``find_tools`` candidates (TS-2a).

    It awaits :func:`_studio_member_open` once, and only when ``studio`` is
    in *allow*. The test that it gives then reads no member again. An owner
    passes when its module loads (:func:`_pack_owner_module`), its pack can
    build a tool for this org and member, and the persona holds the owner's
    delegation text exactly once (or has none by design)."""
    from acb_skills import tool_packs

    # STU-G1 and J1-1: the one check of the studio pack filter, read once for
    # the run. A run whose packs hold no ``studio`` reads no member.
    # WS-67 TS-1b: it relies on FOLLOWS (``studio-more`` follows ``studio``).
    studio_ok = "studio" in allow and await _studio_member_open()

    def _available(owner: str) -> bool:
        if _pack_owner_module(owner) is None:
            return False
        # OA-8b: a covered org loads no Projects pack, so the loader cannot
        # reach the owner and the specialist stays (``_drop_pack_covered``).
        if owner == "projects-assistant" and _run_covered(owner):
            return False
        # STU-G1 and J1-1: an org that STUDIO_ORGS lacks, and a member without
        # feature:studio, get an empty studio pack (``_drop_pack_unless_studio``),
        # so the loader cannot offer it.
        if owner == "studio-builder" and not studio_ok:
            return False
        if owner in tool_packs.NO_PERSONA_BULLETS and owner not in bullets:
            return True  # by design the persona has no text of this owner (OA-8a)
        old = str(bullets.get(owner) or "")
        return bool(old) and persona.count(old) == 1

    return _available


async def _plan_pack_loader(
    agent: Any, allow: frozenset[str], held: list[str], *, builds: Sequence[PackBuild],
    current: int, pool: Sequence[Any], persona: str, bullets: Mapping[str, str],
    agent_name: str, no_egress: bool, is_authorized: Any,
    spare: Mapping[str, int] | None = None,
    available: Callable[[str], bool] | None = None,
) -> Any:
    """THE one plan of the loader in a run (OA-7i): ``tool_packs.plan_loader``
    with the facts of this run. Its answer drives the offer
    (:func:`_offer_pack_loader`) AND the removal of a specialist tool and of
    its persona text, so the two cannot disagree.

    An owner can be reached only when the agent is a native MAF ``Agent``, the
    owner's module loads (:func:`_pack_owner_module`), and the persona holds
    the owner's delegation text exactly once. The MCP rule reads the registry
    (:func:`_mcp_may_attach`) and only for a pack of an owner that reads
    stranger text, and only while the run holds none of them. *spare* names
    the packs that can give way to a load, each with its tool count (RX-1,
    ``PackRun.spare``). For the Projects owner the plan reaches through them
    (task 1747).

    *available* is the test of :func:`_pack_available` when the caller made
    it already (TS-2a, the search is on). With None the plan makes it here,
    after the MCP lookahead, as before TS-2a."""
    from acb_skills import tool_packs

    if not _loader_agent_ok(agent):
        return tool_packs.LoaderPlan()
    removable: dict[str, int] = {}
    for item in pool:
        target = _delegates_to(item)
        if target:
            removable[target] = removable.get(target, 0) + 1
    unheld = tool_packs.loadable(allow, held)
    mcp = False
    reads_mail = any(
        str(tool_packs.SKILL_FAMILIES[slug].get("owner")) in UNTRUSTED_INPUT_AGENTS
        for slug in unheld)
    if len(held) < tool_packs.MAX_PACKS and reads_mail:
        mcp = await _mcp_may_attach(
            agent, agent_name, no_egress=no_egress, is_authorized=is_authorized,
            held_untrusted=any(b.owner in UNTRUSTED_INPUT_AGENTS for b in builds))
    if available is None:
        available = await _pack_available(allow, persona=persona, bullets=bullets)
    return tool_packs.plan_loader(
        allow, held, current=current, removable=removable, mcp=mcp, available=available,
        spare=spare)


# ── Tool packs in the middle of a turn: layer (b) (WS-67 OA-7e, D117) ───────
# ``one_assistant_context.md`` §4.2.2 and §4.2.8. ``load_skill_tools`` lets the
# model load a pack that layer (a) missed. Its tool, its middleware and its
# provider live in ``acb_skills.tool_packs``. This seam puts the provider on
# the view of the run, and gives the middleware :func:`_pack_tools`, the SAME
# guarded builder as layer (a). Only :func:`_apply_tool_packs` with
# ``offer_loader`` calls it, and only ``executor._apply_pack_turn`` passes
# that, so rooms, WhatsApp and delegated runs never get the tool. Fence:
# ``tests/unit/test_load_skill_tools.py``.


async def _build_pack_for_load(
    slug: str, run_ctx: Mapping[str, Any], holds: frozenset[str],
) -> PackBuild | None:
    """The build of one pack for a load in the middle of a turn: the guarded
    builder of layer (a) (:func:`_pack_tools`). *run_ctx* is the run context
    of the call, which the server bound, with this run's key. *holds* names
    each tool of the live list, so the build skips each of them."""
    return await _pack_tools(slug, run_ctx, holds=holds)


async def _search_setup(
    search_on: bool, agent: Any, allow: frozenset[str], loaded: Sequence[str],
    holds: frozenset[str], build_ctx: Mapping[str, Any], *, persona: str,
    bullets: Mapping[str, str],
) -> tuple[Callable[[str], bool] | None, Any]:
    """The test of the owners and the candidates of ``find_tools`` for ONE
    run (WS-67 TS-2a), or ``(None, None)``.

    It acts only when *search_on* (the flag, read by the caller for the bound
    tenant) and the agent is a native MAF ``Agent``, the one shape that
    :func:`_offer_pack_loader` serves. Then it makes :func:`_pack_available`
    once, so the loader plan and the search read the member's Studio access
    once, and :func:`_find_candidates`. With the flag off it awaits nothing,
    so the run is as before TS-2a."""
    if not search_on or not _loader_agent_ok(agent):
        return None, None
    available = await _pack_available(allow, persona=persona, bullets=bullets)
    return available, await _find_candidates(allow, loaded, holds, build_ctx, available)


async def _find_candidates(
    allow: frozenset[str], loaded: Sequence[str], holds: frozenset[str],
    build_ctx: Mapping[str, Any], available: Callable[[str], bool] | None,
) -> Any:
    """The candidates of ``find_tools`` for ONE run (WS-67 TS-2a), or None
    when the setup fails, so the run then gets no search.

    It calls ``tool_packs.setup_candidates`` inside the apply, with
    *build_ctx*: the run context that the server bound, with this run's key
    (``PACK_RUN_KEY``), as layer (a) and ``load_early`` build. A context with
    no key builds nothing, and the tool would ship with an empty catalog.
    The owners are ``PACK_ONLY_OWNERS``, the builder is the guarded builder
    of layer (a), and *available* is :func:`_pack_available`. *allow* is the
    ``allow`` of the apply, so a pack that an admin turned off is never a
    candidate. The log line gives the cost of the setup builds."""
    import time

    from acb_skills import tool_packs

    started = time.perf_counter()
    try:
        found = await tool_packs.setup_candidates(
            allow, loaded, holds, owners=tool_packs.PACK_ONLY_OWNERS,
            build=_build_pack_for_load, ctx=build_ctx, available=available)
    except Exception as exc:  # a search never costs the turn
        _log.error("executor.find_tools_refused", reason="setup_failed",
                   error_type=type(exc).__name__)
        return None
    _log.info("executor.find_tools_setup", packs=list(found.packs), tools=len(found.tools),
              ms=round((time.perf_counter() - started) * 1000, 1))
    return found


def _make_finder(applied: PackApply, candidates: Any) -> Any:
    """THE ``FindTools`` of one run (TS-2a). Its record is the record of the
    apply, the same ``PackRun`` that the loader holds, so the two share
    ``PackRun.lock``. Its builder is the guarded builder of layer (a), and
    its measure is ``_maf_mcp.schema_tokens``.

    SBX-1a (``agent_sandbox.md`` §6.1): its ``on_gap`` is
    ``tool_gap_path.make_gap_hook()``, the hook of the no-match path. With
    ``user_tool_find_enabled`` off it is None, and the finder is as before.
    The import is lazy, because ``tool_gap_path`` imports this module."""
    from acb_skills import tool_packs

    from orchestrator import _maf_mcp, tool_gap_path

    return tool_packs.FindTools(
        candidates=candidates, run_key=applied.run_key, record=applied.record,
        build=_build_pack_for_load, measure=_maf_mcp.schema_tokens,
        on_gap=tool_gap_path.make_gap_hook(),
    )


def _offer_pack_loader(
    agents: list[Any], applied: PackApply, plan: Any, candidates: Any = None,
) -> bool:
    """Put ``load_skill_tools`` on the view of the run (layer (b), OA-7e).

    It offers the packs of *plan* (``tool_packs.LoaderPlan``, from
    :func:`_plan_pack_loader`, OA-7i): each allowed pack that the run does not
    hold and that fits the caps, so the catalog never lists a pack that the
    flag, a toggle or a cap stops. The provider goes on a per-run view
    (``agent_with_providers``) that takes the place of ``agents[0]``, so the
    agent object of another run never changes. Each later step reads
    ``agents`` again, and each view that the injection makes keeps the
    provider. True when the run offers a load.

    TS-2a: with *candidates* (the search is on), the same ONE list puts
    ``tool_packs.FindToolsProvider`` BEFORE ``LoadSkillToolsProvider``. So
    ``find_tools`` is in ``context.tools`` when layer (c) counts the live
    list (``LoadSkillTools.load_early``). With no offer, the list holds the
    finder alone. With None the list is as before TS-2a.
    """
    from acb_skills import tool_packs

    offered = tuple(plan.offered)
    if not offered and candidates is None:
        return False
    try:
        from agent_framework import Agent
    except ImportError:  # pragma: no cover - MAF ships with the platform
        return False
    agent = agents[0] if agents else None
    if not isinstance(agent, Agent):
        _log.warning("executor.pack_loader_refused", reason="agent_shape")
        return False
    from orchestrator._native_run_context import agent_with_providers

    providers: list[Any] = []
    if candidates is not None:
        providers.append(tool_packs.FindToolsProvider(_make_finder(applied, candidates)))
    if offered:
        loader = tool_packs.LoadSkillTools(
            offered=offered, run_key=applied.run_key, record=applied.record,
            build=_build_pack_for_load,
        )
        providers.append(tool_packs.LoadSkillToolsProvider(loader))
    agents[0] = agent_with_providers(agent, providers)
    if candidates is not None:
        _log.info("executor.find_tools_offered", packs=list(candidates.packs),
                  tools=len(candidates.tools), loaded=list(applied.record.slugs))
    if not offered:
        return False
    _log.info("executor.pack_loader_offered", packs=list(offered),
              loaded=list(applied.record.slugs))
    return True


def _holds_untrusted_pack(agent: Any) -> bool:
    """True when *agent* holds a tool of a pack whose owner reads stranger
    text (:data:`UNTRUSTED_INPUT_AGENTS`). Such a run gets no registry MCP
    server, as the owner's own run gets none. It reads the owner, not the
    pack, so ``email-setup`` counts as well as ``email``, and so does a pack
    that stays on for a later turn (OA-7c sticky packs)."""
    for _label, pool in _own_tool_pools(agent):
        for tool in pool:
            owner = getattr(tool, PACK_OWNER_ATTR, None) or getattr(
                getattr(tool, "func", None), PACK_OWNER_ATTR, None)
            if owner in UNTRUSTED_INPUT_AGENTS:
                return True
    return False


def merge_mcp_servers(
    agent: Any, servers: dict[str, dict[str, Any]], *, override: bool,
) -> None:
    """Merge MCP servers into the field the Copilot SDK actually reads.

    ⚠️ **The field MOVED with the 2.0 wrapper (H-181).** The 1.0.0b wrapper
    read ``self._mcp_servers`` and ignored ``_default_options["mcp_servers"]``.
    The 2.0 wrapper is the reverse: it has no ``_mcp_servers`` attribute, and
    ``_build_session_kwargs`` forwards every ``_default_options`` key to
    ``create_session``. ``MetoriteCopilotAgent`` reads the same key. So the
    target is ``_default_options["mcp_servers"]``. Writing the old attribute
    would silently drop every server again, the bug this function fixed once.

    ``override=True`` lets the caller win over what's already there (the DB
    registry outranks a repo's own files); ``False`` only fills gaps.
    Best-effort — never raises.
    """
    if not servers:
        return
    try:
        opts = getattr(agent, "_default_options", None)
        if not isinstance(opts, dict):
            return  # not a Copilot-SDK agent: it has no MCP field to fill
        existing = opts.get("mcp_servers")
        merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        for name, cfg in servers.items():
            if override or name not in merged:
                merged[name] = cfg
        opts["mcp_servers"] = merged
    except Exception:
        pass


#: The transports that the MAF path connects with ``MCPStreamableHTTPTool``.
_MAF_HTTP_TRANSPORTS = frozenset({"http-sse", "http"})


def _str_map(raw: Any) -> dict[str, str]:
    """A JSON object column as ``dict[str, str]``. Anything else is empty."""
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _in_scope(scope: Any, agent_name: str) -> bool:
    """True when a row's ``agent_scope`` names ``*`` or *agent_name*."""
    scope_list: list[Any]
    if isinstance(scope, list):
        scope_list = scope
    elif isinstance(scope, str):
        scope_list = [scope]
    else:
        scope_list = ["*"]
    return "*" in scope_list or agent_name in scope_list


async def _inject_mcp_servers(
    agent: Any,
    agent_name: str,
    *,
    organization_id: str | None = None,
    is_authorized: Any = None,
    opened: list[Any] | None = None,
    member_present: bool = False,
) -> None:
    """Give *agent* the enabled MCP servers of the run's organization.

    Registry MCP is MAF-only (WS-8c, ``agent_architecture.md`` §12.2):

    * **A Copilot agent gets NO registry server** (review round 2 of #869).
      The Copilot CLI connects with its own client, so no URL check at
      register time can stop DNS rebinding there, and no pin, flag or grant
      applies. Each in-scope row is dropped and logged as
      ``mcp.copilot_registry_skipped``, with the name only. Copilot leaves
      with WS-43q, and production held 0 rows. A repo ``.mcp.json`` still
      reaches a Copilot agent through :func:`merge_mcp_servers`.
    * **A native MAF agent** gets one ``MCPStreamableHTTPTool`` for each
      ``http-sse`` server (``orchestrator._maf_mcp``), only when
      ``MAF_MCP_ORGS`` allows the org. With the flag off, it reads no row.
      A server that ``is_authorized("mcp-<name>")`` refuses is left out, and
      the log names it as ``mcp.not_authorized``. Each tool and its client go
      into *opened*, and the run closes them in its ``finally``.

    **The org.** It is *organization_id*, which the executor takes from the
    gateway's server-side stamp, else ``executor._current_run_org()`` on THIS
    event-loop frame. It never comes from tool arguments or the event payload
    (R11). With no org, nothing is read and nothing is injected.
    A registry row always names its org, so there are no org-less "global"
    servers.

    **The card** (H-299, MCP-NC1). *member_present* is True only when a
    member started this run. The executor passes it. Then a tool runs with no
    card, unless its server is set to always confirm. The default is False,
    so a caller that does not say refuses each MCP tool that does not say
    read-only, and each tool that needs a card (``_maf_mcp.McpPermissionGate``).

    **No stdio** (D85). A stdio row starts a process on the host, so both
    paths drop it and log ``mcp.stdio_refused``.

    **The store** (WS-54 IN-4). The servers live in the connection registry,
    which has FORCE RLS, and the headers sit in its encrypted blob. The one
    reader is ``gateway.connections.mcp.servers_for_org``. It opens
    ``tenant_session(org)``, so row level security AND an org predicate bind
    the read, whatever ``ACB_GRAPH_TENANT_BIND`` says. It decrypts the
    headers, and they go to the MCP client as an argument only. An old
    ``mcp_servers`` row of the org moves into the registry on that read.

    Best-effort: a registry fault or a server fault never blocks the run.
    Fences: ``tests/unit/test_maf_mcp_injection.py``,
    ``tests/unit/test_maf_mcp_org_scope_r8.py``,
    ``tests/unit/test_mcp_servers_org_scope.py`` and
    ``tests/unit/test_mcp_registry_r8.py``.
    """
    from orchestrator import _maf_mcp

    _note_mcp_labels([], [], frozenset())  # CSY-10: no label of an older run stays
    _note_mcp_briefs([], [], None)  # CSY-1: no brief of an older run stays
    if agent_name in UNTRUSTED_INPUT_AGENTS:
        # EM-G11d (UMCC): a mail could tell the model to send mailbox data to
        # a server, and no check here can prove that a server cannot fetch.
        _log.info("mcp.untrusted_input_agent_skipped", agent=agent_name)
        return
    if _holds_untrusted_pack(agent):
        # WS-67 OA-7b (§4.2.3): a run that holds the email pack reads mail
        # in its own run, so it gets no server either. Packs load before
        # this step (OA-7c).
        _log.info("mcp.untrusted_pack_skipped", agent=agent_name)
        # HRD-1d: the model can still call a tool of such a server from the
        # history of the chat. Its answer then says why the tool did not run.
        await _note_withheld_servers(
            agent, agent_name, organization_id=organization_id, is_authorized=is_authorized)
        # CSY-5: the model learns WHY the system is off (reason `mail`).
        await _note_mail_skipped(agent, agent_name, organization_id, is_authorized)
        return

    org = organization_id or _run_org_or_none()
    if not org:
        return  # no tenant: the safe subset is EMPTY, never every org's rows

    native = _maf_mcp.is_native_maf_agent(agent)
    if native and not _maf_mcp.maf_mcp_org_allowed(org):
        return  # the flag is off for this org: read nothing

    try:
        # Lazy, as ``_maf_mcp.attach_mcp_tools`` imports the URL guard: the
        # orchestrator runs inside the gateway process.
        from gateway.connections import mcp as mcp_registry

        # IN-8b: an OAuth row that this run cannot use is never refreshed.
        stored = await mcp_registry.servers_for_org(
            str(org), oauth_wanted=_oauth_wanted(agent_name, native, is_authorized),
        )
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.registry_unavailable", error=type(exc).__name__)
        return

    servers = [srv for srv in stored if _keep_mcp_server(srv, agent_name)]
    if native:
        maf_servers = _maf_servers(servers, is_authorized)
        attached: Any = []
        reasons: dict[str, str] = {}
        tools: Any = getattr(agent, "mcp_tools", None)
        if maf_servers:
            reserved = _reserved_tool_names(agent)
            failures: dict[str, str] = {}
            attached = await _maf_mcp.attach_mcp_tools(
                agent, maf_servers, opened if opened is not None else [],
                member_present=member_present,
                on_unauthorized=_mark_unauthorized_for(str(org)),
                on_needs_signin=_mark_needs_signin_for(str(org)),
                failures=failures,
                # Task 3924, review P2: a first-party name never autoloads a server tool.
                reserved=reserved,
            )
            # CSY-10: only a server that attached (after the grant check and
            # the connect) gets a label, so a refused one never shows.
            _note_mcp_labels(servers, attached, reserved)
            tools = getattr(agent, "mcp_tools", None)
            reasons = await _mcp_reasons(str(org), maf_servers, attached, failures, tools, member_present)
        # Task 3866: a row in needs_reconnect is not read for a run, so it is
        # named from a metadata read. Its tools never attach.
        held_names = {str(srv.name) for srv in servers}
        # A row that this read held was named by the connect (``failures``).
        parked = [p for p in await _reconnect_rows(str(org), agent_name, is_authorized)
                  if str(p.name) not in held_names]
        for srv in parked:
            reasons.setdefault(str(srv.name), "reconnect")
        if maf_servers or parked:
            _note_mcp_briefs([*servers, *parked], attached, tools, reasons)
            await _count_left_out(str(org), reasons)
        return

    for srv in servers:
        _log.info("mcp.copilot_registry_skipped", name=srv.name)


async def _note_withheld_servers(
    agent: Any, agent_name: str, *, organization_id: str | None, is_authorized: Any,
) -> None:
    """HRD-1d (task 3719): tell the unknown-tool answer of this run which MCP
    servers the run held back, because it holds a pack that reads mail from
    outside senders (OA-7b).

    The servers are the ones that the injection would try without that pack:
    :func:`_attachable_rows`, which has the same org, flag and native check as
    :func:`_inject_mcp_servers`, then :func:`_row_may_attach`, the one row
    rule. The org is the run's server-side stamp, never a tool argument or
    the member's text. The read is ``enabled_meta``: no header and no OAuth
    refresh. The value goes to
    ``unknown_tool.note_withheld`` as the tool-name prefix of each server
    (``_maf_mcp.tool_prefix``).

    It reads only when the view holds a chat provider
    (``unknown_tool.reads_withheld``), so a step and a later agent of the run
    read nothing. Never raises. A fault leaves no value, and the answer stays
    the answer of HRD-1c."""
    try:
        from acb_skills import unknown_tool

        from orchestrator import _maf_mcp

        if not unknown_tool.reads_withheld(agent):
            return
        org = organization_id or _run_org_or_none()
        # No org, no native agent or the flag off: an empty list, and no read.
        # An empty list then changes nothing (``note_withheld`` keeps no value).
        rows = await _attachable_rows(agent, agent_name, org, is_authorized)
        names = [str(srv.name) for srv in rows]
        if unknown_tool.note_withheld(
                agent, [_maf_mcp.tool_prefix(n) for n in names],
                reason=unknown_tool.WITHHELD_MAIL_REASON):
            _log.info("mcp.withheld_noted", agent=agent_name, servers=len(names))
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.withheld_unavailable", error=type(exc).__name__)


#: Tools of the platform that no MCP server may take the look of.
_FIRST_PARTY_LOADERS = frozenset({"load_skill_tools", "find_tools"})


def _reserved_tool_names(agent: Any) -> frozenset[str]:
    """CSY-10: the first-party tool names of the run: what the agent holds
    before any server attaches, the registered platform surface and the
    loader tools. A name of this set is never labelled as an MCP tool."""
    try:
        from orchestrator import _maf_mcp

        held = _maf_mcp._names_held(agent)
    except Exception:  # a fault gives the platform surface alone
        held = set()
    packs: set[str] = set()
    try:
        from acb_skills.skill_families import SKILL_FAMILIES

        packs = {n for fam in SKILL_FAMILIES.values() for n in fam["tools"]}
    except Exception:  # the platform surface alone
        packs = set()
    return frozenset(held | _registered_platform_surface() | packs | _FIRST_PARTY_LOADERS)


async def _note_mail_skipped(agent: Any, agent_name: str, organization_id: str | None,
                             is_authorized: Any) -> None:
    """CSY-5, reason ``mail``: a run that holds an email pack reads no server, so the
    registry read never ran. When the flag is on, one metadata read (``enabled_meta``:
    no header, no token refresh, inside the org binding) names the servers that this
    run could have used, and each gets the fixed ``mail`` line. A server that the
    member's grant refuses gets no line. A fault never blocks the run."""
    try:
        from acb_skills import server_brief

        from orchestrator import _maf_mcp

        org = organization_id or _run_org_or_none()
        if (not org or not _maf_mcp.is_native_maf_agent(agent)
                or not _maf_mcp.maf_mcp_org_allowed(org)
                or not _maf_mcp.connected_systems_org_allowed(org)):
            return
        rows = await _attachable_rows(agent, agent_name, org, is_authorized, log_stdio=True)
        notes = []
        for srv in rows:
            notes.append((str(srv.name), server_brief.reason_line(str(srv.label or srv.name), str(srv.name), "mail")))
        server_brief.set_attached([], notes)
        await _count_left_out(str(org), {n: "mail" for n, _ in notes})
    except Exception as exc:
        _log.warning("mcp.mail_notes_unavailable", error=type(exc).__name__)


async def _reconnect_rows(org: str, agent_name: str, is_authorized: Any) -> list[Any]:
    """Task 3866, CSY-5: the servers of *org* in ``needs_reconnect`` that this
    run may name, as metadata only. The row rule is :func:`_row_may_attach`
    with *parked* (such a row reads ``enabled`` False) and *log_stdio*, as
    :func:`_note_mail_skipped` reads it. Only an org with
    ``CONNECTED_SYSTEMS_ORGS`` on. Never attached. A fault gives none and
    never blocks the run."""
    try:
        from orchestrator import _maf_mcp

        if not _maf_mcp.connected_systems_org_allowed(org):
            return []
        from gateway.connections import mcp as mcp_registry

        return [srv for srv in await mcp_registry.needs_reconnect_meta(org)
                if _row_may_attach(srv, agent_name, is_authorized, log_stdio=True, parked=True)]
    except Exception as exc:  # the class only
        _log.warning("mcp.reconnect_notes_unavailable", error=type(exc).__name__)
        return []


async def _count_left_out(org: str, reasons: dict[str, str]) -> None:
    """CSY-8: add ONE to the daily count of each server and reason that left a server out
    of this turn. The reasons come from the closed list of CSY-5, so a turn is counted
    once for each server. Names and counts only. A fault never blocks the run."""
    try:
        if not org or not reasons:
            return
        from acb_common import mcp_use

        for name, reason in reasons.items():
            await mcp_use.count_left_out(org, str(name), str(reason))
    except Exception as exc:  # the class only
        _log.warning("mcp_use.counter_fault", op="count_left_out", error=type(exc).__name__)


async def _mcp_reasons(org: str, maf_servers: list[Any], attached: Any, failures: dict[str, str],
                       tools: Any, member_present: bool) -> dict[str, str]:
    """CSY-5: ``{server name: reason}`` for each server of this run that is out of reach or
    asks a card, from the closed list of ``server_brief.REASONS``. Only for an org with
    ``CONNECTED_SYSTEMS_ORGS`` on. A server that the grant refused never reached here
    (:func:`_maf_servers` dropped it), so it gets no line. Never raises."""
    try:
        from orchestrator import _maf_mcp

        if not _maf_mcp.connected_systems_org_allowed(org):
            return {}
        out = {str(n): r for n, r in failures.items() if r in ("unreachable", "reconnect")}
        names = {str(n) for n in (attached or [])}
        from acb_skills.egress import session_is_marked
        from acb_skills.permission_policy import MCP_READ_ONLY, MCP_READ_ONLY_KEY, decide

        marked = bool(await session_is_marked())
        by_name = {str(getattr(t, "name", "")): t for t in tools or []}
        for srv in maf_servers:
            if str(srv.name) not in names:
                continue
            meta = getattr(by_name.get(str(srv.name)), "__dict__", {}).get("_ws8c_tool_meta") or {}
            if not meta:
                continue

            def needs_card(hints: Any, after_mark: bool) -> bool:
                _ok, code, _ = decide({
                    "tool_name": f"{srv.name}_x", "mcp_tool": True, "mcp_annotations": hints,
                    "mcp_always_confirm": srv.always_confirm, "mcp_read_only_key": srv.read_only_key,
                    # MCP-NC1: a member's run needs no card, so the same fact as the gate.
                    "mcp_member_present": bool(member_present),
                    "after_untrusted_answer": after_mark,
                })
                return code not in (MCP_READ_ONLY, MCP_READ_ONLY_KEY)

            hints = [getattr(m, "hints", None) for m in meta.values()]
            if not member_present and all(needs_card(h, marked) for h in hints):
                out[str(srv.name)] = "no_card"
            elif marked and any(needs_card(h, True) and not needs_card(h, False) for h in hints):
                out[str(srv.name)] = "asks_each_call"
        return out
    except Exception as exc:
        _log.warning("mcp.reasons_unavailable", error=type(exc).__name__)
        return {}


def _note_mcp_briefs(servers: list[Any], attached: Any, tools: Any, reasons: dict[str, str] | None = None) -> None:
    """CSY-1: record the brief of each server that attached (*attached* is what
    ``attach_mcp_tools`` returned), for the per-turn "Connected systems" block
    (``acb_skills.server_brief``). It reuses the rows that the read gave. The
    remote tool names come from the tool's own record. CSY-5: *reasons* adds one
    fixed line for a server that is out of reach or asks a card. CSY-6: the
    server's own ``instructions`` come from the tool that THIS run connected
    (``_maf_mcp.server_instructions``), never from the row. A fault never
    blocks the run."""
    try:
        from acb_skills import server_brief

        from orchestrator._maf_mcp import server_instructions

        names = {str(n) for n in (attached or [])}
        remote: dict[str, list[str]] = {}
        said: dict[str, str | None] = {}
        for tool in tools or []:
            meta = getattr(tool, "__dict__", {}).get("_ws8c_tool_meta") or {}
            remote[str(getattr(tool, "name", ""))] = [str(k) for k in meta]
            said[str(getattr(tool, "name", ""))] = server_instructions(tool)
        briefs = [
            (str(s.name), server_brief.server_brief(
                str(s.label or s.name), str(s.name), str(getattr(s, "description", "") or ""),
                remote.get(str(s.name), []), said.get(str(s.name))))
            for s in servers if s.name and str(s.name) in names
        ]
        notes = [
            (str(s.name), server_brief.reason_line(str(s.label or s.name), str(s.name), reasons[str(s.name)]))
            for s in servers if s.name and reasons and str(s.name) in reasons
        ]
        server_brief.set_attached(briefs, notes)
    except Exception as exc:
        _log.warning("mcp.briefs_unavailable", error=type(exc).__name__)


def _note_mcp_labels(servers: list[Any], attached: Any, reserved: frozenset[str]) -> None:
    """CSY-10: replace the ``{name: label}`` of this run with the servers that
    attached (*attached* is what ``attach_mcp_tools`` returned), so the
    translator can stamp each MCP tool start with its server's label
    (``event_translator.set_mcp_labels``). It reuses the rows that the read
    gave, so it adds no registry read. A fault never blocks the run."""
    try:
        from orchestrator import event_translator

        names = {str(n) for n in (attached or [])}
        labels = {str(s.name): str(s.label) for s in servers
                  if s.name and s.label and str(s.name) in names}
        event_translator.set_mcp_labels(labels, reserved)
    except Exception as exc:
        _log.warning("mcp.labels_unavailable", error=type(exc).__name__)


def _oauth_wanted(agent_name: str, native: bool, is_authorized: Any) -> Any:
    """WS-54 IN-8b: the filter that ``servers_for_org`` applies to an OAuth
    row BEFORE its refresh. It is the filter that this module applies after
    the read (the scope, the transport and the grant), and a Copilot agent
    wants no OAuth row, because only MAF connects one. It logs nothing: the
    log lines stay with the filters after the read (review of #1011, P2 1)."""
    from orchestrator import _maf_mcp

    def _want(srv: Any) -> bool:
        if not native or not _in_scope(list(srv.agent_scope), agent_name):
            return False
        if srv.transport not in _MAF_HTTP_TRANSPORTS or not srv.url:
            return False
        return is_authorized is None or bool(is_authorized(_maf_mcp.mcp_service(str(srv.name))))

    return _want


def _mark_unauthorized_for(org: str) -> Any:
    """WS-54 IN-8b: the callback for a 401 on the connect of an OAuth server.
    It marks the row of *org*, the run binding, never request input, and it
    says whether the registry refreshed the token in this run
    (``integrations_console.md`` §3.6.7)."""

    async def _mark(server: Any) -> None:
        from gateway.connections import mcp as mcp_registry

        await mcp_registry.mark_unauthorized(
            org, str(server.name), dict(server.headers),
            refreshed=getattr(server, "oauth_refreshed", False) is True,
        )

    return _mark


def _mark_needs_signin_for(org: str) -> Any:
    """Task 3866: the callback for a 401 or 403 with an OAuth challenge on the
    connect of a row with no sign-in. It marks the row of *org*, the run
    binding, never request input (``integrations_console.md`` §3.6.7)."""

    async def _mark(server: Any) -> None:
        from gateway.connections import mcp as mcp_registry

        await mcp_registry.mark_needs_signin(org, str(server.name), str(server.url))

    return _mark


def _run_org_or_none() -> str | None:
    """``executor._current_run_org()``, or None when it cannot resolve."""
    try:
        from orchestrator.executor import _current_run_org
        return _current_run_org()
    except Exception:
        return None


def _keep_mcp_server(server: Any, agent_name: str) -> bool:
    """True when *server* is in scope for *agent_name* and is not stdio (D85)."""
    if not _in_scope(list(server.agent_scope), agent_name):
        return False
    if server.transport == "stdio":
        _log.warning("mcp.stdio_refused", name=server.name)
        return False
    return True


def _maf_servers(servers: list[Any], is_authorized: Any) -> list[Any]:
    """The ``http-sse`` servers that the run's authority allows, for MAF."""
    from orchestrator import _maf_mcp

    out: list[Any] = []
    for srv in servers:
        if srv.transport not in _MAF_HTTP_TRANSPORTS or not srv.url:
            _log.info("mcp.transport_skipped", name=srv.name, transport=srv.transport)
            continue
        if is_authorized is not None and not is_authorized(
            _maf_mcp.mcp_service(str(srv.name)),
        ):
            _log.info("mcp.not_authorized", name=srv.name)
            continue
        out.append(_maf_mcp.McpServer(
            name=str(srv.name), url=str(srv.url), headers=_str_map(srv.headers),
            # H-300: the admin's "always confirm" switch. A row with no key
            # reads as false, so the gate applies the rule of item 33
            # (MCP-NC1): no card in a verified member's run in Metorite, and
            # the H-299 refusal of a write with no member and on WhatsApp.
            always_confirm=getattr(srv, "always_confirm", False) is True,
            # MCP-C2: the admin's read-only key, as the registry parsed it
            # once (``StoredServer.read_only_key``). Nothing parses it again.
            read_only_key=getattr(srv, "read_only_key", False) is True,
            # IN-8b: an OAuth row, and whether this read refreshed its token.
            oauth=getattr(srv, "oauth", False) is True,
            oauth_refreshed=getattr(srv, "refreshed", False) is True,
            # IN-8c (D98): the admin that an OAuth row acts as, for the card.
            acts_as=(str(getattr(srv, "connected_by", None) or "")
                     if getattr(srv, "oauth", False) is True else ""),
            # CSY-1: the label and the admin's description, for the brief.
            label=str(getattr(srv, "label", "") or ""),
            description=str(getattr(srv, "description", "") or ""),
        ))
    return out


async def _close_mcp_tools(opened: list[Any]) -> None:
    """Close each MCP tool and client that one run opened. Never raises."""
    if not opened:
        return
    from orchestrator._maf_mcp import close_mcp_tools

    await close_mcp_tools(opened)
