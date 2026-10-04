"""MCP-style risk annotations for platform tools (HH-2).

Four hints per tool, mirroring the MCP tool-annotation vocabulary:

    read_only    the tool observes only — no state mutation anywhere
    destructive  the effect is outward-facing or irreversible (send, delete)
    idempotent   repeating the call with the same args changes nothing more
    open_world   the tool reaches outside Metorite (web, packages, email)

The registry is the single source of truth: the executor renders it into the
injected-tools addendum so agents can reason about risk, and permission /
confirmation layers consult it to decide what may proceed without a human.

Agents' own tools can register too via :func:`annotate`.

``open_world`` is also the egress annotation (H-236), and the rule that reads
it fails closed (``acb_skills.egress``). A covered Projects run, and each run
under it, keeps only the tools that say ``open_world=False`` (and the
delegation and sandbox tools). A tool with no ``open_world`` counts as able to
send data off the platform. So every tool states it: ``True`` for a send, a
fetch, a push to an outside system or a rule that forwards, ``False`` for a
read or a write that stays inside Metorite. :func:`annotate` has no default
for it. Fence: ``tests/unit/test_delegation_no_egress.py``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable

# name → {read_only, destructive, idempotent, open_world}
TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    # Inter-agent delegation — the sub-agent may act, and acts beyond our view.
    "call_agent":            {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
    "call_agents_parallel":  {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
    "call_agent_background": {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
    # Web access
    "web_search":            {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": True},
    "fetch_page":            {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": True},
    # Artifacts
    "write_artifact":        {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "share_artifact":        {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    # H-229: the text of a file attached in this chat. Pure parsing, no code.
    "read_attachment":       {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    # Memory
    "remember":              {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "recall_timeline":       {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "save_memory":           {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "save_episode":          {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    # Todos / HITL
    "manage_todo_list":      {"read_only": False, "destructive": False, "idempotent": True,  "open_world": False},
    "ask_questions":         {"read_only": True,  "destructive": False, "idempotent": False, "open_world": False},
    "ask_user":              {"read_only": True,  "destructive": False, "idempotent": False, "open_world": False},
    "request_confirmation":  {"read_only": True,  "destructive": False, "idempotent": False, "open_world": False},
    # Code / runtime
    "get_errors":            {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "run_diagnostics":       {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    # destructive: installs into the SHARED gateway venv (not per-agent-
    # isolated) — a malicious/typosquatted package can affect every agent in
    # the process, not just the caller. Note (BO-7 cheap win 2/3): flagging
    # here changes decide()'s reason code (tool_destructive_defer) and the
    # agent-facing risk_summary_block text, but no tool in this codebase yet
    # calls request_confirmation on its own behalf before running — that live
    # HITL wiring is BO-14's job, not this flag. Don't read "destructive here"
    # as "a confirmation card already exists."
    "install_dependency":    {"read_only": False, "destructive": True,  "idempotent": True,  "open_world": True},
    # Coding skill — run_script executes arbitrary saved code; code_task runs a
    # bounded Copilot coding session. Both mutate the workspace and can reach
    # out (a script may hit the network), hence open_world. NOT flagged
    # destructive despite executing code: both are explicitly documented (see
    # the injected-tools addendum) as fast, non-interactive re-runs of
    # already-reviewed workspace scripts — confirming every call would
    # contradict that intentional design, not hardener it. The real gate on
    # what they can execute is decide()'s shell-denylist/workspace-
    # containment checks (BO-7 cheap win 1/3), which now see run_script's
    # actual path/args, not this annotation.
    "run_script":            {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
    "code_task":             {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
    "list_integrations":     {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "load_design_system":    {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    # Notes / history / code search
    "save_note":             {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "recall_notes":          {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "query_history":         {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "github_search":         {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": True},
    "github_repo_search":    {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": True},
    # WS-31 CP-13d: a typed decision from a third-party model through the
    # Console Router. It changes nothing, and it reaches outside Metorite.
    "decide":                {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": True},
    # WS-43d (maf_coding_engine.md §7.4): the sandbox tools. run_command runs
    # code, so it is a shell tool (manifest.SHELL_TOOLS), and it runs only in
    # the container, which has no network: open_world is False until a grant
    # (WS-43g). request_network_access is the stub of that grant. Neither is
    # ever injected, so the risk block below never names them.
    "run_command":           {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "request_network_access": {"read_only": False, "destructive": False, "idempotent": False, "open_world": True},
    # H-236: the other sandbox tools. MAF's eight file tools over the run's
    # TenantFileStore and the three tools of its SkillsProvider. They touch
    # only the run's own files, and a skill script runs in the container,
    # which has no network. So each one says open_world=False explicitly.
    "file_access_read":       {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "file_access_read_lines": {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "file_access_ls":         {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "file_access_grep":       {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "file_access_write":      {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "file_access_delete":     {"read_only": False, "destructive": True,  "idempotent": True,  "open_world": False},
    "file_access_replace":    {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "file_access_replace_lines": {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "load_skill":             {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "read_skill_resource":    {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "run_skill_script":       {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    # H-236: chain tools that had no entry. emit_generative_ui renders in the
    # member's own sandboxed iframe, load_artifact_kit reads a bundled file,
    # and the four agent and org memory tools read and write the platform's
    # own store. None of them reaches outside Metorite.
    "emit_generative_ui":    {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "load_artifact_kit":     {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "recall_agent":          {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "recall_org":            {"read_only": True,  "destructive": False, "idempotent": True,  "open_world": False},
    "save_agent_memory":     {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
    "save_org_memory":       {"read_only": False, "destructive": False, "idempotent": False, "open_world": False},
}

#: Tools that only a sandboxed run holds (WS-43d). ``_inject_agent_tools`` never
#: adds them, and only a factory that the broker covers attaches them, to ONE
#: run. :func:`risk_summary_block` leaves them out, so the addendum of every
#: other agent stays byte-identical (``tests/unit/test_run_command_tool.py``).
SANDBOX_TOOL_NAMES: frozenset[str] = frozenset({
    "run_command", "request_network_access",
    "file_access_read", "file_access_read_lines", "file_access_ls", "file_access_grep",
    "file_access_write", "file_access_delete", "file_access_replace",
    "file_access_replace_lines", "load_skill", "read_skill_resource", "run_skill_script",
})

#: Names that H-236 registered only so that each one carries an explicit
#: ``open_world``. The risk block leaves them out, so it stays the text it
#: was before H-236.
_H236_UNLISTED: frozenset[str] = frozenset({
    "emit_generative_ui", "load_artifact_kit", "recall_agent", "recall_org",
    "save_agent_memory", "save_org_memory",
})

#: Every name that :func:`annotate` registered: an agent's OWN tool. They
#: used to join the risk block whenever their module was imported, so the
#: block changed with the import order of one process. Now each agent's block
#: names its own risky tools only (:func:`risk_summary_block`, ``own=``).
_AGENT_OWN: set[str] = set()

#: The platform's own names: the entries this module defines, captured before
#: any :func:`annotate` call or runtime registration. The risk block lists
#: these, so it no longer depends on what one process imported.
_PLATFORM_STATIC: frozenset[str] = frozenset(TOOL_ANNOTATIONS)


def annotate(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator registering risk annotations for an agent-defined tool.

    ``open_world`` has no default (H-236). A tool that omits it carries no
    ``open_world`` key, and ``acb_skills.egress`` then counts it as able to
    send data off the platform. So every tool states it.

    Example::

        @annotate(destructive=True, open_world=True)
        async def send_email(...): ...
    """
    hints: dict[str, bool] = {
        "read_only": read_only,
        "destructive": destructive,
        "idempotent": idempotent,
    }
    if open_world is not None:
        hints["open_world"] = bool(open_world)

    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOL_ANNOTATIONS[fn.__name__] = hints
        _AGENT_OWN.add(fn.__name__)
        fn.__tool_risk__ = hints  # type: ignore[attr-defined]
        return fn

    return _wrap


