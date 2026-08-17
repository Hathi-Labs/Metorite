"""Native-MAF mutation → Metorite monorepo PR (Part 1).

⚠️  DEV-ONLY MECHANISM — MUST BE REPLACED BEFORE PRODUCTION / MULTI-TENANCY.
    Landing an agent's self-mutation as a PR against the SHARED Metorite
    monorepo is acceptable only while every agent is first-party and Command
    Center is a work in progress. In a multi-tenant deployment, third-party /
    customer agents must NEVER push to the shared monorepo — this must be swapped
    for a tenant-isolated mechanism (per-tenant repo, or a tenant-scoped store the
    loader reads at runtime). Tracking doc:
    ``docs/DESIGN_LIMITATION_native_maf_mutation.md``.

A native MAF agent (runtime "maf", registered by ``local_path`` only) runs from
an isolated, local-only clone under ``{agents_clone_dir}/repos/{agent}`` with NO
git remote. Approving its self-mutation therefore used to be a no-op — the commit
lived only in that throwaway clone and was clobbered on the next deploy re-seed.

This module makes approval durable: it takes the agent's committed change and
opens a **pull request against the Metorite monorepo** that edits the
agent's source at ``apps/agents/agent-<name>/`` in place. Once merged, the fix is
real source that ships on the next deploy — closing the clobber gap and, per the
product intent, folding the agent's self-improvement back into Metorite.

Flow (``open_monorepo_pr``):
  1. Resolve the agent's monorepo ``local_path`` from the registry.
  2. Export the mutated file tree from the agent's local clone (its HEAD).
  3. In a throwaway checkout of the monorepo, create a branch, copy the mutated
     files onto ``local_path/``, commit, and push the branch.
  4. Open a PR (base = mutation_monorepo_base) via the GitHub REST API.

Requires ``mutation_monorepo_repo`` (owner/name) + a token with push + PR scope
(``mutation_pr_token``, falling back to ``github_token``). If either is missing,
the caller falls back to the previous keep-local behaviour.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from acb_common import get_logger, get_settings

_log = get_logger("gateway.monorepo_pr")


class MonorepoPRError(Exception):
    """Raised when the monorepo PR cannot be opened (config or git/API failure)."""


@dataclass
class MonorepoPRResult:
    pr_url: str
    branch: str
    target_path: str


def _pr_token() -> str:
    """The token used to push + open the monorepo PR.

    Dedicated ``mutation_pr_token`` when set (explicit push/PR credential),
    else falls back to ``github_token``. Empty string disables the flow.
    """
    settings = get_settings()
    return (
        getattr(settings, "mutation_pr_token", "")
        or getattr(settings, "github_token", "")
        or ""
    )


def monorepo_pr_configured() -> bool:
    """True when the monorepo-PR path is usable (repo slug + a token present)."""
    settings = get_settings()
    return bool(getattr(settings, "mutation_monorepo_repo", "") and _pr_token())


def resolve_agent_local_path(agent_name: str) -> str | None:
    """Return the agent's monorepo source path (e.g. apps/agents/agent-x), or None.

    Looks the agent up in the merged registry (static + dynamic). A native MAF
    agent is registered with a ``local_path``; agents without one (GitHub-sourced)
    are not eligible for the monorepo-PR path.
    """
    try:
        from gateway.routes.agent import (  # noqa: PLC0415
            _AGENT_REGISTRY,
            _load_dynamic_agents,
        )

        want = agent_name.strip()
        want_bare = want[len("agent-"):] if want.startswith("agent-") else want
        for a in _load_dynamic_agents() + _AGENT_REGISTRY:
            name = (a.get("name") or "").strip()
            if name in (want, want_bare) and a.get("local_path"):
                return str(a["local_path"]).strip()
    except Exception as exc:  # noqa: BLE001
        _log.warning("monorepo_pr.local_path_lookup_failed", agent=agent_name, error=str(exc))
    return None


def _find_monorepo_root() -> Path | None:
    """Locate the Metorite monorepo checkout on disk (the gateway runs in it).

    Walk up from this file until a directory containing both ``apps/`` and a
    ``.git`` (or the ``apps/agents`` tree) is found.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "apps" / "agents").is_dir() and (parent / ".git").exists():
            return parent
    # Fallback: the first ancestor that has apps/agents even without .git
    for parent in here.parents:
        if (parent / "apps" / "agents").is_dir():
            return parent
    return None


