"""Resident Agent Cache — clone once, pull on demand (Metorite v2).

Design change from original v2:
  Previously, every event triggered a full ``git clone`` (~2–5 s latency).
  Now repos are maintained as **persistent local clones** under
  ``{agents_clone_dir}/repos/{repo-name}/``.  On each ``load_agent`` call:

    1. If the clone does not exist → ``git clone`` (first use only).
    2. If it does exist → ``git pull --ff-only`` (fast, typically < 0.5 s).
    3. ``graph.py`` is imported with a **unique module name per run_id** so
       concurrent executions of the same agent never share Python module state.
    4. ``LoadedAgent.cleanup()`` removes only the sys.path injection and the
       in-memory module; the local clone is preserved for the next event.

Authentication — private repos (ADR-020):
  Primary:  ``GITHUB_TOKEN`` (PAT with ``repo`` scope) embedded in the clone
            URL and refreshed before every pull via ``git remote set-url``.
  Upgrade path (not yet active):  GitHub App (``GITHUB_APP_ID`` +
            ``GITHUB_APP_PRIVATE_KEY_PATH``) — short-lived installation tokens,
            scoped to the org.  Swap ``_get_auth_token()`` when ready.

Bot identity (ADR-021):
  Every local clone is configured with ``git config user.name`` /
  ``user.email`` from ``GITHUB_BOT_NAME`` / ``GITHUB_BOT_EMAIL``.
  This means any commit created inside the clone (by Self_Mutation_Node or
  eval scripts) carries the bot identity automatically.

Security note (ADR-020):
  Only repos under the configured ``github_org`` are ever cloned.  The
  ``agent`` field in an event payload is validated against an allowlist
  before this function is called.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("acb_skills.loader")


class AgentLoadError(Exception):
    """Raised when an agent or skill repo cannot be cloned or imported."""


# ---------------------------------------------------------------------------
# Persistent clone cache (module-level, lives for the process lifetime)
# ---------------------------------------------------------------------------

# Guards the _repo_locks dict itself (created lazily per repo name).
_cache_meta_lock = threading.Lock()
# Per-repo locks prevent concurrent clone/pull races for the same repo.
_repo_locks: dict[str, threading.Lock] = {}


def _get_repo_lock(repo_name: str) -> threading.Lock:
    with _cache_meta_lock:
        if repo_name not in _repo_locks:
            _repo_locks[repo_name] = threading.Lock()
        return _repo_locks[repo_name]


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _build_github_url(org: str, repo: str, token: str | None) -> str:
    """Return an authenticated (or public) GitHub HTTPS clone URL."""
    if token:
        return f"https://x-token:{token}@github.com/{org}/{repo}.git"
    return f"https://github.com/{org}/{repo}.git"


# Environment passed to every git subprocess: suppresses credential popups and
# GUI password prompts (GIT_TERMINAL_PROMPT=0, GCM_INTERACTIVE=never) so the
# process fails fast rather than hanging waiting for user input.
# PYTHONUTF8=1 + PYTHONIOENCODING=utf-8: ensure child Python processes use
# UTF-8 on Windows (avoids cp1252 UnicodeEncodeError for scripts with emoji/
# non-ASCII output — e.g. zoho_crm.py's pipeline summary headers).
_GIT_ENV: dict[str, str] = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "GCM_INTERACTIVE": "never",
    "GCM_CREDENTIAL_STORE": "plaintext",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}


def _run_git(args: list[str], *, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_GIT_ENV,
        timeout=timeout,
    )


def _clone_repo(url: str, dest: Path) -> None:
    """Full clone of *url* into *dest*.  Raises :class:`AgentLoadError` on failure."""
    # Remove a stale/partial directory that has no .git — git clone refuses to
    # clone into a non-empty directory, leaving agents in a broken state.
    if dest.exists() and not (dest / ".git").exists():
        import shutil
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", url, str(dest)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_GIT_ENV,
        timeout=120,
    )
    if result.returncode != 0:
        raise AgentLoadError(
            f"git clone failed for {url.split('@')[-1]!r}:\n{result.stderr.strip()}"
        )
    # If the checked-out working tree is empty (e.g. the default branch is an
    # empty placeholder while all code lives on another branch), try to checkout
    # the first remote branch that has actual files.
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(dest), capture_output=True, text=True, encoding="utf-8"
    )
    if not tracked.stdout.strip():
        branches_r = subprocess.run(
            ["git", "branch", "-r", "--sort=-committerdate"],
            cwd=str(dest), capture_output=True, text=True, encoding="utf-8",
        )
        for branch_line in branches_r.stdout.splitlines():
            branch = branch_line.strip()
            if "HEAD" in branch or not branch:
                continue
            co = subprocess.run(
                ["git", "checkout", branch, "--", "."],
                cwd=str(dest), capture_output=True, text=True, encoding="utf-8",
            )
            if co.returncode == 0:
                # Verify we actually got files
                verify = subprocess.run(
                    ["git", "ls-files"], cwd=str(dest), capture_output=True, text=True, encoding="utf-8"
                )
                if verify.stdout.strip():
                    _log.info("loader.fallback_branch", dest=dest.name, branch=branch)
                    break


def _install_push_guard(repo_dir: Path) -> None:
    """Install a pre-push hook so agents can't push without human approval.

    Written once on first clone.  The executor's post-run commit scan detects
    any commits the agent made locally and registers them as pending_commit rows
    for inbox approval.  This hook is the hard stop preventing a direct push.

    Skipped for local-only repos (no remote origin) — there is nothing to
    protect against pushing to.
    """
    try:
        hooks_dir = repo_dir / ".git" / "hooks"
        if not hooks_dir.is_dir():
            return
        hook_file = hooks_dir / "pre-push"
        if hook_file.exists():
            return

        # Check if this repo has a remote origin — local-only repos don't need
        # a push guard since there's nothing to push to.
        remote_check = _run_git(["remote", "get-url", "origin"], cwd=repo_dir, timeout=5)
        if remote_check.returncode != 0:
            _log.debug(
                "loader.push_guard_skipped_local",
                repo=repo_dir.name,
                hint="Local-only repo — no origin remote.",
            )
            return

        hook_file.write_text(
            "#!/bin/sh\n"
            "echo 'Direct push blocked: commits are queued for human approval'\n"
            "echo 'Approve via the Metorite Control Plane inbox.'\n"
            "exit 1\n",
            encoding="utf-8",
        )
        hook_file.chmod(0o755)
        _log.info("loader.push_guard_installed", repo=repo_dir.name)
    except Exception as exc:  # noqa: BLE001
        _log.warning("loader.push_guard_failed", repo=repo_dir.name, error=str(exc))


def _configure_bot_identity(repo_dir: Path, settings: Any) -> None:
    bot_name: str = getattr(settings, "github_bot_name", "metorite-bot")
    bot_email: str = getattr(settings, "github_bot_email", "") or f"{bot_name}@users.noreply.github.com"
    _run_git(["config", "user.name", bot_name], cwd=repo_dir)
    _run_git(["config", "user.email", bot_email], cwd=repo_dir)


def _refresh_remote_auth(repo_dir: Path, org: str, repo_name: str, token: str | None) -> None:
    """Update the remote URL so short-lived or rotated tokens stay current."""
    url = _build_github_url(org, repo_name, token)
    _run_git(["remote", "set-url", "origin", url], cwd=repo_dir)


def _resolve_rebase_conflicts(repo_dir: Path) -> bool:
    """Resolve rebase merge conflicts using the tier-powerful LLM.

    Scans the working tree for conflicted files (those containing
    ``<<<<<<<`` markers left by ``git rebase``).  For each conflicted
    file, sends the full content to the tier-3 LLM (powerful reasoning)
    via LiteLLM and writes back the resolved version.  Then stages all
    resolved files and runs ``git rebase --continue``.

    Returns True if all conflicts were resolved and the rebase continued
    successfully.  Returns False if the LLM is unavailable, the rebase
    --continue fails, or no conflicted files were found (unexpected).

    This is always best-effort — any exception is swallowed after logging
    and the caller falls back to ``--ours`` resolution.
    """
    # ── Find conflicted files ────────────────────────────────────────
    diff_result = _run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=repo_dir, timeout=10,
    )
    if diff_result.returncode != 0:
        _log.warning(
            "loader.conflict_scan_failed",
            repo=repo_dir.name,
            stderr=diff_result.stderr.strip()[:200],
        )
        return False

    conflicted = [
        line.strip() for line in diff_result.stdout.splitlines()
        if line.strip()
    ]
    if not conflicted:
        _log.debug(
            "loader.no_conflicts_found",
            repo=repo_dir.name,
            hint="Rebase reported conflict but no U files in diff.",
        )
        return False

    _log.info(
        "loader.conflicts_detected",
        repo=repo_dir.name,
        count=len(conflicted),
        files=conflicted,
    )

    # ── Resolve each conflicted file with the LLM ────────────────────
    resolved_count = 0
    for rel_path in conflicted:
        full_path = repo_dir / rel_path
        if not full_path.is_file():
            _log.warning(
                "loader.conflict_file_missing",
                repo=repo_dir.name,
                file=rel_path,
            )
            continue

        try:
            raw_content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _log.warning(
                "loader.conflict_file_unreadable",
                repo=repo_dir.name,
                file=rel_path,
                error=str(exc),
            )
            continue

        # Skip binary-looking or empty files
        if not raw_content.strip() or "\0" in raw_content:
            _log.debug(
                "loader.conflict_file_skipped",
                repo=repo_dir.name,
                file=rel_path,
                hint="Empty or binary.",
            )
            continue

        resolved = _call_llm_for_merge_resolution(
            repo_name=repo_dir.name,
            file_path=rel_path,
            conflict_content=raw_content,
        )
        if resolved is None:
            _log.warning(
                "loader.llm_resolution_failed",
                repo=repo_dir.name,
                file=rel_path,
                hint="LLM call failed; will fall back to --ours.",
            )
            return False  # One failure → fall back entirely

        try:
            full_path.write_text(resolved, encoding="utf-8")
            _run_git(["add", rel_path], cwd=repo_dir, timeout=10)
            resolved_count += 1
            _log.info(
                "loader.conflict_resolved_by_llm",
                repo=repo_dir.name,
                file=rel_path,
            )
        except OSError as exc:
            _log.warning(
                "loader.conflict_write_failed",
                repo=repo_dir.name,
                file=rel_path,
                error=str(exc),
            )
            return False

    if resolved_count == 0:
        return False

    # ── Continue the rebase ──────────────────────────────────────────
    continue_result = _run_git(
        ["rebase", "--continue"],
        cwd=repo_dir, timeout=30,
    )
    if continue_result.returncode == 0:
        _log.info(
            "loader.rebase_continued_after_llm",
            repo=repo_dir.name,
            resolved=resolved_count,
        )
        return True

    _log.warning(
        "loader.rebase_continue_failed_after_llm",
        repo=repo_dir.name,
        stderr=continue_result.stderr.strip()[:300],
        hint="Aborting rebase; caller will fall back to --ours.",
    )
    _run_git(["rebase", "--abort"], cwd=repo_dir, timeout=15)
    return False


def _call_llm_for_merge_resolution(
    *,
    repo_name: str,
    file_path: str,
    conflict_content: str,
) -> str | None:
    """Send a conflicted file to the tier-powerful LLM for resolution.

    Uses the ``complete()`` function from ``acb_llm`` to call the tier-3
    (powerful reasoning) model.  The prompt instructs the model to resolve
    the conflict markers intelligently, preserving both the upstream
    improvements and the local pending changes.

    Returns the resolved file content as a string, or None if the LLM
    call fails.
    """
    import asyncio as _asyncio  # noqa: PLC0415

    try:
        from acb_llm import LLMTier, complete  # noqa: PLC0415
    except ImportError:
        _log.warning(
            "loader.llm_import_failed",
            hint="acb_llm not available; skipping LLM resolution.",
        )
        return None

    # Truncate extremely large files to avoid token overflow.
    max_chars = 30_000
    if len(conflict_content) > max_chars:
        truncation_note = (
            f"\n\n[TRUNCATED: original file was "
            f"{len(conflict_content)} chars; "
            f"showing first {max_chars} chars]\n"
        )
        content_for_llm = conflict_content[:max_chars] + truncation_note
    else:
        content_for_llm = conflict_content

    system_prompt = (
        "You are a senior software engineer resolving a git merge conflict. "
        "The file below contains conflict markers (<<<<<<<, =======, >>>>>>>). "
        "The section between <<<<<<< and ======= is the LOCAL change (our "
        "pending commits). The section between ======= and >>>>>>> is the "
        "REMOTE change (incoming upstream update).\n\n"
        "Your task: resolve the conflict by producing the CORRECT merged "
        "version of the file. Preserve the intent of BOTH changes when "
        "possible. If the changes are incompatible, prefer the remote "
        "(upstream) change for infrastructure/config and the local change "
        "for business logic/agent code.\n\n"
        "IMPORTANT: Return ONLY the resolved file content. Do NOT include "
        "any explanation, markdown fences, or commentary. Your entire "
        "response will be written directly to the file."
    )

    user_prompt = (
        f"Repository: {repo_name}\n"
        f"File: {file_path}\n\n"
        f"{content_for_llm}"
    )

    try:
        raw = _asyncio.get_event_loop().run_until_complete(
            complete(
                tier=LLMTier.TIER_3,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=16_384,
            )
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "loader.llm_call_failed",
            repo=repo_name,
            file=file_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None

    if not raw or not raw.strip():
        _log.warning(
            "loader.llm_empty_response",
            repo=repo_name,
            file=file_path,
        )
        return None

    # Strip markdown fences if the model wrapped the output
    resolved = raw.strip()
    if resolved.startswith("```"):
        lines = resolved.splitlines()
        # Remove opening fence line
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        resolved = "\n".join(lines)

    # Sanity check: the resolved file must not contain leftover conflict
    # markers (the LLM failed to resolve them).
    if any(marker in resolved for marker in ("<<<<<<<", "=======", ">>>>>>>")):
        _log.warning(
            "loader.llm_incomplete_resolution",
            repo=repo_name,
            file=file_path,
            hint="LLM returned content still containing conflict markers.",
        )
        return None

    _log.info(
        "loader.llm_resolution_success",
        repo=repo_name,
        file=file_path,
        original_len=len(conflict_content),
        resolved_len=len(resolved),
    )
    return resolved


def _pull_latest(repo_dir: Path) -> dict[str, Any]:
    """Pull the latest commits from origin while preserving local commits.

    Strategy (in order):

    1. Fast-forward only (``git pull --ff-only``) — clean, no conflicts.
    2. If that fails AND there are NO local-only commits (i.e. our HEAD
       equals origin/HEAD), stash uncommitted changes, reset hard to
       ``origin/HEAD``, drop stash.  This cleans up leftover agent
       workspace dirt without losing anything of value.
    3. If there ARE local-only commits (pending approval), try
       ``git pull --rebase`` which replays our local commits on top of
       the latest remote HEAD.  If rebase hits a merge conflict,
       attempt LLM-powered resolution first, then fall back to
       ``--ours`` (our pending commits take priority).
    4. If all strategies fail, the run proceeds with the cached clone
       and logs a warning — we never destroy pending commits.

    The pull is always non-fatal.

    Returns:
        A dict with keys:
        - ``strategy``: "fast-forward", "hard-reset", "rebase-clean",
          "rebase-llm", "rebase-ours", "rebase-aborted", or "skipped"
        - ``conflicts_resolved_by_llm``: bool
    """
    result_info: dict[str, Any] = {
        "strategy": "skipped",
        "conflicts_resolved_by_llm": False,
    }

    # Step 1 — fast-forward (best case: no divergence)
    result = _run_git(["pull", "--ff-only"], cwd=repo_dir, timeout=30)
    if result.returncode == 0:
        result_info["strategy"] = "fast-forward"
        return result_info

    _log.info(
        "loader.pull_ff_failed",
        repo=repo_dir.name,
        stderr=result.stderr.strip()[:200],
        hint="Checking for local-only commits before falling back.",
    )

    # ── Fetch origin so we can compare HEAD positions ──────────────────
    fetch_result = _run_git(["fetch", "origin"], cwd=repo_dir, timeout=30)
    if fetch_result.returncode != 0:
        _log.warning(
            "loader.pull_fetch_failed",
            repo=repo_dir.name,
            stderr=fetch_result.stderr.strip()[:200],
            hint="Using cached clone.",
        )
        return result_info

    # ── Detect local-only (unpushed) commits ───────────────────────────
    # git rev-list origin/HEAD..HEAD lists commits we have that origin doesn't.
    # If there are any, we MUST NOT hard-reset — that would destroy pending
    # mutation commits awaiting approval.
    has_local_commits = False
    try:
        rev_list_result = _run_git(
            ["rev-list", "--count", "origin/HEAD..HEAD"],
            cwd=repo_dir, timeout=10,
        )
        if rev_list_result.returncode == 0 and rev_list_result.stdout.strip() not in ("", "0"):
            count = int(rev_list_result.stdout.strip())
            if count > 0:
                has_local_commits = True
                _log.info(
                    "loader.local_commits_detected",
                    repo=repo_dir.name,
                    count=count,
                    hint="Preserving pending commits — using rebase instead of reset.",
                )
    except (ValueError, Exception):
        pass  # Can't determine — proceed cautiously

    if has_local_commits:
        # ── Step 3: Rebase local commits on top of origin/HEAD ─────────
        # This replays our pending commits onto the latest remote code.
        # If conflicts arise, attempt LLM-powered resolution first,
        # then fall back to --ours if the LLM is unavailable.
        rebase_result = _run_git(
            ["rebase", "origin/HEAD"],
            cwd=repo_dir, timeout=60,
        )
        if rebase_result.returncode == 0:
            _log.info("loader.pull_rebase_ok", repo=repo_dir.name)
            result_info["strategy"] = "rebase-clean"
            return result_info

        # ── Rebase conflict — attempt LLM-powered resolution ─────────
        _log.warning(
            "loader.pull_rebase_conflict",
            repo=repo_dir.name,
            stderr=rebase_result.stderr.strip()[:200],
            hint="Attempting LLM-powered conflict resolution.",
        )
        llm_resolved = _resolve_rebase_conflicts(repo_dir)
        if llm_resolved:
            _log.info(
                "loader.pull_rebase_llm_resolved",
                repo=repo_dir.name,
                hint="Conflicts resolved via LLM; pending commits preserved.",
            )
            result_info["strategy"] = "rebase-llm"
            result_info["conflicts_resolved_by_llm"] = True
            return result_info

        # LLM unavailable or failed — fall back to --ours
        _log.warning(
            "loader.pull_rebase_llm_failed",
            repo=repo_dir.name,
            hint="LLM resolution failed; falling back to checkout --ours.",
        )
        _run_git(["checkout", "--ours", "."], cwd=repo_dir, timeout=15)
        _run_git(["add", "-A"], cwd=repo_dir, timeout=15)
        continue_result = _run_git(
            ["rebase", "--continue"],
            cwd=repo_dir, timeout=30,
        )
        if continue_result.returncode == 0:
            _log.info(
                "loader.pull_rebase_conflict_resolved",
                repo=repo_dir.name,
                hint="Conflicts auto-resolved with --ours; pending commits preserved.",
            )
            result_info["strategy"] = "rebase-ours"
            return result_info

        # Rebase failed even after conflict resolution — abort and keep
        # the local state intact.
        _run_git(["rebase", "--abort"], cwd=repo_dir, timeout=15)
        _log.warning(
            "loader.pull_rebase_aborted",
            repo=repo_dir.name,
            stderr=continue_result.stderr.strip()[:200],
            hint="Rebase failed; using cached clone with pending commits.",
        )
        result_info["strategy"] = "rebase-aborted"
        return result_info

    # ── Step 2: No local commits — safe to hard-reset ──────────────────
    # Stash uncommitted changes (workspace dirt from a failed sandbox run),
    # reset to origin/HEAD, drop the stash.
    stash_result = _run_git(
        ["stash", "--include-untracked", "-m", "metorite-auto-stash"],
        cwd=repo_dir, timeout=15,
    )
    stashed = stash_result.returncode == 0 and "No local changes" not in stash_result.stdout

    reset_result = _run_git(["reset", "--hard", "origin/HEAD"], cwd=repo_dir, timeout=15)
    if reset_result.returncode != 0:
        _log.warning(
            "loader.pull_reset_failed",
            repo=repo_dir.name,
            stderr=reset_result.stderr.strip()[:200],
            hint="Using cached clone.",
        )
        if stashed:
            _run_git(["stash", "pop"], cwd=repo_dir, timeout=15)
        return result_info

    # Stash dropped — uncommitted workspace dirt is discarded (it will be
    # regenerated on the next mutation run if needed).
    if stashed:
        _run_git(["stash", "drop"], cwd=repo_dir, timeout=10)

    _log.info("loader.pull_reset_ok", repo=repo_dir.name)
    result_info["strategy"] = "hard-reset"
    return result_info


def _ensure_local_git_repo(source_dir: Path, cache_dir: Path, settings: Any) -> None:
    """Initialise or sync a local-only git-tracked working copy of *source_dir*.

    This gives pure MAF agents (without GitHub remotes) the same version-control
    benefits as GitHub-sourced agents:

    - **Local git tracking** — every change is versioned, committable, and revertible.
    - **Mutation sandbox compatibility** — the cache dir is a proper git repo
      that can be mounted into the Docker sandbox.
    - **Inbox approval flow** — commits are registered for human review.
      Approve → keep.  Reject → ``git reset --hard HEAD~1``.

    Sync strategy (lightweight copy):
        On each call, the source files are copied to the cache.  Only files that
        differ are overwritten (timestamp + size check).  Files in the cache that
        don't exist in the source are left untouched (they may be agent-generated
        improvements awaiting approval).

    Git initialisation (first call only):
        If the cache directory has no ``.git``, a new repo is initialised and an
        initial commit is created as a baseline for ``git reset`` rollback.

    Args:
        source_dir: The agent's source directory (e.g. ``C:/dev/my-agent/``).
        cache_dir:  The persistent cache directory
                    (e.g. ``{agents_clone_dir}/repos/my-agent/``).
        settings:   The application settings object.

    Raises:
        :class:`AgentLoadError` if the cache directory cannot be created or git
        operations fail.
    """
    import shutil
    import filecmp

    cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Sync source → cache ─────────────────────────────────────────────
    _sync_source_to_cache(source_dir, cache_dir)

    # Keep generated workspace dirs untracked so a mutation reset can't wipe
    # them and outputs/ survives redeploys (runs before the baseline commit).
    _ensure_workspace_gitignore(cache_dir)

    # ── Initialise local git repo (first call only) ──────────────────────
    git_dir = cache_dir / ".git"
    if not git_dir.is_dir():
        _log.info("loader.local_git_init", agent=cache_dir.name, source=str(source_dir))
        result = _run_git(["init"], cwd=cache_dir, timeout=15)
        if result.returncode != 0:
            raise AgentLoadError(
                f"git init failed for {cache_dir}: {result.stderr.strip()}"
            )
        _configure_bot_identity(cache_dir, settings)

        # Stage everything and create the initial commit as a rollback baseline.
        _run_git(["add", "-A"], cwd=cache_dir, timeout=15)
        commit_result = _run_git(
            ["commit", "-m", "initial: seeded from local source"],
            cwd=cache_dir, timeout=15,
        )
        if commit_result.returncode != 0:
            # "nothing to commit" is fine — empty repo or all files ignored.
            _log.debug(
                "loader.local_git_init_empty_commit",
                agent=cache_dir.name,
                stderr=commit_result.stderr.strip()[:100],
            )
        _log.info("loader.local_git_ready", agent=cache_dir.name)


_WORKSPACE_GITIGNORE_MARKER = "# Metorite agent workspace (auto-managed)"
_WORKSPACE_GITIGNORE_BLOCK = f"""
{_WORKSPACE_GITIGNORE_MARKER}
# Agent-generated deliverables are runtime state, never source. Keeping them
# untracked means the local-git mutation flow (git add -A / git reset --hard
# on rejection) can never stage or wipe them, so outputs/ persists across
# redeploys, reboots, and self-mutation rollbacks.
outputs/
inputs/
agent-data/
"""


def _ensure_workspace_gitignore(cache_dir: Path) -> None:
    """Guarantee the cache clone ignores the agent-generated workspace dirs.

    ``outputs/`` (plus ``inputs/`` and ``agent-data/``) hold runtime deliverables,
    not source. If they were git-tracked, the mutation sandbox's ``git add -A``
    would stage them and a rejection's ``git reset --hard HEAD~1`` — or any other
    hard reset — could delete a user's generated files. This appends an ignore
    block (idempotent, marker-guarded) to the clone's ``.gitignore`` so the three
    dirs stay untracked and durable, even for GitHub-sourced agents whose repo
    never shipped a ``.gitignore``.
    """
    try:
        gi = cache_dir / ".gitignore"
        existing = gi.read_text(encoding="utf-8", errors="replace") if gi.exists() else ""
        if _WORKSPACE_GITIGNORE_MARKER in existing:
            return  # already managed
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        gi.write_text(existing + sep + _WORKSPACE_GITIGNORE_BLOCK, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.warning("loader.workspace_gitignore_failed", agent=cache_dir.name, error=str(exc))


def _sync_source_to_cache(source_dir: Path, cache_dir: Path) -> None:
    """Copy files from *source_dir* to *cache_dir* that are new or changed.

    Only overwrites files whose modification time OR size differs.
    Files in the cache that don't exist in the source are left untouched
    (preserving agent-generated improvements).
    """
    import shutil

    for src_file in source_dir.rglob("*"):
        if src_file.is_dir():
            continue
        # Skip git internals, bytecode, and env files
        if any(p.startswith(".") for p in src_file.parts if p not in (".", "..")):
            if ".git" in src_file.parts or "__pycache__" in src_file.parts:
                continue
        if src_file.name.endswith(".pyc") or src_file.name == ".env":
            continue

        rel = src_file.relative_to(source_dir)
        dst_file = cache_dir / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        if not dst_file.exists():
            shutil.copy2(src_file, dst_file)
        else:
            src_stat = src_file.stat()
            dst_stat = dst_file.stat()
            if (src_stat.st_mtime != dst_stat.st_mtime
                    or src_stat.st_size != dst_stat.st_size):
                shutil.copy2(src_file, dst_file)


def _sync_new_skills(agent_dir: Path, settings: Any) -> None:
    """Detect scripts in skills/*/scripts/ not yet referenced in agents.py and add wrappers.

    After each git pull, scans the agent repo for Python scripts that don't yet
    appear as tool functions in agents.py.  For each new script, injects an async
    subprocess wrapper and adds it to tools=[...].  Commits and pushes directly to
    the current branch so the update is live on the next run.

    Non-fatal — if agents.py doesn't exist, has no tools=[...], or the push fails,
    the loader continues normally; the sync will be retried on the next pull.
    """
    import re  # noqa: PLC0415

    agents_file = agent_dir / "agents.py"
    if not agents_file.exists():
        return

    current = agents_file.read_text(encoding="utf-8", errors="replace")

    # Discover scripts in skills/*/scripts/ whose stem doesn't appear in agents.py
    new_scripts: list[Path] = []
    for script in sorted(agent_dir.glob("skills/*/scripts/*.py")):
        if script.stem.startswith("_"):
            continue
        if script.stem not in current:
            new_scripts.append(script)

    if not new_scripts:
        return

    _log.info(
        "loader.auto_sync_new_skills",
        agent=agent_dir.name,
        new=[s.stem for s in new_scripts],
    )

    # Build async tool wrappers (subprocess pattern — safe for scripts with own deps).
    # PYTHONUTF8=1 + PYTHONIOENCODING=utf-8 force UTF-8 on Windows (avoids cp1252
    # UnicodeEncodeError when scripts print emoji or non-ASCII characters).
    tool_defs: list[str] = []
    tool_names: list[str] = []
    for script in new_scripts:
        fn = re.sub(r"[^a-z0-9_]", "_", script.stem.lower())
        rel = script.relative_to(agent_dir).as_posix()
        skill_name = script.parent.parent.name
        tool_defs.append(
            f"\nasync def {fn}(*args: str) -> str:\n"
            f'    """Run {script.stem} ({skill_name}). Pass CLI args as strings."""\n'
            f"    import asyncio as _a, os as _os, subprocess as _s, sys as _sys\n"
            f"    from pathlib import Path as _P\n"
            f"    _d = _P(__file__).parent.resolve()\n"
            f'    _cmd = [_sys.executable, "-X", "utf8", str(_d / "{rel}")] + list(args)\n'
            f"    _env = {{**_os.environ, \"PYTHONUTF8\": \"1\", \"PYTHONIOENCODING\": \"utf-8\"}}\n"
            f"    _r = await _a.to_thread(\n"
            f"        _s.run, _cmd, capture_output=True, encoding=\"utf-8\", errors=\"replace\",\n"
            f"        cwd=str(_d), env=_env,\n"
            f"    )\n"
            f"    if _r.returncode != 0:\n"
            f"        raise RuntimeError(_r.stderr[:500] or \"Script exited non-zero\")\n"
            f"    return _r.stdout or \"(no output)\"\n"
        )
        tool_names.append(fn)

    # Insert new tool defs immediately before build_agents()
    marker = "\ndef build_agents("
    if marker not in current:
        return  # unexpected structure — skip silently

    auto_block = "\n# ── Auto-synced tools (added by Metorite) ──────────────────────────\n"
    auto_block += "".join(tool_defs)
    updated = current.replace(marker, auto_block + marker, 1)

    # Append new tool names to the tools=[...] list inside build_agents
    def _add_to_tools(m: re.Match) -> str:  # type: ignore[type-arg]
        inner = m.group(1).strip().rstrip(",")
        additions = ",\n            ".join(tool_names)
        sep = ",\n            " if inner else ""
        return f"tools=[{inner}{sep}{additions}]"

    updated = re.sub(r"tools=\[([^\]]*)\]", _add_to_tools, updated, count=1)

    agents_file.write_text(updated, encoding="utf-8")

    # Commit locally (no push) — the pending_commit row will appear in the inbox
    bot_name: str = getattr(settings, "github_bot_name", "metorite-bot")
    bot_email: str = getattr(settings, "github_bot_email", "") or f"{bot_name}@users.noreply.github.com"
    _run_git(["config", "user.name", bot_name], cwd=agent_dir)
    _run_git(["config", "user.email", bot_email], cwd=agent_dir)
    _run_git(["add", "agents.py"], cwd=agent_dir)
    names_summary = ", ".join(s.stem for s in new_scripts[:5])
    if len(new_scripts) > 5:
        names_summary += f" (+{len(new_scripts) - 5} more)"
    commit_msg = (
        f"auto-sync: add {len(new_scripts)} new tool(s) to agents.py\n\nAdded: {names_summary}"
    )
    commit_r = _run_git(
        ["commit", "-m", commit_msg],
        cwd=agent_dir,
        timeout=30,
    )
    if commit_r.returncode == 0:
        # Read the commit SHA so we can register it in the pending_commit table
        sha_r = _run_git(["rev-parse", "HEAD"], cwd=agent_dir, timeout=10)
        commit_sha = sha_r.stdout.strip() if sha_r.returncode == 0 else ""
        _log.info(
            "loader.auto_sync_committed",
            agent=agent_dir.name,
            tools=tool_names,
            commit_sha=commit_sha,
            hint="Awaiting approval in inbox before push.",
        )
        if commit_sha:
            _register_sync_commit_async(
                agent_name=agent_dir.name,
                local_clone_dir=str(agent_dir),
                commit_sha=commit_sha,
                commit_message=commit_msg,
                tool_names=tool_names,
            )
    else:
        # Nothing staged (e.g. git noticed no effective diff) — restore original
        _run_git(["checkout", "--", "agents.py"], cwd=agent_dir)


