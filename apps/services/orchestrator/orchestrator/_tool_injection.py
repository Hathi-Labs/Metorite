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
import functools
import os
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
    # ⚠️ D85: the floor NAMES the two shell tools, and a SHARED agent still
    # does not get them. `_withheld_shell_tools` takes every SHELL_TOOLS
    # member away from a shared agent until the sandbox broker covers it.
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
    if not raw:
        return frozenset()
    if not isinstance(raw, list):
        _log.warning("executor.floor_opt_out_not_a_list", agent=agent_name)
        return frozenset()
    names = {str(n) for n in raw}
    refused = names - FLOOR_OPT_OUT_ALLOWED
    if refused:
        _log.warning(
            "executor.floor_opt_out_ignored", agent=agent_name,
            entries=sorted(refused),
            hint="only a floor or workflow tool, and never ask_questions or "
                 "emit_generative_ui",
        )
    return frozenset(names & FLOOR_OPT_OUT_ALLOWED)


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
    the CLI instead of into it. So this is ``False`` for every agent, and a
    shared agent's CLI shell is always refused today.
    """
    del agent_name, organization_id
    return False


#: Agents that D85 leaves as they are, by name. ``metorite`` is the root dev
#: agent (repo-root ``agents.py``). It edits platform code and runs the tests
#: through its Copilot CLI shell, with its own ``approve_all`` (H-211). Its root
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

    * A ``personal`` agent. The owner left personal agents out of scope
      (HANDOFF H-225 asks for the same rule there).
    * A name in :data:`_D85_OWNER_PENDING`: the root dev agent, which only a
      first-party admin may run (owner, 2026-10-03, §15.4).

    Every other agent is in scope: ``shared`` (the default when the config has
    no ``sharing`` block), ``team``, a config that does not parse, and
    ``agent_config=None``. So a caller that forgets the config fails closed.
    """
    if agent_name in _D85_OWNER_PENDING:
        return True
    try:
        from acb_skills.manifest import AgentManifest
        instancing = AgentManifest.from_config(
            agent_config, name=agent_name,
        ).sharing.instancing
    except Exception:  # a config that does not parse is shared
        instancing = "shared"
    return instancing == "personal"


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
    these tools for it on 2026-10-03 until the sandbox covers it.

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


def _withhold_egress_from_agent(agent: Any) -> list[str]:
    """Take every egress tool and MCP server out of ONE per-run agent (H-236).

    The factory built this agent object for this run, as every injection site
    assumes. The lists are filtered in place, as ``_apply_own_tool_scope``
    does. Returns the names it took out. The MCP servers of a MAF agent
    (``mcp_tools``) leave on the guarded view (:func:`_with_egress_guard`).
    """
    removed: list[str] = []

    def _filter(pool: Any) -> None:
        if not isinstance(pool, list):
            return
        kept = [t for t in pool if not _is_egress(t)]
        removed.extend(_tool_name(t) for t in pool if _is_egress(t))
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


