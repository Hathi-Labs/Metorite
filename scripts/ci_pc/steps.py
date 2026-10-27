"""Render one pr-check job as a bash script, for the local CI on this PC.

pr-check.yml stays the ONE description of CI. This script reads a job from it
and prints its `run:` steps as bash, so `run_suite.sh` runs the same commands
that GitHub runs, with no second copy to drift.

    python3 scripts/ci_pc/steps.py lint
    python3 scripts/ci_pc/steps.py test --ctx matrix.shard=3 --set TENANT_LADDER_DATABASE_URL=...

* `uses:` steps are dropped: checkout, setup-uv, setup-node, artifacts.
* Steps that INSTALL are dropped (SKIP_STEPS): the distro is preset by
  scripts/ci_pc/provision.sh and bootstrap.sh, which is the whole point.
* `${{ ... }}` is replaced from --ctx (and a few defaults). An expression it
  does not know is an ERROR, never an empty string: a silent blank is how a
  check stops checking.
* --set overrides an env value from the job or the step, for example a
  service port. On GitHub each job has its own network. Here, all jobs share
  one, so each service gets its own port.

Standard library plus PyYAML (in the project's dev dependencies).
Fence: tests/unit/test_ci_pc.py.
"""
from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pr-check.yml"

#: Steps the preset distro has already done. Matched by step name prefix.
SKIP_STEPS = (
    "Install uv",
    "Install Python",
    "Sync deps",
    "Install deps",
    "Install the postgresql-16 client",
    "Install Chromium",
    "Install gitleaks",
    "Cache the browser",
    "Keep ",  # artifact uploads
)

_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def load_jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _sub(text: str, ctx: dict[str, str], where: str) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in ctx:
            raise SystemExit(f"steps.py: unknown expression ${{{{ {key} }}}} in {where}. Pass --ctx {key}=...")
        return ctx[key]

    return _EXPR.sub(repl, str(text))


def render(
    job_name: str,
    ctx: dict[str, str],
    overrides: dict[str, str],
    replace: dict[str, str] | None = None,
) -> str:
    """``replace`` swaps literal text in the `run:` bodies, for example
    ``--splits 6`` for ``--splits 12`` on a PC with more cores. Text that no
    step contains is an ERROR: a replacement that quietly matched nothing
    would run 12 groups of a 6-way split."""
    replace = replace or {}
    unused = set(replace)
    jobs = load_jobs()
    if job_name not in jobs:
        raise SystemExit(f"steps.py: no job {job_name!r} in pr-check.yml")
    job = jobs[job_name]
    workdir = (job.get("defaults", {}).get("run", {}) or {}).get("working-directory", ".")
    out = ["#!/usr/bin/env bash", f"# pr-check job `{job_name}`, rendered by scripts/ci_pc/steps.py", "set -u"]
    job_env = {k: _sub(v, ctx, f"{job_name}.env") for k, v in (job.get("env") or {}).items()}
    for step in job.get("steps", []):
        name = step.get("name", "")
        if "uses" in step or name.startswith(SKIP_STEPS) or "run" not in step:
            continue
        env = {**job_env, **{k: _sub(v, ctx, f"{job_name}/{name}.env") for k, v in (step.get("env") or {}).items()}}
        env.update(overrides)
        body = _sub(step["run"], ctx, f"{job_name}/{name}")
        for old, new in replace.items():
            if old in body:
                body = body.replace(old, new)
                unused.discard(old)
        cwd = step.get("working-directory", workdir)
        exports = "".join(f"export {k}={shlex.quote(str(v))}; " for k, v in env.items())
        out.append(f"echo {shlex.quote('== ' + name)}")
        # The shell GitHub uses for `run:` is `bash --noprofile --norc -eo pipefail`.
        out.append(
            f"( cd {shlex.quote(cwd)} || exit 1; {exports}"
            f"bash --noprofile --norc -eo pipefail -c {shlex.quote(body)} ) || exit 1"
        )
    if unused:
        raise SystemExit(f"steps.py: --replace text not found in job {job_name!r}: {sorted(unused)}")
    out.append('echo "== job ok"')
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--ctx", action="append", default=[], help="expression=value")
    ap.add_argument("--set", action="append", default=[], help="ENV=value override")
    ap.add_argument("--replace", action="append", default=[], help="OLD=>NEW literal text in run bodies")
    a = ap.parse_args(argv)
    replace = dict(r.split("=>", 1) for r in a.replace)
    ctx = {
        "github.workspace": str(ROOT),
        "github.event_name": "pull_request",
        "github.token": "",
        "github.event.pull_request.number": "",
        "runner.os": "Linux",
    }
    ctx.update(dict(kv.split("=", 1) for kv in a.ctx))
    overrides = dict(kv.split("=", 1) for kv in a.set)
    sys.stdout.write(render(a.job, ctx, overrides, replace))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
