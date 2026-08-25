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
``_apply_own_tool_scope``, ``_build_registry_block``,
``_build_injected_tools_addendum``, ``_inject_agent_tools``,
``_inject_mcp_servers``.
"""
from __future__ import annotations

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
    "emit_generative_ui",                # rich/interactive HITL UI (genui_2)
    "load_design_system",                # on-demand design language (genui_2 §7)
    "load_artifact_kit",                 # on-demand @cc/ui component reference
    "manage_todo_list",                  # task tracking panel
    "ask_questions",                     # HITL clarification
    "run_diagnostics", "get_errors",     # code / file error checking
    "save_note", "recall_notes",         # cross-session working memory
    # Coding skill (agent_coding_skill.md): every MAF agent can author scripts
    # via a bounded Copilot session (code_task) and cheaply re-run the durable
    # scripts it accumulated (run_script). Workspace-jailed + env-scrubbed;
    # list_integrations shows what a script may reach (names, never values).
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
})


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
) -> dict[str, str]:
    """Lay down this agent's on-demand skill bodies (QM-2) — no-op when OFF.

    Called by the executor once per run, AFTER the workspace has been
    rehydrated from the blob store, so a fresh body is never overwritten by a
    stale restore. Resolves the SAME effective scope injection used (declared
    ``tool_scope`` ∪ core floor, ∩ enabled families) so a body describes
    exactly the tools the agent actually received.

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
        )
        return materialize_skill_bodies(
            workspace_root,
            effective_scope=(
                frozenset(scope) if scope is not None else None
            ),
            registry_block=_build_registry_block(),
        )
    except Exception:  # noqa: BLE001 — never block a run over a body file
        _log.warning(
            "executor.skill_bodies_materialize_failed", agent=agent_name,
        )
        return {}


def _resolve_injected_scope(
    tool_scope: list[str] | None,
    *,
    disabled_families: frozenset[str] | None = None,
) -> set[str] | None:
    """Resolve which injected tool names an agent should receive.

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
        base = set(_CORE_STANDARD_TOOL_NAMES) | {
            name for fam in SKILL_FAMILIES.values() for name in fam["tools"]
        }
    return base & enabled_tools


def _build_output_discipline_block(*, compact: bool = False) -> str:
    """The 'all generated files live under outputs/' + design-language rule.

    Prose moved to ``acb_skills.addendum`` (WS-23 S3 — one text source for the
    generated addendum AND this native-MAF instruction append); this seam keeps
    the historical name both injection paths call.
    """
    from acb_skills.addendum import build_output_discipline_block  # noqa: PLC0415
    return build_output_discipline_block(compact=compact)


def _ui_first_directive(*, compact: bool = False) -> str:
    """The proactive 'render UI by default' rule (generative_ui_2 Phase 2).

    Prose moved to ``acb_skills.addendum`` (WS-23 S3); same seam rationale as
    :func:`_build_output_discipline_block` — the addendum renderer and the
    native-MAF instruction append must share one byte-stable text source.
    """
    from acb_skills.addendum import ui_first_directive  # noqa: PLC0415
    return ui_first_directive(compact=compact)


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

    def _gate(call_kwargs: dict[str, Any] | None = None) -> tuple[bool, str]:
        """Return (allowed, reason). Never raises. Logs EVERY decision.

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
        """
        try:
            from acb_skills.permission_policy import (  # noqa: PLC0415
                build_tool_call_context, decide,
            )
            request = build_tool_call_context(tool_name, call_kwargs or {})
            ok, code, _ = decide(request)
            mode = os.environ.get(
                "AGENT_PERMISSION_MODE", "enforce"
            ).strip().lower()
            enforced = (not ok) and mode == "enforce"
            _log.info(
                "permission.decision", mode=mode, tool=tool_name,
                approved=(not enforced), would_deny=(not ok), reason=code,
                surface="injected_tool",
            )
            return (not enforced), code
        except Exception:  # noqa: BLE001 — a gate bug must never brick a tool
            return True, "gate_error"

    # Mid-run steer (docs/multiplayer/README.md §4.6): this wrapper is the one
    # tool boundary that exists on EVERY tier — the executor's Tier-2 shim never
    # runs on the native-streaming path, and the Copilot SDK has its own hooks —
    # so pending guidance is drained here. No pending steer means byte-identical
    # behaviour; the call is a dict lookup.
    def _with_steer(result: Any) -> Any:
        from orchestrator.steer import decorate_tool_result  # noqa: PLC0415
        return decorate_tool_result(result)

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _agated(*args: Any, **kwargs: Any) -> Any:
            allowed, reason = _gate(kwargs)
            if not allowed:
                return f"[blocked by permission policy: {reason}]"
            return _with_steer(await fn(*args, **kwargs))
        return _agated

    @functools.wraps(fn)
    def _sgated(*args: Any, **kwargs: Any) -> Any:
        allowed, reason = _gate(kwargs)
        if not allowed:
            return f"[blocked by permission policy: {reason}]"
        return _with_steer(fn(*args, **kwargs))
    return _sgated


@functools.lru_cache(maxsize=32)
def _build_injected_tools_addendum(
    *,
    is_sub_agent: bool = False,
    effective_scope: frozenset[str] | None = None,
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
    """
    try:
        from acb_skills.addendum import (  # noqa: PLC0415
            render_injected_tools_addendum,
        )
    except ImportError:
        # acb_skills absent means _collect_injectable_platform_tools() returned
        # [] and nothing was injected — an empty addendum is the truthful text.
        return ""
    return render_injected_tools_addendum(
        is_sub_agent=is_sub_agent,
        effective_scope=effective_scope,
        registry_block=_build_registry_block(),
    )