async def _run(args: list[str], *, cwd: str | Path, timeout: int = 60) -> tuple[int, str, str]:
    """Run a subprocess, returning (returncode, stdout, stderr). Never raises."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode or 0,
        out_b.decode(errors="replace"),
        err_b.decode(errors="replace"),
    )


async def _export_agent_tree(clone_dir: str, commit_sha: str, dest: Path) -> None:
    """Materialise the agent clone's tree at *commit_sha* into *dest*.

    Uses ``git archive`` so only tracked files at that commit are exported —
    generated outputs/ inputs/ agent-data/ are gitignored and excluded, so the
    PR contains only source changes, never the agent's runtime state.
    """
    dest.mkdir(parents=True, exist_ok=True)
    # git archive <sha> | tar -x -C dest
    rc, _out, err = await _run(
        ["git", "archive", "--format=tar", commit_sha, "-o", str(dest / "_tree.tar")],
        cwd=clone_dir,
    )
    if rc != 0:
        raise MonorepoPRError(f"git archive failed: {err.strip()[:200]}")
    # Extract with the stdlib to stay cross-platform (no tar dependency).
    import tarfile  # noqa: PLC0415

    tar_path = dest / "_tree.tar"
    with tarfile.open(tar_path) as tf:
        # Guard against path traversal in archive members.
        base = dest.resolve()
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(base)):
                raise MonorepoPRError("unsafe path in agent archive")
        tf.extractall(dest)  # noqa: S202 — members validated above
    tar_path.unlink(missing_ok=True)


async def _github_create_pr(
    *, repo: str, token: str, head: str, base: str, title: str, body: str
) -> str:
    """Open a PR via the GitHub REST API; return the PR html_url."""
    import httpx  # noqa: PLC0415

    owner_repo = repo.strip().removesuffix(".git")
    url = f"https://api.github.com/repos/{owner_repo}/pulls"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"title": title, "head": head, "base": base, "body": body}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201):
        raise MonorepoPRError(
            f"GitHub PR create failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json().get("html_url", "")


async def open_monorepo_pr(
    *,
    agent_name: str,
    clone_dir: str,
    commit_sha: str,
    commit_message: str,
) -> MonorepoPRResult:
    """Open a Metorite PR that lands the agent's mutation into its source.

    Raises MonorepoPRError on any failure (config, git, or GitHub API). The
    caller (approve endpoint) catches it and surfaces the message to the operator.
    """
    settings = get_settings()
    repo = getattr(settings, "mutation_monorepo_repo", "").strip()
    base = getattr(settings, "mutation_monorepo_base", "main").strip() or "main"
    token = _pr_token()
    if not repo or not token:
        raise MonorepoPRError(
            "monorepo PR not configured (set mutation_monorepo_repo + a PR token)"
        )

    target_path = resolve_agent_local_path(agent_name)
    if not target_path:
        raise MonorepoPRError(
            f"agent {agent_name!r} has no monorepo local_path — not eligible for a PR"
        )

    monorepo_root = _find_monorepo_root()
    if monorepo_root is None:
        raise MonorepoPRError("could not locate the Metorite monorepo checkout")

    # Deterministic branch name from the commit so a retry reuses it rather than
    # spawning duplicates. (No timestamp — Date.now is fine here but the SHA is
    # already unique per mutation.)
    branch = f"agent-mutation/{agent_name}/{commit_sha[:12]}"
    from acb_common import get_settings as _gs  # noqa: PLC0415

    bot_name = getattr(_gs(), "github_bot_name", "Metorite")
    bot_email = (
        getattr(_gs(), "github_bot_email", "")
        or f"{bot_name}@users.noreply.github.com"
    )

    # Work in a throwaway sibling checkout so we never touch the running gateway's
    # own working tree (which the deploy hard-resets anyway).
    import tempfile  # noqa: PLC0415

    workdir = Path(tempfile.mkdtemp(prefix=f"ccpr-{agent_name}-"))
    auth_remote = (
        f"https://x-token:{token}@github.com/{repo.removesuffix('.git')}.git"
    )
    try:
        # Shallow-clone the monorepo base branch (fast; we only need one path).
        rc, _o, err = await _run(
            ["git", "clone", "--depth", "1", "--branch", base, auth_remote, "repo"],
            cwd=workdir, timeout=180,
        )
        if rc != 0:
            raise MonorepoPRError(f"monorepo clone failed: {err.strip()[:200]}")
        repo_dir = workdir / "repo"
        await _run(["git", "config", "user.name", bot_name], cwd=repo_dir)
        await _run(["git", "config", "user.email", bot_email], cwd=repo_dir)
        await _run(["git", "checkout", "-b", branch], cwd=repo_dir)

        # Export the agent's mutated tree and copy it onto the monorepo source.
        export_dir = workdir / "export"
        await _export_agent_tree(clone_dir, commit_sha, export_dir)
        dest = repo_dir / target_path
        # Replace the agent source dir with the mutated tree (add/modify/delete).
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(export_dir, dest)

        await _run(["git", "add", "-A", target_path], cwd=repo_dir)
        # Nothing changed? Then the mutation didn't alter tracked source.
        rc, _o, _e = await _run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo_dir
        )
        if rc == 0:
            raise MonorepoPRError(
                "no source changes to submit (mutation touched only runtime files)"
            )

        msg = commit_message.strip() or f"fix({agent_name}): agent self-mutation"
        commit_body = (
            f"{msg}\n\nAuto-generated from an approved self-mutation of the "
            f"'{agent_name}' agent (source clone {commit_sha[:12]}).\n"
        )
        rc, _o, err = await _run(
            ["git", "commit", "-m", commit_body], cwd=repo_dir
        )
        if rc != 0:
            raise MonorepoPRError(f"commit failed: {err.strip()[:200]}")

        rc, _o, err = await _run(
            ["git", "push", "--force-with-lease", "origin", branch],
            cwd=repo_dir, timeout=120,
        )
        if rc != 0:
            raise MonorepoPRError(f"push failed: {err.strip()[:200]}")

        pr_body = (
            f"### Agent self-mutation → `{target_path}`\n\n"
            f"The **{agent_name}** agent proposed this fix to its own code while "
            f"running. It was reviewed and approved in the Metorite "
            f"approvals inbox, and is submitted here as a PR so it becomes durable "
            f"source that ships on the next deploy.\n\n"
            f"- Agent: `{agent_name}`\n"
            f"- Source clone commit: `{commit_sha[:12]}`\n\n"
            f"Merging this lands the change in `{target_path}/`."
        )
        pr_url = await _github_create_pr(
            repo=repo,
            token=token,
            head=branch,
            base=base,
            title=msg.splitlines()[0][:120],
            body=pr_body,
        )
        _log.info(
            "monorepo_pr.opened",
            agent=agent_name,
            branch=branch,
            target=target_path,
            pr_url=pr_url,
        )
        return MonorepoPRResult(pr_url=pr_url, branch=branch, target_path=target_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
