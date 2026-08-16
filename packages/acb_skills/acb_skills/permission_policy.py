"""Risk-aware permission handler for agent runs (B6 / HH-6).

Replaces the blanket ``PermissionHandler.approve_all`` — which auto-approved
EVERY shell command, file write, and network fetch an agent decided to run —
with a policy that gates on the request's own classification plus our
``tool_annotations`` risk vocabulary. The single biggest safety lever in the
core: it turns "the model can do anything in-process, silently" into "dangerous
shell + out-of-workspace writes are blocked, and every privileged op is logged
and attributable" (via the E2 run-correlation contextvars).

Decision policy (see specs/permissions_sandbox_b6.md for the table):
  * read-only requests / read-only tools      → APPROVE (observe only)
  * annotated non-destructive named tools      → APPROVE (reversible writes)
  * annotated destructive named tools          → APPROVE, deferring to the
                                                 tool's own request_confirmation
                                                 fail-closed gate (HH-2) — must
                                                 NOT double-gate or it deadlocks
  * shell commands                             → APPROVE unless they match the
                                                 dangerous-command denylist
  * file writes outside the agent workspace    → DENY (out of bounds)
  * network                                    → APPROVE (open_world is normal),
                                                 logged for exfil visibility
  * unknown / unclassifiable                   → APPROVE + WARN (fail-open-loud;
                                                 tighten from observed data)

Mode via ``AGENT_PERMISSION_MODE``:
  enforce (default) apply the policy · audit log the would-be decision but
  always approve (safe rollout) · approve_all keep the old behaviour (handled
  by the executor: it uses the SDK's approve_all directly in that mode).

The handler is SDK-shape-agnostic: it reads request fields defensively (dict or
attribute access) and returns whatever ``PermissionRequestResult`` /
``PermissionDecision`` the installed ``copilot`` package expects, so it works
across SDK versions.
"""
from __future__ import annotations

import os
import re
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.permission_policy")

# Dangerous shell patterns → fail-closed DENY even in enforce mode. Conservative
# (matches the unambiguously destructive), overridable via
# AGENT_PERMISSION_DENY_PATTERNS (newline- or ';;'-separated regexes).
_DEFAULT_DENY_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b.*\s(/|~|\$HOME|\.\.)",
    r"\bmkfs\b",
    r"\bdd\b.+\bof=/dev/",
    r">\s*/dev/sd[a-z]",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;",          # fork bomb
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\b(curl|wget)\b.+\|\s*(sudo\s+)?(sh|bash|zsh)\b",  # curl … | sh
    r"\bchmod\s+-R\s+777\s+/",
    r"\b(userdel|deluser|passwd)\b",
    r"\bgit\s+push\b.+--force\b.+\b(origin\s+)?(main|master)\b",
]


def _mode() -> str:
    m = os.environ.get("AGENT_PERMISSION_MODE", "enforce").strip().lower()
    return m if m in ("enforce", "audit", "approve_all") else "enforce"


def _deny_patterns() -> list[re.Pattern[str]]:
    raw = os.environ.get("AGENT_PERMISSION_DENY_PATTERNS", "")
    pats = (
        [p for chunk in raw.replace(";;", "\n").splitlines()
         if (p := chunk.strip())]
        if raw.strip() else list(_DEFAULT_DENY_PATTERNS)
    )
    out: list[re.Pattern[str]] = []
    for p in pats:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return out


def _field(request: Any, name: str) -> Any:
    """Read a PermissionRequest field whether it's a dict or a dataclass/obj."""
    if isinstance(request, dict):
        return request.get(name)
    return getattr(request, name, None)


def _workspace_root() -> str | None:
    try:
        from acb_skills.write_artifact import (
            _WRITE_ARTIFACT_CONTEXT,
        )
        # BO-7 phase 2: a sandboxed Copilot session (copilot_sandbox.py) reports
        # PermissionRequest paths relative to the CONTAINER's fixed mount point
        # (/workspace/repo), not the host workspace_root — the call site sets
        # permission_check_root for the duration of that call so this containment
        # check compares against the right root. Unset everywhere else, so this
        # is a no-op for every non-sandboxed call.
        return (
            _WRITE_ARTIFACT_CONTEXT.get("permission_check_root")
            or _WRITE_ARTIFACT_CONTEXT.get("workspace_root")
            or None
        )
    except Exception:
        return None