def _register_sync_commit_async(
    *,
    agent_name: str,
    local_clone_dir: str,
    commit_sha: str,
    commit_message: str,
    tool_names: list[str],
) -> None:
    """Fire-and-forget: insert a pending_commit row for this auto-sync commit.

    Uses a background thread so the sync function stays synchronous and
    non-blocking from the caller's perspective.
    """
    import threading  # noqa: PLC0415

    def _do() -> None:
        try:
            import uuid  # noqa: PLC0415
            from acb_graph import get_session  # noqa: PLC0415
            from sqlalchemy import text  # noqa: PLC0415

            row_id = str(uuid.uuid4())
            with get_session() as sess:
                sess.execute(
                    text(
                        "INSERT INTO pending_commit "
                        "(id, agent_name, run_id, local_clone_dir, commit_sha, "
                        " commit_message, diff_text, test_summary, status) "
                        "VALUES (:id, :agent_name, :run_id, :local_clone_dir, :commit_sha, "
                        "        :commit_message, :diff_text, :test_summary, 'pending')"
                    ),
                    {
                        "id": row_id,
                        "agent_name": agent_name,
                        "run_id": f"auto-sync-{commit_sha[:8]}",
                        "local_clone_dir": local_clone_dir,
                        "commit_sha": commit_sha,
                        "commit_message": commit_message,
                        "diff_text": "",  # diff available via git in clone dir
                        "test_summary": f"auto-sync: {', '.join(tool_names[:5])}",
                    },
                )
                sess.commit()
            _log.info(
                "loader.sync_commit_registered",
                agent=agent_name,
                pending_commit_id=row_id,
            )
        except Exception as exc:
            # DB write failure is non-fatal — the commit still exists locally
            _log.warning("loader.sync_register_failed", agent=agent_name, error=str(exc))

    threading.Thread(target=_do, daemon=True).start()


