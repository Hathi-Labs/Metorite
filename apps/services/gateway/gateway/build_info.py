"""What code is this process actually running?

CLAUDE.md §3 point 8 is a standing rule and names the evidence it wants:

    Verify delivery by evidence, never by a green job. Migration ledger lines,
    **the deployed SHA**, the log line. Four deploys once reported success while
    shipping nothing.

Until this module existed the deployed SHA was **not obtainable**. `/health`
answered `{"status": "ok", "env": "dev"}` and no route anywhere reported a
build identity, so "is production on the latest code?" could only be answered
by looking at the UI and recognising a bug — which is exactly how it was
answered on 2026-08-25, from a screenshot of the wrong icons.

── Why it reads `.git` rather than an injected build variable ───────────────

The obvious design is a `ACB_GIT_SHA` stamped into the environment by the
deploy. It is rejected here for one practical reason: writing it would mean
editing `deploy/hostinger/deploy.sh` and `.github/workflows/deploy.yml`, both of
which are owner-gated (`work_plan.md` §6, and `plan-guard.mjs` blocks agent
writes under `deploy/`). An endpoint that only starts telling the truth after
an owner-gated change is an endpoint that does not exist yet.

Reading `.git` needs no deploy change at all, because of how delivery already
works: `scripts/vps_pull.sh` keeps `/opt/acb/app` as a git checkout and moves it
with `git reset --hard`, so the worktree the services run from IS a repository
and its HEAD is the answer. The env override is still honoured first, so a
future container build — which would have no `.git` — can stamp the value
without this module changing.

⚠️ **`build_sha()` speaks for the GATEWAY only** (corrected for H-142). This
used to say one SHA covers the gateway AND the workbench because one checkout
builds both. The order breaks that: the apply restarts the gateway, THEN builds
the workbench, and a failed build keeps the old web app serving. So the web app
has its own answer, `applied_marker()`: the deploy marker that `vps_apply.sh`
writes only when the whole apply finished.

Nothing here touches the database. A version endpoint that needs a query fails
in precisely the situation where you most want to know what is running.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _repo_root() -> Path | None:
    """The nearest ancestor holding a `.git`, or None.

    Walks up rather than assuming a fixed depth: the file sits at
    `apps/services/gateway/gateway/build_info.py` in the repo but is installed
    differently under `uv sync`, and hard-coding `parents[4]` would answer
    confidently and wrongly in one of the two layouts.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".git").exists():
            return candidate
    return None


def _sha_from_git_dir(git_dir: Path) -> str | None:
    """Resolve HEAD to a commit SHA by reading files, never by running git.

    No subprocess, deliberately. This is called from a request handler, and
    shelling out there turns a metadata read into a process spawn that can
    hang on a busy or half-mounted box — on the exact endpoint you reach for
    when something is already wrong.

    Handles the three shapes a checkout's HEAD actually takes:
      * `ref: refs/heads/main` with a loose ref file — the ordinary case, and
        what `git reset --hard` leaves behind (it moves the branch, it does not
        detach);
      * the same ref present only in `packed-refs`, which is what a fresh
        `git clone` produces before anything writes a loose ref;
      * a bare 40-character SHA — a detached HEAD.
    """
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        # Detached HEAD: the file already holds the SHA.
        return head or None

    ref = head[4:].strip()
    loose = git_dir / ref
    try:
        return loose.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass

    # Packed. Lines are "<sha> <refname>"; annotated-tag peel lines start "^"
    # and must not be matched as refs.
    try:
        for line in (git_dir / "packed-refs").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith(("#", "^")):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip() == ref:
                return parts[0]
    except OSError:
        pass
    return None


@lru_cache(maxsize=1)
def build_sha() -> str | None:
    """The commit this process is running, or None if it cannot be determined.

    Cached for the process lifetime, which is the honest scope: the code does
    not change under a running service. `vps_pull.sh` restarts the units after
    it moves the checkout, so a stale cached value cannot outlive a deploy — and
    if it somehow did, a version endpoint reporting the code the PROCESS was
    started from is the more useful answer anyway.

    ⚠️ Returns None rather than a placeholder like "unknown". A caller must be
    able to tell "this box cannot report its version" from "this box is running
    a commit named unknown", and a string does not admit that distinction.
    """
    override = os.environ.get("ACB_GIT_SHA", "").strip()
    if override:
        return override
    root = _repo_root()
    if root is None:
        return None
    git_path = root / ".git"
    # A worktree or submodule checkout has `.git` as a FILE containing
    # "gitdir: <path>", not a directory. The production box is a plain clone,
    # but every developer running this from a `git worktree` — which this repo
    # uses heavily — would otherwise get None locally and think the endpoint
    # was broken.
    if git_path.is_file():
        try:
            pointer = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not pointer.startswith("gitdir:"):
            return None
        git_dir = Path(pointer[7:].strip())
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()
    else:
        git_dir = git_path
    return _sha_from_git_dir(git_dir)


_SHA_CHARS = frozenset("0123456789abcdef")


def _marker_path() -> Path | None:
    """Where `vps_apply.sh` writes its deploy marker, or None.

    `vps_apply.sh` and `vps_pull.sh` both default it to
    `$(dirname "$APP_DIR")/acb-deploy.applied`, beside the checkout, so this is
    the parent of the repo root. `ACB_DEPLOY_MARKER` overrides it, the same way
    `DEPLOY_MARKER` overrides it in both scripts.
    """
    override = os.environ.get("ACB_DEPLOY_MARKER", "").strip()
    if override:
        return Path(override)
    root = _repo_root()
    if root is None:
        return None
    return root.parent / "acb-deploy.applied"


def applied_marker() -> tuple[str | None, str | None]:
    """The last COMPLETE apply on this box: ``(sha, finished_at)``, or Nones.

    H-142. `vps_apply.sh` writes ``<sha> <UTC time>`` to its marker ONLY at its
    last line, after the workbench build and its restart. So this answers a
    question that `build_sha()` cannot: did the WEB app get built too? The
    gateway restarts before the workbench builds, and a failed build keeps the
    old web app serving. In that state `build_sha()` reports the new commit and
    this still reports the last apply that finished.

    `deploy.yml` reads it from outside the box when its runner could not reach
    the box over ssh, to decide whether the pull timer delivered the commit.

    ⚠️ NOT cached, unlike `build_sha()`. The marker moves at the END of an
    apply, long after the gateway restarted, so a cached value would lag by a
    whole deploy. It is one small file read per request.

    Nones when the file is absent, unreadable or malformed. The sha must be
    40 lowercase hex characters, so a corrupt file cannot pass as a commit.
    """
    path = _marker_path()
    if path is None:
        return None, None
    try:
        parts = path.read_text(encoding="utf-8").split()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not parts:
        return None, None
    sha = parts[0]
    if len(sha) != 40 or not set(sha) <= _SHA_CHARS:
        return None, None
    return sha, (parts[1] if len(parts) > 1 else None)