def _is_within(path: str, root: str) -> bool:
    """True if *path* resolves inside *root* (blocks ../ traversal)."""
    try:
        from pathlib import Path
        rp = Path(root).resolve()
        target = Path(path)
        target = target if target.is_absolute() else rp / target
        target.resolve().relative_to(rp)
        return True
    except Exception:
        return False


# ── Pure decision function (unit-testable, no SDK types) ─────────────────────


def decide(request: Any) -> tuple[bool, str, str]:
    """Return ``(approved, reason_code, detail)`` for a permission request.

    Pure over the request fields — no SDK result objects, no I/O beyond reading
    env policy + the workspace-root context. The executor's handler wraps this
    and maps ``approved`` to the SDK's result type.

    Ordering (BO-7 cheap win 1/3): the dangerous-shell and out-of-workspace
    hard vetoes run BEFORE the named-tool annotation lookup, not after. They
    used to run only when a request carried no recognised ``tool_name`` — so
    a call that was BOTH a named platform tool AND, per its real arguments,
    a denylisted shell command or an out-of-workspace write, was waved
    through purely on the tool's "reversible"/"destructive-defer"
    classification without ever being checked against what it actually asked
    to do. The annotation lookup itself is unchanged; only its position moved,
    and it still runs — and still short-circuits to approve — once neither
    veto applies. Every existing single-field test call (no request combines
    ``tool_name`` with shell/write fields) is unaffected by the reorder.
    """
    read_only = bool(_field(request, "read_only"))
    tool_name = str(_field(request, "tool_name") or "")
    commands = _field(request, "commands")
    full_cmd = str(_field(request, "full_command_text") or "")
    has_write_redir = bool(_field(request, "has_write_file_redirection"))
    new_file = _field(request, "new_file_contents")
    path = str(_field(request, "path") or "")
    url = _field(request, "url") or _field(request, "possible_urls")

    # 1. Read-only → always safe.
    if read_only:
        return True, "read_only", "observation only"

    # 2. Dangerous shell command → hard veto (fail-closed), regardless of
    #    what tool is asking for it.
    cmd_text = full_cmd
    if not cmd_text and commands:
        cmd_text = " ".join(
            commands if isinstance(commands, list) else [str(commands)]
        )
    if cmd_text:
        for pat in _deny_patterns():
            if pat.search(cmd_text):
                return False, "shell_denied", cmd_text[:200]

    # 3. File write outside the agent workspace → hard veto (fail-closed),
    #    same reasoning: a tool's own annotation cannot waive this.
    if has_write_redir or new_file is not None or path:
        root = _workspace_root()
        if root and path and not _is_within(path, root):
            return False, "write_out_of_workspace", path[:200]

    # 4. Named platform tool → consult risk annotations. Reached once the
    #    hard vetoes above have cleared (or didn't apply), so the common case
    #    — an annotated tool with no separately-supplied command/path context
    #    — still gets its fast, no-extra-context approval.
    if tool_name:
        try:
            from acb_skills.tool_annotations import (
                get_annotations,
            )
            hints = get_annotations(tool_name)
        except Exception:
            hints = None
        if hints:
            if hints.get("read_only"):
                return True, "tool_read_only", tool_name
            if hints.get("destructive"):
                # The destructive tool self-gates via request_confirmation
                # (fail-closed, HH-2). Approving here lets that card fire;
                # denying would deadlock it. Do NOT double-gate.
                return True, "tool_destructive_defer", tool_name
            return True, "tool_reversible", tool_name
        # Unknown named tool → fall through to fail-open-loud below.

    # 5. Shell command that cleared the denylist → approve.
    if cmd_text:
        return True, "shell_ok", cmd_text[:200]

    # 6. File write that cleared containment (or no workspace configured) →
    #    approve.
    if has_write_redir or new_file is not None or path:
        return True, "write_in_workspace", path[:200] or "(workspace)"

    # 7. Network → open_world is expected; approve but surface for audit.
    if url:
        return True, "network", str(url)[:200]

    # 8. Unknown / unclassifiable → fail OPEN but LOUD (near-term slice).
    return True, "unknown_allowed", tool_name or "(unclassified request)"


