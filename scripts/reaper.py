"""The reaper: stop orphan dev stacks and remove finished worktrees (CIV-14j).

Two jobs, one pass:

1. STACKS. A `metorite-dev-*` container group stops when its worktree is gone,
   or when no process uses the worktree and its last activity is older than
   --stack-hours (default 4).
2. WORKTREES. A box worktree goes when its pull request is merged, its HEAD
   equals the merged head, it has no change, and no process uses it. The
   removal is `scripts/worktree.sh remove <slug>`, never `git worktree remove`.

    python scripts/reaper.py --dry-run     # list what it would do
    python scripts/reaper.py               # do it, and log each act

It never touches a worktree with a change, an unmerged branch, or a live
process. Fence: tests/unit/test_reaper.py (fake docker, gh, clock).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

STACK_PREFIX = "metorite-dev-"
DEFAULT_STACK_HOURS = 4.0
WT_GLOB = "wt-*"


@dataclass
class Worktree:
    path: Path
    branch: str
    head: str
    dirty: bool
    stack_name: str | None
    last_activity: float


@dataclass
class Env:
    """Everything that touches the machine. The test swaps it for fakes."""

    now: float = field(default_factory=time.time)
    containers: list[str] = field(default_factory=list)
    worktrees: list[Worktree] = field(default_factory=list)
    busy_paths: set[str] = field(default_factory=set)  # a process runs here
    merged_heads: dict[str, set[str]] = field(default_factory=dict)  # branch -> merged head SHAs


def stack_of(container: str) -> str:
    """`metorite-dev-foo-pg` and `metorite-dev-foo-redis` both belong to `metorite-dev-foo`."""
    for suffix in ("-pg", "-redis", "-app", "-web", "-gateway"):
        if container.endswith(suffix):
            return container[: -len(suffix)]
    return container


def _busy(env: Env, wt: Worktree) -> bool:
    p = str(wt.path)
    return any(b == p or b.startswith(p + "/") for b in env.busy_paths)


def plan(env: Env, stack_hours: float = DEFAULT_STACK_HOURS) -> list[dict]:
    """Return the acts: {"act": "stop-stack"|"remove-worktree", ...}. Pure."""
    acts: list[dict] = []
    by_stack = {w.stack_name: w for w in env.worktrees if w.stack_name}
    stacks = sorted({stack_of(c) for c in env.containers if c.startswith(STACK_PREFIX)})
    for stack in stacks:
        wt = by_stack.get(stack)
        if wt is None:
            acts.append({"act": "stop-stack", "stack": stack, "why": "its worktree is gone"})
        elif not _busy(env, wt) and env.now - wt.last_activity > stack_hours * 3600:
            hours = (env.now - wt.last_activity) / 3600
            acts.append({"act": "stop-stack", "stack": stack, "why": f"idle {hours:.1f} h, no process", "path": str(wt.path)})
    for wt in env.worktrees:
        if wt.dirty or _busy(env, wt):
            continue
        if wt.head and wt.head in env.merged_heads.get(wt.branch, set()):
            acts.append({"act": "remove-worktree", "slug": wt.path.name.removeprefix("wt-"), "path": str(wt.path), "why": f"PR merged at {wt.head[:9]}"})
    return acts


def _run(*cmd: str, cwd: str | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    return r.stdout if r.returncode == 0 else ""


def _state_name(path: Path) -> str | None:
    state = path / ".dev_up" / "state"
    if not state.is_file():
        return None
    for line in state.read_text(encoding="utf-8").splitlines():
        if line.startswith("NAME="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def _last_activity(path: Path) -> float:
    marks = [path / ".dev_up" / "state", path / ".git"]
    return max((m.stat().st_mtime for m in marks if m.exists()), default=0.0)


def _busy_paths() -> set[str]:
    out = set()
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                out.add(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                pass
    return out


def collect(home: Path, repo: Path) -> Env:
    env = Env(busy_paths=_busy_paths())
    env.containers = [n for n in _run("docker", "ps", "--format", "{{.Names}}").split() if n.startswith(STACK_PREFIX)]
    for p in sorted(home.glob(WT_GLOB)):
        if not (p / ".git").exists():
            continue
        branch = _run("git", "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD").strip()
        head = _run("git", "-C", str(p), "rev-parse", "HEAD").strip()
        dirty = bool(_run("git", "-C", str(p), "status", "--porcelain").strip())
        env.worktrees.append(Worktree(p, branch, head, dirty, _state_name(p), _last_activity(p)))
    for wt in env.worktrees:
        if wt.branch in ("HEAD", "main") or wt.branch in env.merged_heads:
            continue
        out = _run("gh", "pr", "list", "--head", wt.branch, "--state", "merged", "--json", "headRefOid", "--limit", "5", cwd=str(repo))
        env.merged_heads[wt.branch] = {x["headRefOid"] for x in json.loads(out or "[]")}
    return env


def apply(acts: list[dict], repo: Path, log=print) -> None:
    for a in acts:
        if a["act"] == "stop-stack":
            names = _run("docker", "ps", "-a", "--format", "{{.Names}}").split()
            mine = [n for n in names if stack_of(n) == a["stack"]]
            log(f"reaper: stop {a['stack']} ({a['why']}): {' '.join(mine)}")
            if mine:
                subprocess.run(["docker", "stop", *mine], capture_output=True, timeout=120)
        else:
            log(f"reaper: remove worktree {a['path']} ({a['why']})")
            r = subprocess.run(["bash", str(repo / "scripts" / "worktree.sh"), "remove", a["slug"]], capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                log(f"reaper: worktree.sh refused {a['slug']}: {r.stdout.strip()[-200:]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stack-hours", type=float, default=DEFAULT_STACK_HOURS)
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--repo", default=str(Path.home() / "Metorite"))
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    acts = plan(collect(Path(args.home), repo), args.stack_hours)
    if args.dry_run:
        for a in acts:
            print(f"would {a['act']}: {a.get('stack') or a.get('path')} ({a['why']})")
        print(f"{len(acts)} act(s), dry run")
        return 0
    apply(acts, repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