def _with_egress_guard(agent: Any) -> Any:
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

    view = agent_with_providers(agent, [EgressGuardProvider()])
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
    narrow it (a disabled family, or the D85 withheld shell tools).
    """
    try:
        from acb_skills.skill_families import SKILL_FAMILIES
    except ImportError:
        return set(_CORE_STANDARD_TOOL_NAMES)
    return set(_CORE_STANDARD_TOOL_NAMES) | {
        name for fam in SKILL_FAMILIES.values() for name in fam["tools"]
    }


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
    that :func:`_withheld_shell_tools` keeps from a shared agent. An unscoped
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
        from acb_skills.skill_families import SKILL_FAMILIES  # noqa: PLC0415
    except ImportError:
        return base  # registry unavailable — settings cannot apply safely
    # Only families that are real, not the core floor, and carry static tools
    # can narrow the injected set. ('apps' is dynamic — the API refuses it;
    # 'workflows' tools are appended per-agent, and _inject_agent_tools also
    # honors the toggle at that append site.)
    relevant = {
        slug for slug in disabled_families
        if slug in SKILL_FAMILIES
        and not SKILL_FAMILIES[slug].get("core")
        and SKILL_FAMILIES[slug].get("tools")
    }
    if not relevant:
        return base  # nothing narrowing ⇒ byte-identical to today (rule 3)
    enabled_tools = set(_CORE_STANDARD_TOOL_NAMES)
    for slug, fam in SKILL_FAMILIES.items():
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
    """*instructions* with the attachment failure rule, once.

    For a native MAF agent, which reads no Copilot addendum. Only an agent
    that holds ``read_attachment`` gets it, and the rule itself is the
    marker, so a second injection adds nothing (KV-cache stable).
    """
    from acb_skills.addendum import ATTACHMENT_FAILURE_RULE  # noqa: PLC0415
    if not holds_reader or ATTACHMENT_FAILURE_RULE in instructions:
        return instructions
    return instructions + "\n\n" + ATTACHMENT_FAILURE_RULE


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
    carry. The MCP gate passes ``mcp_tool`` and the server's
    ``mcp_annotations`` (H-299), so ``decide()`` applies its MCP rule. They
    are set after the argument mapping, so a tool argument cannot write them.
    A caller that needs a fail-closed answer must not trust ``gate_error``:
    the MCP gate shows a card for every code but ``mcp_read_only``.
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

    # Mid-run steer (docs/multiplayer/README.md §4.6): this wrapper is the one
    # tool boundary that exists on EVERY tier — the executor's Tier-2 shim never
    # runs on the native-streaming path, and the Copilot SDK has its own hooks —
    # so pending guidance is drained here. No pending steer means byte-identical
    # behaviour; the call is a dict lookup.
    def _with_steer(result: Any) -> Any:
        from orchestrator.steer import decorate_tool_result  # noqa: PLC0415
        return decorate_tool_result(result)

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
            if _after_untrusted_answer(reason, marked) and not await _untrusted_answer_card(
                    tool_name, kwargs):
                return UNTRUSTED_ANSWER_REFUSED.format(tool=tool_name)
            # WS-47 WAC-4: name the call for the WhatsApp card gate. A no-op
            # outside a WhatsApp run with writes on.
            with _whatsapp_tool_call(fn, args, kwargs):
                return _with_steer(await fn(*args, **kwargs))
        return _register_platform_wrapper(fn, _agated)

    @functools.wraps(fn)
    def _sgated(*args: Any, **kwargs: Any) -> Any:
        allowed, reason = _gate(kwargs)
        if not allowed:
            return f"[blocked by permission policy: {reason}]"
        if _after_untrusted_answer(reason):
            # A sync tool cannot wait for a card, so it is refused.
            return UNTRUSTED_ANSWER_REFUSED.format(tool=tool_name)
        return _with_steer(fn(*args, **kwargs))
    return _register_platform_wrapper(fn, _sgated)


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
    fault, are a no at once (round 3)."""
    import json

    refusal = _untrusted_answer_card_refusal()
    if refusal is not None:
        _log.info("permission.untrusted_answer_refused", tool=tool_name, outcome=refusal)
        return False
    try:
        from acb_skills.ask_tools import request_confirmation

        shown = json.dumps(call_kwargs or {}, ensure_ascii=False, default=str)[:600]
        answer = await request_confirmation(
            f"Run {tool_name}? It reaches outside Metorite",
            "This chat read an answer from the email assistant, which reads "
            "outside email. A mail could ask for this call.",
            f"{tool_name} {shown}",
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
    neither the ``permission.decision`` audit line nor the mid-run steer
    drain. A steer that arrived during a turn that called only own tools was
    buffered, never drained, and erased at the next run (PR #585 review). The
    steer drain lives in this wrapper and nowhere else on Tier 1.

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
) -> str:
    """Return a system-prompt addendum describing the Metorite-injected tools.

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
        from gateway.routes.agent import _AGENT_REGISTRY  # noqa: PLC0415
        from gateway.routes.agent import _load_dynamic_agents
        all_agents = _load_dynamic_agents() + _AGENT_REGISTRY
        for a in all_agents:
            name = a.get("name", "")
            if name:
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


def _registry_block_for(agent_name: str | None) -> str:
    """The registry block that *agent_name* reads: every agent but itself.

    It filters the cached :func:`_build_registry_block`, so a stub of that
    function in a test still applies. ``None`` returns the whole block.
    """
    block = _build_registry_block()
    if not agent_name:
        return block
    own = _registry_line(agent_name, "").rstrip()  # "  - 'name':"
    return "\n".join(
        line for line in block.splitlines()
        if line.rstrip() != own and not line.startswith(own + " ")
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
#: ``log_activity`` stays: a Metorite-only note saves in Mirror mode (CRM-Z6).
MIRROR_RECORD_WRITES: frozenset[str] = frozenset({
    "create_lead", "update_deal_status", "convert_lead",
})


async def _drop_mirror_record_writes(
    agents: list[Any], *, no_egress: bool = False,
) -> None:
    """Take the CRM record writes from this run's agents in Mirror mode.

    It runs at assembly, after :func:`_apply_own_tool_scope`, and only for an
    agent that holds one of :data:`MIRROR_RECORD_WRITES`. It reads
    ``GET /crm/mode`` as the run's member (``acb_skills.crm_tools.crm_mode``),
    never a request field. If the read fails, the tools stay, and the 409
    relay of the write tools is the fallback. A change in place holds for
    this run only (:func:`_own_tool_pools`).

    A ``no_egress`` run (H-236) reads nothing: the egress control drops the
    three writes there anyway (each says ``open_world=True``), and a covered
    run sends no request that it does not need.
    """
    if no_egress:
        return
    held = {
        _tool_name(t)
        for agent in agents for _attr, pool in _own_tool_pools(agent) for t in pool
    }
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


async def _add_crm_facts_block(
    agents: list[Any], run_tools: Any, *, no_egress: bool = False,
) -> frozenset[str]:
    """Add the CRM facts block to a run that holds a CRM tool (WS-53
    CRM-Q32-5, ``crm_platform.md`` §18.14.4): the answered facts of the money
    modules and of the stages, at most 12 lines, at the END of the prompt,
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
    """
    held = frozenset(run_tools or ())
    try:
        from acb_skills.crm_tools import CRM_TOOL_NAMES, questions_box_on
        from acb_skills.org_facts_tools import (
            BLOCK_MARKER,
            ORG_FACTS_TOOLS,
            crm_facts_block,
            crm_facts_for_run,
        )
    except ImportError:
        return held
    facts_tools = frozenset(fn.__name__ for fn in ORG_FACTS_TOOLS)
    own = {_tool_name(t) for agent in agents for _a, pool in _own_tool_pools(agent)
           for t in pool}
    holds_crm = bool((held | own) & CRM_TOOL_NAMES)
    rows: list[Any] = []
    if holds_crm and not no_egress and questions_box_on():
        rows = await crm_facts_for_run()
    if not rows:
        _drop_own_tools(agents, facts_tools)
        return held - facts_tools
    block = crm_facts_block(rows)
    added = 0
    if block:
        added = sum(1 for agent in agents if _append_instructions(agent, block, BLOCK_MARKER))
    _log.info("executor.crm_facts_block", facts=len(rows),
              lines=block.count("\n") + 1 if block else 0, agents=added)
    return held


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
    return WITHHELD_TOOLS if current_run() is not None else frozenset()


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
    try:
        from acb_skills.attachment_tools import read_attachment
        _all_tools = [*_all_tools, read_attachment]
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
    # core floor does not change (crm_platform.md §18.14.4).
    try:
        from acb_skills.org_facts_tools import ORG_FACTS_TOOLS
        _all_tools = [*_all_tools, *ORG_FACTS_TOOLS]
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
) -> frozenset[str]:
    """Inject cross-agent delegation tools into every loaded agent.

    Returns the names of the platform tools this run holds after every
    narrowing (scope, skill toggles, D85, H-236, the floor opt-out and
    WhatsApp). The executor binds them as ``run_tools`` in the artifact
    context, and ``write_artifact.run_holds_tool`` reads them, so a tool that
    reaches another tool in-process asks the same answer (§14.10 of
    ``projects_ai_chat.md``). Empty when nothing was injected.

    ``agent_config`` is the agent's parsed ``config.json``. Its ``sharing``
    block decides the D85 interim block (:func:`_withheld_shell_tools`): a
    shared agent gets no ``SHELL_TOOLS`` member until the sandbox broker
    covers it. ``None`` reads as shared, so a caller that forgets it fails
    closed. Every executor call site passes it, and
    ``tests/unit/test_shared_agent_shell_tools.py`` says so. Its
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
    _all_tools = _collect_injectable_platform_tools(agent_name)
    if not _all_tools:
        if no_egress:  # H-236 holds even when nothing else is injected
            _apply_no_egress(agents, agent_name)
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
    # D85 (the interim block): a shared agent gets no shell tool until the
    # sandbox broker covers it. The withheld names leave the SCOPE, so the
    # addendum below never describes a tool this agent does not hold.
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
    _scope_names = _resolve_injected_scope(
        tool_scope, disabled_families=_disabled_families,
        withheld=_withheld | _egress | _mail_store | _opted_out | _wa_withheld,
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
    # through the same _gate_injected_tool pipeline below. Any write inside
    # a workflow is already broker-gated / approval-noded.
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
    _extra_tools = _drop_withheld(_extra_tools, _withheld | _opted_out | _wa_withheld)
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
                # join the list (they are gated above already). Same B6 gate
                # and steer drain the Copilot branch gives its own tools.
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
                        # to itself only spends a turn.
                        _reg = _registry_block_for(agent_name)
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

    # H-236: the agent's OWN egress tools and MCP servers leave as well, and
    # a native MAF agent gets the call-time refusal, as a per-run view.
    if no_egress:
        _apply_no_egress(agents, agent_name, injected=_egress)
    # CRM-Q29: a native MAF agent that holds a CRM tool gets the number
    # check, as a per-run view, after the H-236 step.
    _apply_answer_check(agents, agent_name)
    return _run_tool_names


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
    """
    from acb_common.settings import crm_answer_check_mode

    mode = crm_answer_check_mode()
    if mode == "off":
        return
    try:
        from acb_skills.answer_check import AnswerCheckProvider, holds_crm_tool
        from agent_framework import Agent
    except ImportError as exc:
        _log.warning("executor.answer_check_unavailable", error=str(exc)[:200])
        return
    from orchestrator._native_run_context import agent_with_providers

    for i, agent in enumerate(agents):
        if not isinstance(agent, Agent):
            continue
        held = [_tool_name(t) for _label, pool in _own_tool_pools(agent) for t in pool]
        if not holds_crm_tool(held):
            continue
        agents[i] = agent_with_providers(agent, [AnswerCheckProvider(mode)])
        _log.info("executor.answer_check_on", agent=agent_name, mode=mode)


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
            removed.update(_withhold_egress_from_agent(agent))
            agents[i] = _with_egress_guard(agent)
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

    **The card** (H-299). *member_present* is True only when a member can
    answer a confirmation card in this run. The executor passes it. The
    default is False, so a caller that does not say refuses each MCP tool
    that needs a card (``_maf_mcp.McpPermissionGate``).

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

    if agent_name in UNTRUSTED_INPUT_AGENTS:
        # EM-G11d (UMCC): a mail could tell the model to send mailbox data to
        # a server, and no check here can prove that a server cannot fetch.
        _log.info("mcp.untrusted_input_agent_skipped", agent=agent_name)
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
        if maf_servers:
            await _maf_mcp.attach_mcp_tools(
                agent, maf_servers, opened if opened is not None else [],
                member_present=member_present,
                on_unauthorized=_mark_unauthorized_for(str(org)),
            )
        return

    for srv in servers:
        _log.info("mcp.copilot_registry_skipped", name=srv.name)


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
            # reads as false, so the server keeps its H-299 behaviour.
            always_confirm=getattr(srv, "always_confirm", False) is True,
            # IN-8b: an OAuth row, and whether this read refreshed its token.
            oauth=getattr(srv, "oauth", False) is True,
            oauth_refreshed=getattr(srv, "refreshed", False) is True,
            # IN-8c (D98): the admin that an OAuth row acts as, for the card.
            acts_as=(str(getattr(srv, "connected_by", None) or "")
                     if getattr(srv, "oauth", False) is True else ""),
        ))
    return out


async def _close_mcp_tools(opened: list[Any]) -> None:
    """Close each MCP tool and client that one run opened. Never raises."""
    if not opened:
        return
    from orchestrator._maf_mcp import close_mcp_tools

    await close_mcp_tools(opened)
