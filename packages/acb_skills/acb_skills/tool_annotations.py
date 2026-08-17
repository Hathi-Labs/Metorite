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
"""
from __future__ import annotations

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
}


def annotate(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator registering risk annotations for an agent-defined tool.

    Example::

        @annotate(destructive=True, open_world=True)
        async def send_email(...): ...
    """
    hints = {
        "read_only": read_only,
        "destructive": destructive,
        "idempotent": idempotent,
        "open_world": open_world,
    }

    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOL_ANNOTATIONS[fn.__name__] = hints
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


def risk_summary_block() -> str:
    """Byte-stable addendum block summarising tool risk classes.

    Rendered into the injected-tools system-prompt addendum so the agent can
    reason about which calls are safe to make freely vs. which reach outside
    the platform or mutate state.
    """
    read_only = sorted(n for n, h in TOOL_ANNOTATIONS.items() if h["read_only"])
    writes = sorted(
        n for n, h in TOOL_ANNOTATIONS.items()
        if not h["read_only"] and not h["destructive"]
    )
    destructive = sorted(n for n, h in TOOL_ANNOTATIONS.items() if h["destructive"])
    open_world = sorted(n for n, h in TOOL_ANNOTATIONS.items() if h["open_world"])

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
