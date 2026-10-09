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
  * shell commands of a SHARED agent (D85)     → DENY in every mode, until the
                                                 sandbox covers it
                                                 (:func:`guard_shared_agent_shell`)
  * any request but a read, a write or a       → DENY in every mode
    non-egress tool, in a run that a covered     (:func:`guard_shared_agent_shell`,
    run delegated to (H-236)                     ``acb_skills.egress``)
  * file writes outside the agent workspace    → DENY (out of bounds)
  * a read of a BINARY file in the workspace   → DENY in enforce AND audit,
                                                 with a sentence that names
                                                 ``read_attachment`` (incident
                                                 2026-10-09, :func:`binary_file_note`)
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

import functools
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
        from acb_skills.write_artifact import artifact_context
        # BO-7 phase 2: a sandboxed Copilot session (copilot_sandbox.py) reports
        # PermissionRequest paths relative to the CONTAINER's fixed mount point
        # (/workspace/repo), not the host workspace_root — the call site sets
        # permission_check_root for the duration of that call so this containment
        # check compares against the right root. Unset everywhere else, so this
        # is a no-op for every non-sandboxed call.
        ctx = artifact_context()
        return (
            ctx.get("permission_check_root")
            or ctx.get("workspace_root")
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

    The SDK 1.0 request shapes (fix round 1 of PR #598, 2026-10-03):
    ``PermissionRequestWrite`` names its target ``file_name``, not ``path``,
    so a Copilot CLI write used to skip both write vetoes and read as
    ``write_in_workspace`` wherever it went. ``PermissionRequestRead`` has a
    ``path`` and no ``read_only``, so a read used to take the write branch.
    A read now has its own rule: inside the workspace it is approved, and
    outside it, or with no workspace, it is refused. That is the outcome the
    write branch gave a read before, under its own reason code. Fence:
    tests/unit/test_shared_agent_shell_tools.py (WS43-F23).
    """
    kind = str(_field(request, "kind") or "").strip().lower()
    read_only = bool(_field(request, "read_only"))
    tool_name = str(_field(request, "tool_name") or "")
    commands = _field(request, "commands")
    full_cmd = str(_field(request, "full_command_text") or "")
    has_write_redir = bool(_field(request, "has_write_file_redirection"))
    new_file = _field(request, "new_file_contents")
    path = str(_field(request, "path") or "")
    url = _field(request, "url") or _field(request, "possible_urls")
    # The target of a write: ``file_name`` (SDK 1.0), else ``path`` (the
    # older shape, and the run_script context). A read's path is not a write.
    target = str(_field(request, "file_name") or "") or (
        "" if kind == "read" else path
    )

    # 1. Read-only → always safe.
    if read_only:
        return True, "read_only", "observation only"

    # 1b. A file read → inside the workspace only, and never a write.
    if kind == "read":
        return _decide_read(path)

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
    if has_write_redir or new_file is not None or target:
        root = _workspace_root()
        # H-201 (§21.16): a write to a path with NO run context has no
        # workspace to be inside, so it is refused. The old global dict hid
        # this case, because it always held the workspace of SOME run.
        if target and not root:
            return False, "write_without_workspace", target[:200]
        if root and target and not _is_within(target, root):
            return False, "write_out_of_workspace", target[:200]

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
    if has_write_redir or new_file is not None or target:
        return True, "write_in_workspace", target[:200] or "(workspace)"

    # 7. Network → open_world is expected; approve but surface for audit.
    if url:
        return True, "network", str(url)[:200]

    # 8. Unknown / unclassifiable → fail OPEN but LOUD (near-term slice).
    return True, "unknown_allowed", tool_name or "(unclassified request)"


def _decide_read(path: str) -> tuple[bool, str, str]:
    """The decision for a ``read`` request (SDK ``PermissionRequestRead``).

    Inside the run's workspace it is approved. Outside it, or with no run
    context, it is refused, so the CLI cannot read the gateway's ``.env`` or
    another tenant's dir. A read with no path names nothing, and it is
    approved as an observation.

    A read of a BINARY file inside the workspace is refused with the reason
    :data:`BINARY_READ_REASON`, and the detail is the sentence that the model
    reads (:func:`binary_file_note`). Containment runs first, so the check
    never opens a path outside the workspace.
    """
    if not path:
        return True, "read_only", "observation only"
    root = _workspace_root()
    if not root:
        return False, "read_without_workspace", path[:200]
    if not _is_within(path, root):
        return False, "read_out_of_workspace", path[:200]
    note = binary_file_note(path, root)
    if note:
        return False, BINARY_READ_REASON, note
    return True, "read_in_workspace", path[:200]


# ── A binary file is not text (incident 2026-10-09) ──────────────────────────
# The Copilot CLI's built-in ``view`` tool reads any file as text. On an Excel
# file it returned the raw bytes of the ZIP container, ``PK\x03\x04\x14\x00``.
# That tool result failed every save of the chat with a 500, because Postgres
# refuses a NUL. The CLI is a vendor binary, so the read request that reaches
# ``decide()`` is the one place this repo can stop it. The persistence seam
# (``acb_common.pg_text.storable``) still guards the save on its own.
# Fence: tests/unit/test_chat_nul_persist.py.

#: The reason code of a refused read of a binary file.
BINARY_READ_REASON = "read_binary_file"

#: How much of a file the check reads.
_SNIFF_BYTES = 8192

#: The start of each binary format that a text read can meet, and the kind
#: that the sentence names when the file name has no suffix.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
    (b"\x1f\x8b", "gzip"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
)

#: The kinds that ``acb_skills.attachment_tools.read_attachment`` reads.
_ATTACHMENT_KINDS = frozenset({"docx", "xlsx", "pdf"})

#: The suffixes that the Copilot CLI's ``view`` tool sends to the model as an
#: IMAGE, not as text. ``view`` asks the native
#: ``imageHelpersIsBinaryImageFile`` (``function Lk`` in CLI 1.0.66
#: ``app.js``), which answers by the suffix alone, in any case. Its text
#: result is "Viewed image file successfully.", with no NUL. So a read of such
#: a name is always approved, whatever the bytes are, or a screenshot that a
#: member attaches cannot be seen (fix rounds 1 and 2).
#:
#: ⚠️ This list follows CLI 1.0.66. Production fetches the CLI runtime of its
#: SDK pin (``scripts/vps_apply.sh``, the H-181 fetch). A CLI upgrade must
#: check this list again against ``imageHelpersIsBinaryImageFile``.
_CLI_IMAGE_SUFFIXES = frozenset({
    "png", "jpg", "jpeg", "gif", "webp",
    "bmp", "ico", "tif", "tiff", "heic", "avif",
})

#: The byte order marks of UTF-32 and UTF-16 text. Such a file holds NULs and
#: is still text. ``acb_common.pg_text.storable`` guards the save of a NUL
#: that comes through.
_TEXT_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff", b"\xff\xfe", b"\xfe\xff")


def _bomless_utf16(head: bytes) -> bool:
    """True when *head* looks like UTF-16 text with no byte order mark.

    In UTF-16 text that is mostly Latin, every second byte is zero. LE puts
    the zero byte after the letter, and BE puts it before. Nine in ten byte
    pairs must show the pattern, and the other byte must not be zero.
    """
    pairs = len(head) // 2
    if pairs < 2:
        return False
    for zero_at in (1, 0):
        hits = sum(
            1 for i in range(pairs)
            if head[2 * i + zero_at] == 0 and head[2 * i + 1 - zero_at] != 0
        )
        if hits * 10 >= pairs * 9:
            return True
    return False


def _size_text(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def binary_file_note(path: str, root: str) -> str | None:
    """A sentence for the model when *path* is a binary file, else ``None``.

    A file is binary when its first 8 KB hold a NUL, or when it starts with a
    known magic number (ZIP, which holds .xlsx and .docx, PDF, PNG and more).
    The sentence names the file, its kind and its size, and it points a
    .docx, .xlsx or .pdf file at ``read_attachment``.

    Two kinds of file are never binary here. A name with an image suffix of
    :data:`_CLI_IMAGE_SUFFIXES` is an image to the CLI, so the file is not
    opened at all. A file that starts with a UTF-16 or UTF-32 byte order mark
    is text. UTF-16 text with no mark stays refused, with a sentence that
    asks for a UTF-8 copy (:func:`_bomless_utf16`).

    It opens the file through :mod:`acb_skills.safe_open` (a link at any depth
    fails), so call it only after the containment check. Any failure, a
    missing file and a folder return ``None``, and the read goes on as before.
    """
    from pathlib import Path, PurePosixPath

    from acb_skills import safe_open

    try:
        base = os.path.normpath(os.path.abspath(root))
        target = os.path.normpath(os.path.join(base, path))
        rel = os.path.relpath(target, base).replace(os.sep, "/")
        if rel == "." or rel.startswith("../") or rel == "..":
            return None
        if PurePosixPath(rel).suffix.lower().lstrip(".") in _CLI_IMAGE_SUFFIXES:
            return None
        fh = safe_open.open_read(Path(base), rel)
        if fh is None:
            return None
        with fh:
            size = os.fstat(fh.fileno()).st_size
            head = fh.read(_SNIFF_BYTES)
    except (safe_open.UnsafePath, OSError, ValueError):
        return None
    if head.startswith(_TEXT_BOMS):
        return None
    magic = next((kind for sig, kind in _MAGIC if head.startswith(sig)), None)
    if magic is None and b"\x00" not in head:
        return None
    name = PurePosixPath(rel).name
    kind = PurePosixPath(rel).suffix.lower().lstrip(".") or magic or "binary"
    if magic is None and _bomless_utf16(head):
        # Still refused: a text read gives a NUL after each letter. But the
        # agent gets a way on (fix round 2).
        return (
            f"{name} ({kind}, {_size_text(size)}) looks like UTF-16 text with "
            "no byte order mark, so a text read returns it with a NUL in each "
            "letter. It was not read. Ask the member to save it again as UTF-8."
        )
    note = (
        f"{name} is a binary file ({kind}, {_size_text(size)}), so a text "
        "read returns only raw bytes. It was not read."
    )
    if kind in _ATTACHMENT_KINDS:
        note += (
            " If the member attached it in this chat, use read_attachment to "
            "read its text."
        )
    else:
        note += " Do not read it as text."
    return note


def _binary_read_result(note: str) -> Any:
    """The SDK refusal for a read of a binary file. No person approves it."""
    from copilot.generated.rpc import PermissionDecisionReject
    return PermissionDecisionReject(feedback="Blocked by Metorite: " + note)


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


def _run_command_context(kwargs: dict) -> dict:
    """``run_command`` (WS-43d, ``maf_coding_engine.md`` §7.4): the command IS
    the text that runs, so the denylist reads it whole. It runs in the
    container, never on the host, so no ``path`` is mapped."""
    return {"full_command_text": str(kwargs.get("command") or "")}


_TOOL_CONTEXT_BUILDERS: dict[str, Any] = {
    "run_script": _run_script_context,
    "run_command": _run_command_context,
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
    """The installed SDK's one-shot approval.

    SDK 1.0 (H-181) replaced the ``PermissionRequestResult(kind=...)`` record
    with one class per decision, and removed ``copilot.types``.
    """
    from copilot.generated.rpc import PermissionDecisionApproveOnce
    return PermissionDecisionApproveOnce()


def _denied_result(reason: str) -> Any:
    """The installed SDK's refusal, carrying agent-visible feedback.

    ``Reject`` is the SDK 1.0 decision that carries ``feedback``, so the model
    still reads WHY it was blocked. ``DeniedByRules`` takes a rule list and no
    text, which would lose that sentence.
    """
    from copilot.generated.rpc import PermissionDecisionReject
    return PermissionDecisionReject(
        feedback=(
            "Blocked by Metorite's permission policy: " + reason +
            ". If you need this, ask the user to approve it explicitly."
        ),
    )


# ── D85: no shell for a shared agent until the sandbox covers it ─────────────
# Owner decision, 2026-10-03 (work_plan.md D85, maf_coding_engine.md §7.9).
# The injection seam withholds the shell TOOLS from a shared agent (a cover
# may lift that). This half refuses the Copilot CLI's OWN shell, which
# task-manager and app-builder hold. That shell runs on the HOST, so a cover
# never lifts it. The run decides it once, at its boundary: the executor binds
# ``host_shell_refused`` into the artifact context from
# ``orchestrator._tool_injection._host_shell_refused``.
# Fence: tests/unit/test_shared_agent_shell_tools.py (WS43-F23).

#: The text the model reads when D85 refuses its shell.
SHELL_WITHHELD_REASON = (
    "Shell is disabled for shared agents until the sandbox covers them, D85"
)

#: Request kinds that run a shell command. The SDK sends every shell, bash and
#: PowerShell command as ``shell``; the other names cover a future SDK.
_SHELL_KINDS = frozenset({"shell", "bash", "powershell", "pwsh"})


def is_shell_request(request: Any) -> bool:
    """True when *request* asks to run a shell command (D85).

    A ``shell`` kind is one. So is any request that carries command text, so a
    shell request with an unknown kind does not slip through.
    """
    kind = str(_field(request, "kind") or "").strip().lower()
    if kind in _SHELL_KINDS:
        return True
    return bool(
        _field(request, "full_command_text")
        or _field(request, "commands")
        or _field(request, "command_segments")
    )


def host_shell_refused_for_this_run() -> bool:
    """True unless the run on this frame says its host shell may run (D85).

    Only an explicit ``host_shell_refused=False`` in the artifact context
    allows the Copilot CLI's shell. A frame with no run context, or a context
    with no such key, reads as refused. That fails closed, as every H-201
    reader does.
    """
    try:
        from acb_skills.write_artifact import artifact_context
        return artifact_context().get("host_shell_refused", True) is not False
    except Exception:
        return True


def _shell_withheld_result() -> Any:
    """The SDK refusal for a D85 shell request.

    It does not tell the model to ask the user, because no person can approve
    this one. Only a Copilot CLI that runs inside a broker sandbox lifts it,
    and none does yet.
    """
    from copilot.generated.rpc import PermissionDecisionReject
    return PermissionDecisionReject(
        feedback=(
            "Blocked by Metorite: " + SHELL_WITHHELD_REASON + ". Do not try "
            "the shell again in this chat. Use your own tools."
        ),
    )


# ── H-236: no egress in a run that a covered run delegated to ────────────────
# The executor binds ``no_egress`` into the artifact context of every run that
# a covered run delegates to (``acb_skills.egress``). A Copilot sub-agent then
# keeps only the requests that cannot reach the network: a file read, a file
# write (which ``decide()`` contains) and a call to one of its own tools that
# is not an egress tool. Everything else is refused, an unknown kind included:
# the CLI's URL fetch, an MCP call, the shell, memory, hooks and extensions.
# Fence: tests/unit/test_delegation_no_egress.py (WS43-F24).

#: The text the model reads when the H-236 control refuses a request.
EGRESS_WITHHELD_REASON = (
    "A run that another agent called during a sandboxed turn may not send "
    "data off the platform, H-236"
)

#: The request kinds that a ``no_egress`` run may still make.
_NO_EGRESS_KINDS = frozenset({"read", "write", "custom-tool"})


def is_egress_request(request: Any, tools: Any = None) -> bool:
    """True when *request* could carry data off the platform (H-236).

    Only a read, a write, and a call to a tool that is not an egress tool
    pass. A request of any other kind, or with no kind, is an egress request,
    so a new kind of a later SDK fails closed.

    A custom-tool request carries a NAME only. *tools* is a zero-argument
    callable that returns the session's own tool list. The name passes only
    when that list holds a tool of that name and the tool OBJECT is not an
    egress tool, so a tool of another repo that borrows a platform name fails
    (fix round 2). A delegation name is no exception: the session must hold
    the platform's own delegation tool under it (follow-up). With no *tools*,
    or no such tool, it fails closed.
    """
    kind = str(_field(request, "kind") or "").strip().lower()
    if kind not in _NO_EGRESS_KINDS or is_shell_request(request):
        return True
    if kind == "custom-tool":
        from acb_skills.egress import is_egress_tool, tool_name

        name = str(_field(request, "tool_name") or "")
        try:
            held = list(tools() if callable(tools) else [])
        except Exception:
            return True
        match = [t for t in held if tool_name(t) == name]
        return not match or any(is_egress_tool(t) for t in match)
    return False


def _egress_withheld_result() -> Any:
    """The SDK refusal for an H-236 request. No person can approve it."""
    from copilot.generated.rpc import PermissionDecisionReject
    return PermissionDecisionReject(
        feedback=(
            "Blocked by Metorite: " + EGRESS_WITHHELD_REASON + ". Read and "
            "compute with your own tools, and give the answer back."
        ),
    )


def guard_shared_agent_shell(handler: Any, *, tools: Any = None) -> Any:
    """*handler*, with the D85 shell refusal and the H-236 egress refusal in front.

    *tools* returns the session's own tool list, for the H-236 check of a
    custom-tool request (:func:`is_egress_request`). The one install function
    (``_copilot_session._install_copilot_permission_handler``) passes it.

    It refuses every shell request of a run whose host shell is refused, and
    every egress request (:func:`is_egress_request`) of a ``no_egress`` run.
    It passes every other request to *handler* unchanged. It holds in EVERY
    ``AGENT_PERMISSION_MODE``, and whatever handler the agent's factory set.
    Production runs ``enforce`` (the box's ``.env`` sets no mode, read on
    2026-10-03). The guard still ignores the mode, so a later switch to
    ``audit`` or ``approve_all`` cannot waive an owner decision. Wrapping
    twice is a no-op.
    """
    if getattr(handler, "__cc_d85_guard__", False):
        return handler

    @functools.wraps(handler)
    def _guarded(request: Any, invocation: Any) -> Any:
        from acb_skills.egress import no_egress_for_this_run

        if no_egress_for_this_run() and is_egress_request(request, tools):
            _log.info(
                "permission.decision",
                mode=_mode(),
                approved=False,
                would_deny=True,
                reason="no_egress",
                detail=str(_field(request, "kind") or "")[:40],
                surface="copilot_no_egress",
            )
            return _egress_withheld_result()
        if is_shell_request(request) and host_shell_refused_for_this_run():
            command = str(_field(request, "full_command_text") or "")
            _log.info(
                "permission.decision",
                mode=_mode(),
                approved=False,
                would_deny=True,
                reason="host_shell_refused",
                detail=command[:200],
                surface="copilot_shell",
            )
            return _shell_withheld_result()
        return handler(request, invocation)

    _guarded.__cc_d85_guard__ = True  # type: ignore[attr-defined]
    return _guarded


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

    # A binary read is not a permission refusal, so audit mode does not waive
    # it. An approval would only hand the model the raw bytes again.
    redirect = (not approved) and code == BINARY_READ_REASON
    denied_would = (not approved) and mode == "enforce"
    _log.info(
        "permission.decision",
        mode=mode,
        approved=(approved or mode == "audit") and not redirect,
        would_deny=not approved,
        reason=code,
        detail=detail,
    )
    if redirect:
        return _binary_read_result(detail)
    if denied_would:
        return _denied_result(f"{code}: {detail}")
    return _approved_result()
