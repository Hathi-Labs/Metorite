"""Coding skill for MAF agents — run saved scripts, author new ones.

Two tiers (chat_agent_framework_review §2: MAF is the framework, the Copilot
SDK is the coding ENGINE, exposed as a capability instead of standalone
agents):

* :func:`run_script` — zero-LLM execution of a script the agent already has
  in its workspace. Cheap, fast, no Copilot session.
* :func:`code_task` — a bounded, one-shot Copilot SDK session that writes /
  edits / tests scripts in the agent's workspace, following the
  manifest-first contract (``agent-data/SCRIPTS.md`` + ``agent-data/scripts/``).

Durability (the whole point): scripts live under ``agent-data/`` — a
blob-store-backed folder (Postgres authoritative, disk is a rehydratable
cache), so they survive restarts, redeploys (deploy `git reset --hard` never
touches the workspace, which lives outside the app dir), and volume wipes.
Copilot sessions write via the CLI's NATIVE file tools, which bypass the
write_artifact mirror — so :func:`code_task` finishes with an explicit sweep
that mirrors every changed file under ``agent-data/`` and ``outputs/`` into
the store. :func:`run_script` sweeps ``outputs/`` the same way for files the
script itself produced.

Execution environment (harness standards, permissions_sandbox_b6):
* scripts run with the workspace as cwd and are PATH-CONTAINED — the script
  path must resolve inside the workspace (``resolve_in_workspace``);
* the subprocess env is SCRUBBED — no gateway tokens / provider keys reach
  arbitrary script code (allowlist + secret-pattern filter);
* wall-clock timeouts on both tiers; output is size-capped.
This is process-level hygiene, not a container sandbox — the BO-7 container
sandbox remains the hardening path for untrusted code.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from acb_skills.write_artifact import (
    _WRITE_ARTIFACT_CONTEXT,
    mirror_to_blob_store,
    resolve_in_workspace,
)

# ── Execution limits ────────────────────────────────────────────────────────
_RUN_SCRIPT_TIMEOUT = float(os.environ.get("RUN_SCRIPT_TIMEOUT_SECONDS", "120"))
_OUTPUT_CAP = 8000  # chars of combined stdout/stderr returned to the model
# Files larger than this are not mirrored by the post-run sweep (the blob
# store is for scripts/reports, not gigabyte artifacts).
_SWEEP_MAX_BYTES = 2_000_000
_SWEEP_MAX_FILES = 200
# Kernel file timestamps come from a COARSE clock that can lag time.time() by
# a few ms — a file written right after the cutoff was captured can carry an
# mtime just BEFORE it and be missed by the sweep (seen live on CI). The
# cutoff is therefore slackened; re-mirroring a file modified moments before
# the run is harmless (the mirror is an idempotent write-through).
_SWEEP_MTIME_SLACK = 2.0

# Env allowlist for script subprocesses, plus a deny-pattern so nothing
# secret-shaped leaks even through allowed names.
_ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "PYTHONPATH")
_ENV_DENY_RE = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)", re.I)


def _workspace_root() -> Path | None:
    root = _WRITE_ARTIFACT_CONTEXT.get("workspace_root")
    return Path(root) if root else None


def _declared_integrations() -> list[str]:
    raw = _WRITE_ARTIFACT_CONTEXT.get("integrations")
    return [s for s in raw if isinstance(s, str)] if isinstance(raw, list) else []


def _script_env() -> dict[str, str]:
    """Minimal environment for script subprocesses: base allowlist + exactly
    the agent's DECLARED integrations' credentials.

    The base allowlist is secret-free (deny-pattern on top). On top of it, the
    canonical env vars of the integrations this agent declared in its
    ``config.json`` — and that resolved for this run — are passed through
    (``acb_skills.integrations.FIELD_TO_ENV``). So a script gets the Zoho
    token *its own agent* declared, but not the gateway master key or the DB URL.

    **MT-0a: the credential values come from the run's ContextVar binding**
    (``integrations.credential``), not from ``os.environ``. The executor used to
    export them into the process environment, which meant two overlapping runs
    shared them for the overlap window — a within-org concern under one tenant
    and a credential leak under two (`saas_multitenancy.md` §6.1). A ContextVar
    is per-task, so a concurrent run cannot widen what this script sees.

    Residual caveat, narrowed but not gone: the declared-*list*
    (``_WRITE_ARTIFACT_CONTEXT["integrations"]``) is still a process-global dict
    despite its docstring calling itself coroutine-local, so a concurrent run can
    still transiently widen *which names* are looked up. That is now much less
    dangerous than it was — ``credential()`` resolves against **this** context's
    binding, so a widened name list yields nothing unless this run also holds
    that credential. Making the declared-list itself a ContextVar is the
    remaining half; a true per-run boundary is the Tier-2 container env (MT-0c).
    """
    env = {
        k: v for k, v in os.environ.items()
        if k in _ENV_ALLOW and not _ENV_DENY_RE.search(k)
    }
    declared = _declared_integrations()
    if declared:
        try:
            from acb_skills.integrations import (
                credential,
                env_var_names,
            )
            for var in env_var_names(declared):
                val = credential(var)
                if val:
                    env[var] = val
        except ImportError:
            pass
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _cap(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    half = _OUTPUT_CAP // 2
    return (
        text[:half]
        + f"\n… [{len(text) - _OUTPUT_CAP} chars truncated] …\n"
        + text[-half:]
    )


async def _sweep_to_blob_store(
    root: Path, *, since: float, subdirs: tuple[str, ...] = ("agent-data", "outputs"),
) -> int:
    """Mirror files modified after *since* under *subdirs* into the blob store.

    Closes the durability gap for files written by NATIVE tools (the Copilot
    CLI, or a script's own writes), which bypass the write_artifact mirror.
    Best-effort: any failure leaves the on-disk file intact and is skipped.
    Returns the number of files mirrored.

    Containment: a swept file's REAL (symlink-resolved) path must stay inside
    *root*. ``rglob`` follows symlinked DIRECTORIES on Python < 3.13, so without
    this a ``code_task`` session (or script) that drops ``agent-data/x -> /``
    could smuggle host files — service-account keys, another agent's creds —
    into this agent's durable, downloadable blob store. The leaf ``is_symlink``
    check alone misses files reached through a symlinked parent.

    The tree walk + file reads (up to ``_SWEEP_MAX_FILES`` × ``_SWEEP_MAX_BYTES``)
    run OFF the event loop so a large sweep can't stall every concurrent run.
    """
    import asyncio

    cutoff = since - _SWEEP_MTIME_SLACK

    def _collect() -> list[tuple[str, bytes]]:
        root_r = root.resolve()
        out: list[tuple[str, bytes]] = []
        for sub in subdirs:
            base = root / sub
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if len(out) >= _SWEEP_MAX_FILES:
                    return out
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    # Real path must remain inside the workspace (blocks a
                    # symlinked-directory escape rglob would otherwise follow).
                    try:
                        p.resolve().relative_to(root_r)
                    except ValueError:
                        continue
                    st = p.stat()
                    if st.st_mtime < cutoff or st.st_size > _SWEEP_MAX_BYTES:
                        continue
                    out.append((p.relative_to(root).as_posix(), p.read_bytes()))
                except Exception:
                    continue
        return out

    try:
        collected = await asyncio.to_thread(_collect)
    except Exception:
        return 0
    mirrored = 0
    for rel, data in collected:
        try:
            await mirror_to_blob_store(rel, data, actor="agent")
            mirrored += 1
        except Exception:
            continue
    return mirrored


async def run_script(path: str, args: str = "") -> str:
    """Run a script that already exists in your workspace and return its output.

    Use this to RE-USE a script you (or a previous session) already created —
    it executes directly with no reasoning step, so it is fast and cheap.
    Check ``agent-data/SCRIPTS.md`` (via ``recall_notes``) for the catalog of
    available scripts. To CREATE or CHANGE a script, use ``code_task`` instead.

    Args:
        path: Workspace-relative script path, e.g.
              ``"agent-data/scripts/sales_report.py"`` (``.py`` or ``.sh``).
        args: Optional space-separated command-line arguments.

    Returns:
        Exit status plus captured stdout/stderr (size-capped). Files the
        script writes under ``outputs/`` are persisted and appear in the
        Files panel.
    """
    import asyncio
    import shlex
    import subprocess

    root = _workspace_root()
    if root is None:
        return "run_script failed: no active workspace for this run."
    target = resolve_in_workspace(root, (path or "").strip())
    if target is None:
        return f"run_script blocked: {path!r} escapes the workspace."
    if not target.is_file():
        return (
            f"run_script failed: {path!r} not found. Check agent-data/SCRIPTS.md "
            "for available scripts, or create it with code_task."
        )

    suffix = target.suffix.lower()
    if suffix == ".py":
        cmd = [sys.executable, str(target)]
    elif suffix == ".sh":
        cmd = ["bash", str(target)]
    else:
        return f"run_script failed: unsupported script type {suffix!r} (py|sh)."
    try:
        cmd += shlex.split(args or "")
    except ValueError as exc:
        return f"run_script failed: bad args: {exc}"

    started = time.time()

    def _exec() -> tuple[int | None, str, str]:
        try:
            r = subprocess.run(
                cmd,
                cwd=str(root),
                env=_script_env(),
                capture_output=True,
                text=True,
                timeout=_RUN_SCRIPT_TIMEOUT,
            )
            return r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired as exc:
            return (
                None,
                (exc.stdout or b"").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                f"[timed out after {_RUN_SCRIPT_TIMEOUT:.0f}s]",
            )

    code, out, err = await asyncio.to_thread(_exec)

    # Persist anything the script wrote (its own writes bypass the mirror).
    try:
        swept = await _sweep_to_blob_store(root, since=started, subdirs=("outputs",))
    except Exception:
        swept = 0

    status = "timed out" if code is None else f"exit {code}"
    body = _cap(
        ((out.strip() and f"stdout:\n{out.strip()}") or "")
        + ((err.strip() and f"\nstderr:\n{err.strip()}") or "")
    ) or "(no output)"
    tail = f"\n[{swept} output file(s) persisted]" if swept else ""
    return f"run_script {path} — {status}\n{body}{tail}"


def _commit_repo_changes(root: Path, task: str) -> str | None:
    """Commit any non-ignored working-tree changes a coding session left behind.

    The agent's workspace IS its git clone (``_resolve_effective_agent_dir``),
    so edits to TRACKED source — a repo-baked skill under ``skills/*/scripts/``,
    ``agents.py`` — must become a local commit: the executor's post-run commit
    scan (``_detect_agent_commits``) registers every local commit as a
    ``pending_commit`` row for human approval (the push guard blocks direct
    pushes), and the loader's next ``_pull_latest`` would otherwise stash-drop
    or hard-reset uncommitted tracked changes into oblivion. The session is
    instructed to commit its own repo edits; this is the fail-safe for when it
    doesn't. The runtime workspace dirs (``agent-data/``, ``inputs/``,
    ``outputs/``) are explicitly UNSTAGED after ``git add -A`` — so a secret a
    script wrote under ``agent-data/`` can never land in a commit even for an
    external ``workspace_root`` repo that never received the loader-managed
    ``.gitignore`` (the blob store, not git, is their durable home anyway).

    Returns the short SHA of the created commit, or ``None`` when the workspace
    is not a git repo, the tree is clean, or any git step fails (best-effort —
    never raises).
    """
    import subprocess

    if not (root / ".git").exists():
        return None

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=str(root),
            capture_output=True, text=True, timeout=30,
        )

    try:
        status = _git("status", "--porcelain")
        if status.returncode != 0 or not status.stdout.strip():
            return None
        _git("add", "-A")
        # Belt-and-suspenders: never commit runtime state, regardless of
        # whether this clone carries the loader's .gitignore. These dirs are
        # blob-store durable; committing them would both leak (a script's
        # secrets) and bloat the approval diff.
        _git("reset", "-q", "--", "agent-data", "inputs", "outputs")
        if not _git("diff", "--cached", "--name-only").stdout.strip():
            return None
        commit_cmd = ["commit", "-m", f"code_task: {task.strip()[:72]}"]
        if not _git("config", "user.email").stdout.strip():
            # Clones get the bot identity at clone time; this covers external
            # workspace_root repos that never went through the loader.
            commit_cmd = [
                "-c", "user.name=metorite-bot",
                "-c", "user.email=metorite-bot@users.noreply.github.com",
                *commit_cmd,
            ]
        if _git(*commit_cmd).returncode != 0:
            return None
        sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
        return sha or None
    except Exception:
        return None


async def code_task(task: str) -> str:
    """Write, edit, run, and test scripts in your workspace via a bounded
    coding session (the platform's coding engine).

    Use this when your built-in tools aren't enough — you need a program
    written, an existing script changed, or code executed-and-iterated until
    it works. The session follows the SCRIPT CONTRACT: it reads
    ``agent-data/SCRIPTS.md`` first, edits existing scripts in place, keeps
    reusable scripts under ``agent-data/scripts/``, and updates the manifest —
    so scripts accumulate as durable, reusable capability across sessions,
    restarts, and redeploys. To simply re-run an existing script, use the much
    cheaper ``run_script`` instead.

    This also covers your BUILT-IN skills: if one of your repo-baked skill
    scripts (``skills/*/scripts/``) is broken or needs a change, describe it —
    the session edits the source in place and the change is committed locally
    and queued for human approval (it goes live once approved). Workspace
    scripts under ``agent-data/`` need no approval.

    Args:
        task: What to build or change, with enough context to act — inputs,
              expected output, and the script name if editing an existing one.

    Returns:
        The session's report (what was created/changed, how to run it, key
        output), plus a persistence note for the files it touched.
    """
    root = _workspace_root()
    if root is None:
        return "code_task failed: no active workspace for this run."
    if not (task or "").strip():
        return "code_task failed: describe what to build or change."

    started = time.time()
    try:
        from orchestrator.code_session import run_copilot_code_session
    except ImportError:
        return (
            "code_task unavailable: the coding engine (orchestrator runtime) "
            "is not importable in this environment."
        )
    # Tell the session which integrations its scripts may use (env var NAMES
    # only — values flow through the scoped run env, never through the prompt).
    session_task = task
    declared = _declared_integrations()
    if declared:
        try:
            from acb_skills.integrations import FIELD_TO_ENV
            lines = [
                f"- {svc}: " + ", ".join(v for _, v in FIELD_TO_ENV.get(svc, []))
                for svc in declared
            ]
            session_task = (
                task
                + "\n\n[Platform] This agent's declared integrations are "
                "available to scripts via these environment variables (set at "
                "run time; read with os.getenv, never hard-code values):\n"
                + "\n".join(lines)
            )
        except ImportError:
            pass
    try:
        report = await run_copilot_code_session(
            task=session_task, workspace=str(root),
        )
    except Exception as exc:
        report = None
        error = f"{type(exc).__name__}: {exc}"
    # ALWAYS sweep — even a failed session may have written useful files, and
    # native CLI writes bypass the write_artifact mirror entirely.
    try:
        swept = await _sweep_to_blob_store(root, since=started)
    except Exception:
        swept = 0
    # Fail-safe: commit any repo-source edits the session left uncommitted so
    # the approval pipeline sees them and the next loader pull can't wipe them.
    import asyncio
    committed = await asyncio.to_thread(_commit_repo_changes, root, task)
    commit_note = (
        f"\n[repo changes committed locally as {committed} — queued for "
        "human approval before push]" if committed else ""
    )
    if report is None:
        return (
            f"code_task failed: {error}\n"
            + (f"[{swept} file(s) it wrote were still persisted]" if swept else "")
            + commit_note
        )
    tail = (
        f"\n\n[{swept} file(s) persisted to the durable store]" if swept else ""
    ) + commit_note
    return _cap(report) + tail