# ── Per-tool call-context builders (BO-7 cheap win 1/3) ──────────────────────
# The Copilot SDK's own built-in shell/file/fetch hook already gives decide()
# full context (PermissionRequest carries full_command_text/path/etc. — see
# copilot.generated.session_events.PermissionRequest). Every gate wrapper for
# OUR OWN platform tools, though, called decide({"tool_name": tool_name}) —
# name only, no args — so run_script (which builds and executes a literal
# shell command from its own arguments) was gated purely on its "reversible"
# annotation, never on what it was actually about to run.
#
# Deliberately narrow: only run_script is mapped. code_task's `task` is a
# free-text prompt describing INTENT, not the shell command that will run —
# mapping it into full_command_text would false-positive the denylist on
# ordinary prose ("clean up with rm -rf tmp/ if needed" is a reasonable task
# description), and its real shell commands are already separately gated
# inside its own bounded Copilot session (code_session.py sets its own
# _permission_handler). install_dependency's `packages` string is pre-
# validated against a name[==version]-only regex before it ever reaches a
# subprocess, so it isn't exposed to the same class of shell-injection risk.
def _run_script_context(kwargs: dict) -> dict:
    path = str(kwargs.get("path") or "")
    script_args = str(kwargs.get("args") or "")
    return {
        "path": path,
        "full_command_text": f"{path} {script_args}".strip(),
    }


_TOOL_CONTEXT_BUILDERS: dict[str, Any] = {
    "run_script": _run_script_context,
}


def build_tool_call_context(tool_name: str, kwargs: dict) -> dict:
    """Build the request :func:`decide` sees for a gated platform-tool call.

    Always includes ``tool_name``; additionally maps a tool's real call
    keyword-arguments onto :func:`decide`'s recognised fields for the tools in
    :data:`_TOOL_CONTEXT_BUILDERS`. A builder failure (unexpected arg shape)
    falls back to name-only context rather than raising — never brick a tool
    call over a permission-context mapping bug.
    """
    ctx: dict[str, Any] = {"tool_name": tool_name}
    builder = _TOOL_CONTEXT_BUILDERS.get(tool_name)
    if builder is not None:
        try:
            ctx.update(builder(kwargs or {}))
        except Exception:
            pass
    return ctx


def _approved_result() -> Any:
    """The installed SDK's 'approved' PermissionRequestResult."""
    from copilot.types import PermissionRequestResult
    return PermissionRequestResult(kind="approved")


def _denied_result(reason: str) -> Any:
    """The installed SDK's 'denied' result carrying agent-visible feedback."""
    from copilot.types import PermissionRequestResult
    return PermissionRequestResult(
        kind="denied-by-rules",
        feedback=(
            "Blocked by Metorite's permission policy: " + reason +
            ". If you need this, ask the user to approve it explicitly."
        ),
    )


def risk_aware_permission_handler(request: Any, invocation: dict[str, str]) -> Any:
    """Drop-in replacement for ``PermissionHandler.approve_all`` (B6/HH-6).

    Same ``(request, invocation) -> PermissionRequestResult`` signature. Applies
    :func:`decide`; in ``audit`` mode it logs the would-be decision but always
    approves; ``enforce`` (default) applies it. Every decision is logged (with
    the E2 run-correlation contextvars already bound on the run) so privileged
    operations are observable and attributable.
    """
    mode = _mode()
    try:
        approved, code, detail = decide(request)
    except Exception as exc:
        _log.warning("permission.decide_failed", error=str(exc))
        return _approved_result()

    denied_would = (not approved) and mode == "enforce"
    _log.info(
        "permission.decision",
        mode=mode,
        approved=(approved or mode == "audit"),
        would_deny=not approved,
        reason=code,
        detail=detail,
    )
    if denied_would:
        return _denied_result(f"{code}: {detail}")
    return _approved_result()