def _ensure_repo(
    repo_name: str,
    *,
    org: str,
    token: str | None,
    cache_root: Path,
    settings: Any,
    clone_as: str | None = None,
) -> Path:
    """Return a path to an up-to-date local clone of *org/repo_name*.

    Args:
        repo_name:  The GitHub repository name (e.g. ``"sales-prospector"``).
        clone_as:   Local folder name override. Defaults to *repo_name*.
                    Use this when the GitHub repo name differs from the logical
                    agent name — e.g. clone ``"sales-prospector"`` into the
                    ``"sales-prospector"`` folder even when the agent_name is
                    ``"sales-prospector"`` (no ``agent-`` prefix on GitHub).

    Thread-safe: a per-repo lock ensures only one thread clones/pulls at a time.
    All other concurrent callers wait and then reuse the refreshed clone.
    """
    local_name = clone_as or repo_name
    lock = _get_repo_lock(local_name)
    with lock:
        clone_dir = cache_root / local_name
        if clone_dir.exists() and (clone_dir / ".git").exists():
            # Already cloned — refresh auth token in remote URL, then pull
            _refresh_remote_auth(clone_dir, org, repo_name, token)
            _pull_latest(clone_dir)
        else:
            # First use — full clone
            _log.info("loader.clone_new", repo=repo_name, org=org, local=local_name)
            url = _build_github_url(org, repo_name, token)
            _clone_repo(url, clone_dir)
            _configure_bot_identity(clone_dir, settings)
            _install_push_guard(clone_dir)
        # Keep generated workspace dirs untracked (idempotent) so a mutation
        # reset can't wipe them and outputs/ persists across redeploys.
        _ensure_workspace_gitignore(clone_dir)
        # After every pull/clone, auto-sync any new skill scripts into agents.py
        _sync_new_skills(clone_dir, settings)
        # Install the repo's declared deps into the SHARED gateway venv — agents
        # are imported in-process, so their imports must resolve in the same
        # interpreter the gateway runs from.
        _install_agent_deps(clone_dir, settings)
        return clone_dir