@functools.lru_cache(maxsize=1)
def _build_registry_block() -> str:
    """Build the agent registry listing, cached for the lifetime of the process.

    The registry only changes when a new agent is registered (gateway restart
    or dynamic registration endpoint).  Caching keeps the system-prompt prefix
    byte-stable across turns — required for KV-cache hits (10x cost reduction
    per Manus benchmarks).

    Call ``_build_registry_block.cache_clear()`` after registering a new agent.
    """
    agent_lines: list[str] = []
    try:
        from gateway.routes.agent import _AGENT_REGISTRY  # noqa: PLC0415
        from gateway.routes.agent import _load_dynamic_agents
        all_agents = _load_dynamic_agents() + _AGENT_REGISTRY
        for a in all_agents:
            name = a.get("name", "")
            desc = a.get("description", "")
            if name:
                agent_lines.append(f"  - {name!r}: {desc}")
    except Exception:  # noqa: BLE001
        pass
    return (
        "Registered agents:\n" + "\n".join(agent_lines)
        if agent_lines
        else "Registered agents: check with the orchestrator if unsure."
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
    """
    if not own_scope:
        return
    scope_set = set(own_scope)
    for agent in agents:
        for attr in ("tools", "_tools"):
            lst = getattr(agent, attr, None)
            if not isinstance(lst, list) or not lst:
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


def _collect_injectable_platform_tools() -> list[Any]:
    """Import + return the statically importable platform tools, in injection order.

    Pure collection/introspection — no agent is touched. This is the exact
    import chain :func:`_inject_agent_tools` has always run (extracted
    verbatim, WS-23 S1): an empty list means the mandatory delegation tools
    are unavailable and injection would have bailed out entirely. Also
    consumed read-only by the skills catalog (``acb_skills.skill_families`` +
    ``GET /integrations/skills``) so the catalog can never drift from what
    injection actually offers. Per-agent additions (granted Custom-App action
    tools, the workflow trio) are NOT part of this static set.
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
    # (installed into the shared agent venv, importable immediately).
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

    # GitHub code search — lexical + semantic search across repos.
    try:
        from acb_skills.github_tools import github_search  # noqa: PLC0415
        from acb_skills.github_tools import github_repo_search
        _all_tools = _all_tools + [github_search, github_repo_search]
    except ImportError:
        pass

    return _all_tools


def _inject_agent_tools(
    agents: list[Any], *, is_sub_agent: bool = False,
    tool_scope: list[str] | None = None, agent_name: str | None = None,
) -> None:
    """Inject cross-agent delegation tools into every loaded agent.

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
    """
    _all_tools = _collect_injectable_platform_tools()
    if not _all_tools:
        return  # acb_skills not installed in this env — skip silently

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
    _scope_names = _resolve_injected_scope(
        tool_scope, disabled_families=_disabled_families,
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

    # Gate every injected tool with the risk-aware permission policy (B6). This
    # closes the live gap where injected function-tools (web_search, …) executed
    # on the Copilot-BYOK/streaming path bypass the SDK's on_permission_request
    # hook. functools.wraps keeps __name__/signature so name-based special-cases
    # (call_agent, ask_questions) and SDK/MAF registration are unaffected.
    if os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower() != (
        "approve_all"
    ):
        _extra_tools = [_gate_injected_tool(fn) for fn in _extra_tools]

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
    # Present in BOTH output-discipline variants (the load_design_system
    # pointer) and vanishingly unlikely in a MAF agent's own instructions →
    # a safe idempotency marker for the native-MAF append below.
    _OUTPUT_DISCIPLINE_MARKER = "load_design_system"

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
                        _reg = _build_registry_block()
                        _prev = _do.get("instructions") or ""
                        if _reg and "Delegatable agents" not in _prev:
                            _do["instructions"] = (
                                _prev
                                + "\n\n## Delegatable agents (call_agent)\n"
                                "Hand off to any of these registered specialist "
                                "agents with call_agent(name, message) and use "
                                "their reply (e.g. to gather context before "
                                "drafting):\n" + _reg
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
                    if _OUTPUT_DISCIPLINE_MARKER not in _prev2:
                        _do["instructions"] = (
                            _prev2 + "\n\n"
                            + _build_output_discipline_block(compact=is_sub_agent)
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
                        if _OUTPUT_DISCIPLINE_MARKER not in _instr:
                            agent.instructions = (
                                _instr + "\n\n"
                                + _build_output_discipline_block(
                                    compact=is_sub_agent,
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


def merge_mcp_servers(
    agent: Any, servers: dict[str, dict[str, Any]], *, override: bool,
) -> None:
    """Merge MCP servers into the field the Copilot SDK actually reads.

    MUST target ``agent._mcp_servers``, NOT ``agent._default_options``. The SDK
    consults ``_default_options`` for exactly one key — ``system_message`` —
    and resolves MCP as ``runtime_options.get("mcp_servers") or
    self._mcp_servers`` (``_resume_session`` reads ``self._mcp_servers`` only).
    We pass a fresh per-run options dict to ``agent.run()``, so anything written
    to ``_default_options["mcp_servers"]`` is never read by anyone: MCP servers
    silently never reached a session at all.

    ``override=True`` lets the caller win over what's already there (the DB
    registry outranks a repo's own files); ``False`` only fills gaps.
    Best-effort — never raises.
    """
    if not servers:
        return
    try:
        existing = getattr(agent, "_mcp_servers", None)
        merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        for name, cfg in servers.items():
            if override or name not in merged:
                merged[name] = cfg
        agent._mcp_servers = merged  # type: ignore[attr-defined]
    except Exception:
        pass


async def _inject_mcp_servers(agent: Any, agent_name: str) -> None:
    """Query the MCP server registry and inject matching servers into the agent.

    Reads the ``mcp_servers`` Postgres table, resolves any credential
    references through the Integration Registry, and merges the MCP
    server config into ``agent._mcp_servers`` (see :func:`merge_mcp_servers`
    for why that field and not ``_default_options``).

    Only servers whose ``agent_scope`` includes ``"*"`` or the current
    agent name are injected.

    Best-effort: failures are silently swallowed so MCP issues never
    block agent execution.

    ``mcp_servers`` is RLS-EXEMPT BY DESIGN (``gen_tenant_migration``'s ``EXEMPT``
    set — "keyed (organization_id, name) by MT-0d / 158"): it has no FORCE-RLS
    policy, so the phase-4 RLS cutover does NOT isolate it, and binding a
    ``tenant_session`` here (as slice 7 did for the FORCE-RLS'd reads) would be a
    no-op against a policy-less table. The cross-tenant read is therefore closed
    at the APP level — behind the SAME ``ACB_GRAPH_TENANT_BIND`` flag as slices
    3-7 — by adding an explicit ``organization_id`` filter, so an agent only ever
    receives its OWN org's MCP servers (otherwise, once a second tenant exists, an
    agent in org A would be injected org B's server endpoints/config). The run's
    tenant is resolved SERVER-SIDE on THIS (event-loop) frame via
    ``executor._current_run_org`` (``_RUN_ORG`` keyed by
    ``_stream_relay_thread_id``, else the async ``current_tenant()``) — NEVER from
    tool args / the message (R11). ``mcp_servers.organization_id`` is NOT NULL
    (migration 158), so there are NO org-less "global" servers: the filter is
    strictly ``organization_id = :org`` and the fail-closed subset is EMPTY.

    * flag OFF → no filter, byte-identical to the pre-slice runtime (single-org).
    * flag ON + the run's tenant → only that org's enabled servers.
    * flag ON + NO resolvable tenant → fail closed to nothing (never every org's
      servers).
    """
    sql = (
        "SELECT name, transport, command, url, env_vars, headers, "
        "agent_scope FROM mcp_servers WHERE enabled = true"
    )
    params: dict[str, Any] = {}
    try:
        from acb_graph import get_session, tenant_bind_enabled  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415
        if tenant_bind_enabled():
            # Resolve the run's org on THIS event-loop frame, server-side. This
            # runs before any worker-thread hop, so _current_run_org's _RUN_ORG /
            # current_tenant() lookup resolves on the correct frame.
            from orchestrator.executor import _current_run_org  # noqa: PLC0415
            org = _current_run_org()
            if not org:
                # No tenant → the safe subset is EMPTY (organization_id is NOT
                # NULL, so there are no global rows to keep). NEVER read every
                # org's servers.
                return
            sql += " AND organization_id = :org"
            params = {"org": org}
        with get_session() as s:
            rows = s.execute(text(sql), params).fetchall()
    except Exception:  # noqa: BLE001
        return  # Table may not exist yet — skip gracefully

    mcp_config: dict[str, dict[str, Any]] = {}
    for r in rows:
        name, transport, command, url, env_vars, headers, scope = r
        # Check agent scope
        scope_list: list = scope if isinstance(scope, list) else (
            scope if isinstance(scope, str) else ["*"]
        )
        if isinstance(scope_list, str):
            scope_list = [scope_list]
        if "*" not in scope_list and agent_name not in scope_list:
            continue

        # Shape per the SDK's MCPServerConfig: the key is ``type`` (NOT
        # ``transport``), and a stdio server carries command + args.
        # ``tools: ["*"]`` because the SDK treats an absent/empty list as
        # "expose none" — an enabled server is meant to be usable.
        entry: dict[str, Any] = {}
        if transport == "stdio" and command:
            _parts = str(command).split()
            entry = {
                "type": "stdio",
                "command": _parts[0] if _parts else str(command),
                "args": _parts[1:],
                "tools": ["*"],
            }
            # Resolve env var values from Integration Registry
            resolved_env: dict[str, str] = {}
            raw_env = env_vars or {}
            for k, v in (raw_env if isinstance(raw_env, dict) else {}).items():
                resolved_env[k] = str(v)
            if resolved_env:
                entry["env"] = resolved_env
        elif transport in ("http-sse", "http", "sse") and url:
            entry = {
                "type": "sse" if transport == "sse" else "http",
                "url": url,
                "tools": ["*"],
            }
            raw_headers = headers or {}
            resolved_headers: dict[str, str] = {}
            for k, v in (raw_headers if isinstance(raw_headers, dict) else {}).items():
                resolved_headers[k] = str(v)
            if resolved_headers:
                entry["headers"] = resolved_headers
        else:
            continue

        mcp_config[name] = entry

    if not mcp_config:
        return

    merge_mcp_servers(agent, mcp_config, override=True)