def get_annotations(tool: str | Callable[..., Any]) -> dict[str, bool] | None:
    """Annotations for a tool (by name or callable), or None if unregistered."""
    name = tool if isinstance(tool, str) else getattr(tool, "__name__", "")
    return TOOL_ANNOTATIONS.get(name)


def is_destructive(tool: str | Callable[..., Any]) -> bool:
    """True when the tool is registered as destructive.

    Unregistered tools return False — callers gating destructive actions must
    require an explicit human approval path regardless (fail closed lives in
    ``request_confirmation``, not here).
    """
    hints = get_annotations(tool)
    return bool(hints and hints["destructive"])


def risk_summary_block(own: Iterable[tuple[str, bool, bool]] = ()) -> str:
    """Byte-stable addendum block summarising tool risk classes.

    Rendered into the injected-tools system-prompt addendum so the agent can
    reason about which calls are safe to make freely vs. which reach outside
    the platform or mutate state.

    It is PER AGENT and deterministic (H-236). It lists the platform's own
    names (:data:`_PLATFORM_STATIC`, without the sandbox tools), plus the
    agent's own tools in *own*: ``(name, destructive, open_world)`` for each
    tool of THIS agent that is destructive or reaches outside Metorite. The
    caller builds *own* from the agent's own tool list, never from the
    process-wide registry. An agent's read tools stay out, so the block does
    not grow with every annotated tool.
    """
    unlisted = SANDBOX_TOOL_NAMES | _H236_UNLISTED
    listed = {
        n: TOOL_ANNOTATIONS.get(n, {}) for n in _PLATFORM_STATIC if n not in unlisted
    }
    read_only = sorted(n for n, h in listed.items() if h.get("read_only"))
    writes = sorted(
        n for n, h in listed.items()
        if not h.get("read_only") and not h.get("destructive")
    )
    destructive = sorted(
        {n for n, h in listed.items() if h.get("destructive")}
        | {n for n, d, _o in own if d}
    )
    open_world = sorted(
        {n for n, h in listed.items() if h.get("open_world")}
        | {n for n, _d, o in own if o}
    )

    lines = [
        "### Tool risk annotations",
        f"- Read-only (call freely): {', '.join(read_only)}",
        f"- State-writing (reversible): {', '.join(writes)}",
    ]
    if destructive:
        lines.append(
            "- DESTRUCTIVE (irreversible/outward — always confirm with the "
            f"user first): {', '.join(destructive)}"
        )
    lines.append(
        f"- Open-world (reaches outside Metorite): {', '.join(open_world)}"
    )
    return "\n".join(lines)