# ---------------------------------------------------------------------------
# Dependency installation (shared venv)
# ---------------------------------------------------------------------------

def _is_platform_dep(spec: str) -> bool:
    """True for first-party platform packages already present in the venv.

    These (``acb-*``, ``agent-framework-*``, ``copilot``) ship with the
    Metorite workspace, so trying to ``pip install`` them from an agent's
    pyproject would fail (they're not on PyPI) — skip them.
    """
    import re  # noqa: PLC0415
    name = re.split(r"[<>=!~;\s\[]", spec.strip(), 1)[0].strip().lower()
    return (
        name.startswith("acb")
        or name.startswith("agent-framework")
        or name == "copilot"
    )


def _find_uv() -> str | None:
    """Locate the ``uv`` binary, even when it's not on the service PATH."""
    import shutil  # noqa: PLC0415
    found = shutil.which("uv")
    if found:
        return found
    for cand in (
        Path.home() / ".local" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
        Path("/root/.local/bin/uv"),
    ):
        try:
            if cand.is_file():
                return str(cand)
        except Exception:  # noqa: BLE001
            continue
    return None


def _detect_system_packages(error: str) -> list[str]:
    """Map a pip/uv build failure to the apt packages that would fix it.

    A package with a native extension and no prebuilt wheel must be compiled,
    which needs a toolchain (and sometimes -dev headers) that a slim server
    may lack.  We scan the build error for the tell-tale signs.
    """
    e = (error or "").lower()
    out: list[str] = []

    def _add(pkg: str) -> None:
        if pkg not in out:
            out.append(pkg)

    if any(s in e for s in (
        "gcc", "g++", "cc1", "x86_64-linux-gnu-gcc",
        "failed building wheel", "command 'cc' failed",
        "unable to find vcvarsall", "microsoft visual c++",
    )):
        _add("build-essential")
    if "python.h" in e or "python3-dev" in e:
        _add("python3-dev")
    if "ffi.h" in e or "libffi" in e:
        _add("libffi-dev")
    if "ssl.h" in e or "openssl/" in e:
        _add("libssl-dev")
    if "libgl.so" in e or "libgl1" in e:
        _add("libgl1")
    if "tesseract" in e:
        _add("tesseract-ocr")
    if "cargo" in e or "rustc" in e:
        _add("cargo")
    return out


def _write_dep_status(
    agent_dir: Path,
    *,
    ok: bool,
    error: str,
    needs_system: list[str],
    has_requirements: bool,
    pyproject_dep_count: int,
) -> None:
    """Persist the dependency-install result to ``.git/acb-deps-status.json``
    so the agents page can surface unmet dependencies."""
    try:
        status = {
            "ok": ok,
            "error": error,
            "needs_system_packages": needs_system,
            "has_requirements": bool(has_requirements),
            "pyproject_dep_count": int(pyproject_dep_count),
        }
        git_dir = agent_dir / ".git"
        if git_dir.is_dir():
            (git_dir / "acb-deps-status.json").write_text(
                json.dumps(status), encoding="utf-8",
            )
    except Exception:  # noqa: BLE001
        pass


def read_dep_status(agent_dir: Path) -> dict[str, Any] | None:
    """Read an agent's persisted dependency-install status, or None.

    Public (no underscore) — consumed by the gateway's agent-list endpoint to
    show unmet-dependency warnings on the agents page.
    """
    try:
        f = Path(agent_dir) / ".git" / "acb-deps-status.json"
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return None


def _install_agent_deps(agent_dir: Path, settings: Any) -> None:
    """Install an agent's (or skill's) declared dependencies into the shared
    gateway venv.

    Agents are imported into the gateway interpreter and run in-process, so any
    third-party package an agent or its skills import must exist in the SAME
    venv the gateway runs from.  Nothing else installs them, so we do it here
    after every clone/pull.

    Sources: ``requirements.txt`` (installed verbatim) and the
    ``[project].dependencies`` of ``pyproject.toml`` (platform packages skipped).
    Installed into ``sys.executable``'s environment via ``uv pip install``
    (falling back to ``pip``).

    RCE guard (BO-7 fast pass): unless ``settings.agent_deps_allow_source_
    builds`` is explicitly set, installs run with ``--only-binary=:all:`` —
    wheels only. An sdist's ``setup.py``/PEP 517 build backend can run
    arbitrary code at install time, in the gateway's own process, before any
    tool-call permission gate is even relevant; a wheel install is just an
    unzip. This runs BEFORE ``agents.py`` is ever imported, so it is not
    covered by anything downstream in the loader — the wheel-only restriction
    is the actual boundary here, not a defense in depth on top of one.

    Best-effort + idempotent: a SHA-256 of the dep sources is cached in
    ``.git/acb-deps-hash``; an unchanged set is skipped.  A failed install logs
    a warning and never blocks the agent run.  Runs under the caller's per-repo
    lock, so concurrent loads can't race the installer.
    """
    import hashlib  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    try:
        req = agent_dir / "requirements.txt"
        pyproject = agent_dir / "pyproject.toml"
        sources: list[str] = []
        pyproject_deps: list[str] = []

        if req.is_file():
            sources.append(req.read_text(encoding="utf-8", errors="replace"))
        if pyproject.is_file():
            try:
                import tomllib  # noqa: PLC0415
                data = tomllib.loads(
                    pyproject.read_text(encoding="utf-8", errors="replace")
                )
                raw = (data.get("project") or {}).get("dependencies") or []
                pyproject_deps = [
                    d for d in raw
                    if isinstance(d, str) and not _is_platform_dep(d)
                ]
            except Exception:  # noqa: BLE001
                pyproject_deps = []
            sources.append("\n".join(pyproject_deps))

        if not req.is_file() and not pyproject_deps:
            return  # nothing declared to install

        digest = hashlib.sha256(
            "\x00".join(sources).encode("utf-8", "replace")
        ).hexdigest()
        marker = agent_dir / ".git" / "acb-deps-hash"
        try:
            if (
                marker.is_file()
                and marker.read_text(encoding="utf-8").strip() == digest
            ):
                return  # unchanged since the last successful install
        except Exception:  # noqa: BLE001
            pass

        uv = _find_uv()
        base = (
            [uv, "pip", "install", "--python", sys.executable]
            if uv
            else [sys.executable, "-m", "pip", "install"]
        )
        if not bool(getattr(settings, "agent_deps_allow_source_builds", False)):
            base = base + ["--only-binary", ":all:"]
        cmds: list[list[str]] = []
        if req.is_file():
            cmds.append(base + ["-r", str(req)])
        if pyproject_deps:
            cmds.append(base + pyproject_deps)

        ok = True
        errors: list[str] = []
        for cmd in cmds:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600,
                )
                if result.returncode != 0:
                    ok = False
                    err = (result.stderr or result.stdout or "")
                    errors.append(err)
                    _log.warning(
                        "loader.deps_install_failed",
                        agent=agent_dir.name,
                        tool="uv" if uv else "pip",
                        error=err[-700:],
                    )
            except Exception as exc:  # noqa: BLE001
                ok = False
                errors.append(str(exc))
                _log.warning(
                    "loader.deps_install_error",
                    agent=agent_dir.name, error=str(exc),
                )

        # Persist a machine-readable status so the agents page can surface
        # unmet dependencies (and any apt/system packages the build needs).
        joined_err = "\n".join(errors)
        needs_system = [] if ok else _detect_system_packages(joined_err)
        _write_dep_status(
            agent_dir,
            ok=ok,
            error="" if ok else joined_err[-1200:],
            needs_system=needs_system,
            has_requirements=req.is_file(),
            pyproject_dep_count=len(pyproject_deps),
        )

        if ok:
            try:
                marker.write_text(digest, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            _log.info(
                "loader.deps_installed",
                agent=agent_dir.name,
                requirements=req.is_file(),
                pyproject_deps=len(pyproject_deps),
            )
        else:
            _log.warning(
                "loader.deps_unmet",
                agent=agent_dir.name, needs_system=needs_system,
            )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "loader.deps_install_skipped",
            agent=agent_dir.name, error=str(exc),
        )


# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------

def _import_module_file(agent_dir: Path, filename: str, *, module_name: str) -> Any:
    """Load *filename* from *agent_dir* as an isolated Python module named *module_name*."""
    module_file = agent_dir / filename
    if not module_file.exists():
        raise AgentLoadError(f"No {filename} found in {agent_dir}")

    spec = importlib.util.spec_from_file_location(module_name, module_file)
    if spec is None or spec.loader is None:
        raise AgentLoadError(f"importlib could not build a spec for {module_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # register so relative imports inside the file work
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        del sys.modules[module_name]
        raise AgentLoadError(
            f"{filename} exec failed for module {module_name!r}: {exc}"
        ) from exc

    return module


def _import_graph_module(agent_dir: Path, *, module_name: str) -> Any:
    """Load ``graph.py`` from *agent_dir* (deprecated — prefer _import_module_file)."""
    return _import_module_file(agent_dir, "graph.py", module_name=module_name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class LoadedAgent:
    """Holds a successfully loaded agent for the duration of a single run.

    Use as a context manager::

        with load_agent("task-manager", run_id=run_id) as agent:
            graph = agent.build_graph()
            ...
        # sys.path entries removed, module unregistered.
        # The local clone on disk is preserved for the next event.

    Attributes:
        agent_name:  Bare agent name, e.g. ``"task-manager"``.
        run_id:      Unique execution ID.
        agent_dir:   Path to the local clone of ``agent-{agent_name}``.
                     Pass this to Self_Mutation_Node so it can push PRs
                     directly from the existing authenticated clone.
        config:      Parsed ``config.json`` dict (empty dict if absent/unparseable).
    """

    def __init__(
        self,
        agent_name: str,
        run_id: str,
        agent_dir: Path,
        graph_module: Any,
        injected_paths: list[str],
        module_name: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.run_id = run_id
        self.agent_dir = agent_dir  # exposed so mutation.py can push from here
        self.config: dict[str, Any] = config or {}
        self._graph_module = graph_module
        self._injected_paths = injected_paths
        self._module_name = module_name

    def build_agents(self) -> list[Any]:
        """Load ``agents.py`` and return its ``build_agents()`` result (MAF convention).

        Agent repos **must** export ``build_agents() -> list[Agent]`` in ``agents.py``.
        See ``_templates/agent-template/agents.py`` for the required structure.

        Raises:
            :class:`AgentLoadError` if ``agents.py`` is missing or does not export
            ``build_agents``.
        """
        agents_file = self.agent_dir / "agents.py"
        if not agents_file.exists():
            graph_file = self.agent_dir / "graph.py"
            if graph_file.exists():
                raise AgentLoadError(
                    f"Agent {self.agent_name!r} has graph.py but not agents.py. "
                    "This is a LangGraph-style agent that needs to be migrated. "
                    "Add agents.py exporting build_agents() -> list[Agent] using MAF. "
                    "See _templates/agent-template/agents.py for the required structure."
                )
            raise AgentLoadError(
                f"Agent {self.agent_name!r}: no agents.py found in {self.agent_dir}. "
                "Agent repos must export build_agents() -> list[Agent] in agents.py."
            )

        module_name = f"_agent_{self.run_id.replace('-', '_')}_agents"
        agents_module = _import_module_file(
            self.agent_dir, "agents.py", module_name=module_name
        )
        if not hasattr(agents_module, "build_agents"):
            raise AgentLoadError(
                f"Agent {self.agent_name!r}: agents.py must export a "
                "`build_agents()` function returning a list of MAF agents."
            )
        result = agents_module.build_agents()
        if not isinstance(result, list):
            raise AgentLoadError(
                f"Agent {self.agent_name!r}: build_agents() must return a list, "
                f"got {type(result).__name__}."
            )
        return result

    def build_graph(self) -> Any:
        """Deprecated — load ``graph.py`` and return its StateGraph.

        New agent repos should use ``build_agents()`` instead.
        """
        if self._graph_module is None:
            raise AgentLoadError(
                f"Agent {self.agent_name!r}: no graph.py (MAF agent — use build_agents() instead)."
            )
        if not hasattr(self._graph_module, "build_graph"):
            raise AgentLoadError(
                f"Agent {self.agent_name!r}: graph.py must export a "
                "`build_graph()` function returning a StateGraph."
            )
        return self._graph_module.build_graph()

    def cleanup(self) -> None:
        """Remove injected sys.path entries and unregister the run module.

        Does NOT delete the local clone — it is reused by future events.
        """
        sys.modules.pop(self._module_name, None)
        for p in self._injected_paths:
            try:
                sys.path.remove(p)
            except ValueError:
                pass

    def __enter__(self) -> "LoadedAgent":
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()


def load_agent(
    agent_name: str,
    *,
    run_id: str | None = None,
    repo_name: str | None = None,
    local_path: str | None = None,
) -> LoadedAgent:
    """Ensure the agent repo is up-to-date locally, then import its graph.

    On first call:  full ``git clone`` (~5–20 s depending on repo size).
    On subsequent calls:  ``git pull --ff-only`` (< 0.5 s typically).

    Args:
        agent_name: Bare agent name, e.g. ``"task-manager"``.
                    The repo resolved will be ``{github_org}/agent-{agent_name}``
                    unless *repo_name* overrides it.
        run_id:     Unique ID for this execution (auto-generated if ``None``).
                    Used only for the Python module name — the filesystem path
                    is *not* namespaced by run_id (it is shared across runs).
        repo_name:  Optional override for the GitHub repository name.
                    Use when the repo is not named ``agent-{agent_name}``
                    (e.g. ``repo_name="sales-prospector"`` for a repo that
                    lives at ``FracktalWorks/sales-prospector``).
        local_path: Absolute path to a local agent directory.  When supplied,
                    no git clone is performed — the directory is used as-is.
                    Takes priority over *repo_name*.  Useful during development
                    when the agent lives at e.g.
                    ``C:/Users/dev/Github/sales-prospector``.

    Returns:
        A :class:`LoadedAgent` context manager.

    Raises:
        :class:`AgentLoadError` if clone/pull or import fails.
    """
    settings = get_settings()
    run_id = run_id or str(uuid.uuid4())

    org: str = getattr(settings, "github_org", "FracktalWorks")
    token: str | None = _get_auth_token(settings)
    cache_root = Path(getattr(settings, "agents_clone_dir", "/tmp/acb_agents")) / "repos"

    # ── Local-path with local git tracking ──────────────────────────────────
    # When a local_path is registered (e.g. during local development) we copy
    # the source into the persistent cache and auto-initialise a local-only git
    # repo.  This gives pure MAF agents the same version-control benefits as
    # GitHub-sourced agents: commit tracking, mutation sandbox compatibility,
    # and rollback via git reset.
    if local_path:
        source_dir = Path(local_path)
        if not source_dir.is_dir():
            raise AgentLoadError(
                f"local_path {local_path!r} does not exist or is not a directory."
            )

        # Use the cache directory as the working copy — isolates the running
        # agent from the development source and gives the mutation sandbox a
        # clean, git-tracked directory to mount.
        agent_dir = cache_root / agent_name
        _ensure_local_git_repo(source_dir, agent_dir, settings)
        _install_agent_deps(agent_dir, settings)

        config_path = agent_dir / "config.json"
        config: dict[str, Any] = {}
        skill_repos: list[str] = []
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
                skill_repos = config.get("skill_repos", [])
            except Exception as exc:  # noqa: BLE001
                _log.warning("loader.config_parse_error", agent=agent_name, error=str(exc))

        injected_paths: list[str] = []
        for skill_repo_name in skill_repos:
            try:
                skill_dir = _ensure_repo(
                    skill_repo_name,
                    org=org,
                    token=token,
                    cache_root=cache_root,
                    settings=settings,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "loader.skill_clone_failed",
                    skill=skill_repo_name,
                    agent=agent_name,
                    error=str(exc),
                )
                continue
            skill_src = skill_dir / "src"
            skill_path = str(skill_src if skill_src.exists() else skill_dir)
            sys.path.insert(0, skill_path)
            injected_paths.append(skill_path)

        agent_path = str(agent_dir)
        sys.path.insert(0, agent_path)
        injected_paths.append(agent_path)

        module_name = f"_agent_{run_id.replace('-', '_')}_graph"
        if (agent_dir / "agents.py").exists():
            graph_module: Any = None
        else:
            graph_module = _import_graph_module(agent_dir, module_name=module_name)
        _log.info("loader.agent_ready_local", agent=agent_name, run_id=run_id, path=str(agent_dir))
        return LoadedAgent(
            agent_name=agent_name,
            run_id=run_id,
            agent_dir=agent_dir,
            graph_module=graph_module,
            injected_paths=injected_paths,
            module_name=module_name,
            config=config,
        )

    # ── GitHub clone path ───────────────────────────────────────────────────

    # Resolve the GitHub repo name.
    # Priority: explicit kwarg → default "agent-{name}"
    # When repo_name contains "/" it is an "org/repo" slug — extract the org
    # so agents from external GitHub orgs (e.g. vjvarada/agent-startup-guru)
    # are cloned from the correct org instead of the default github_org.
    agent_repo = repo_name or f"agent-{agent_name}"
    if repo_name and "/" in repo_name:
        org, agent_repo = repo_name.split("/", 1)
    agent_dir = _ensure_repo(
        agent_repo,
        org=org,
        token=token,
        cache_root=cache_root,
        settings=settings,
        clone_as=agent_name,  # always cache under the logical agent name
    )

    # Read config.json → discover skill repos, declared integrations, and optional
    # repo_name override (allows repos not named "agent-{name}" to be loaded).
    config_path = agent_dir / "config.json"
    config: dict[str, Any] = {}
    skill_repos: list[str] = []
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
            skill_repos = config.get("skill_repos", [])
        except Exception as exc:  # noqa: BLE001
            _log.warning("loader.config_parse_error", agent=agent_name, error=str(exc))

    injected_paths: list[str] = []

    # Ensure each skill repo is present and up-to-date
    for skill_repo_name in skill_repos:
        try:
            skill_dir = _ensure_repo(
                skill_repo_name,
                org=org,
                token=token,
                cache_root=cache_root,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "loader.skill_clone_failed",
                skill=skill_repo_name,
                agent=agent_name,
                error=str(exc),
            )
            continue
        skill_src = skill_dir / "src"
        skill_path = str(skill_src if skill_src.exists() else skill_dir)
        sys.path.insert(0, skill_path)
        injected_paths.append(skill_path)

    # Inject agent repo path
    agent_path = str(agent_dir)
    sys.path.insert(0, agent_path)
    injected_paths.append(agent_path)

    # Import graph.py with a unique module name per run_id
    # (concurrent runs of the same agent each get their own Python module object)
    # MAF agents use agents.py (not graph.py) — skip the graph import if agents.py exists.
    # The repo may contain both files (old graph.py + new agents.py during migration);
    # agents.py always wins when present.
    module_name = f"_agent_{run_id.replace('-', '_')}_graph"
    agents_file = agent_dir / "agents.py"
    if agents_file.exists():
        # MAF agent repo — graph_module is unused; build_agents() handles import.
        graph_module: Any = None
    else:
        graph_module = _import_graph_module(agent_dir, module_name=module_name)

    _log.info(
        "loader.agent_ready",
        agent=agent_name,
        run_id=run_id,
        skills=skill_repos,
        agent_dir=str(agent_dir),
    )

    return LoadedAgent(
        agent_name=agent_name,
        run_id=run_id,
        agent_dir=agent_dir,
        graph_module=graph_module,
        injected_paths=injected_paths,
        module_name=module_name,
        config=config,
    )


# ---------------------------------------------------------------------------
# Auth token resolution — swap body of _get_auth_token() to enable GitHub App
# ---------------------------------------------------------------------------

def _get_auth_token(settings: Any) -> str | None:
    """Resolve the best available auth token for GitHub operations.

    Current strategy: PAT from ``GITHUB_TOKEN`` env var.

    Upgrade path — GitHub App (short-lived, scoped tokens):
      1. Set ``GITHUB_APP_ID``, ``GITHUB_APP_PRIVATE_KEY_PATH``,
         ``GITHUB_INSTALLATION_ID`` in .env.
      2. Replace the body of this function with::

             return _get_github_app_token(settings)

      where ``_get_github_app_token`` generates a JWT, exchanges it for an
      installation access token via the GitHub Apps API, and caches the token
      until it expires (tokens are valid for 1 hour).
    """
    return getattr(settings, "github_token", None) or None
