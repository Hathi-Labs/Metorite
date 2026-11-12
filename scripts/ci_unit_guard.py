"""Check the sharded unit run as one run, and refresh the split timings.

pr-check.yml runs ``tests/unit/`` in parallel shards (pytest-split). Each
shard writes a JUnit report. The ``Unit tests`` job reads all of them here.

``check`` fails when:

* a test skipped because ``CUSTOMER_CONSOLE_DATABASE_URL`` or
  ``TENANT_LADDER_DATABASE_URL`` was unset (the R8 gate is disarmed),
* a ``--must-run`` file has no test in any report (no shard ran it), or
* a ``--never-skip`` file skipped a test (a database gate leaked into a fence
  that needs none).

This replaces a step that ran about 40 R8 files a second time only to grep
their skip reasons. The reports already hold those reasons.

``durations`` merges the reports into the ``.test_durations`` format, so the
timings file can be refreshed from a real CI run without a 50-minute local run.

``--only-selected`` (CIV-3) limits ``--must-run`` to the files that the PR tier
selected. ``gate-output``, ``select`` and ``empty-report`` carry that selection
between the ``changes`` job, the shards and this guard. A selection run is a
subset, so the shards and this guard never treat an absent file as lost.

``r8-covered`` (CIV-4c) reads the report of deploy.yml's no-database unit run
and the ``affected_tests.py --r8`` list. It fails when a file skipped for an
R8 reason and is not in the list, because the release gate never runs it.

``repair`` (CIV-32, D218) narrows a repair round. On a ``synchronize`` push it
finds the newest finished pr-check run of the same PR, reads its shard
reports, and selects the files that failed there. It adds the files that the
fix reaches, and the files that main's move since that run reaches. Any doubt
gives the normal PR selection, unchanged. ``gate-meta`` writes the base and
the head of the merge that a run tests, so the next run can find main's move.

Stdlib only: the ``Unit tests`` job runs it without ``uv sync``.
Fence: tests/unit/test_ci_unit_guard.py.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The reason must START with the variable. A test elsewhere skips with
# "AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset" when CI has no
# Redis, and that skip is expected. The workflow passes these with --disarmed.
# A job output has a 1 MB limit. A selection longer than this runs the full
# suite instead, and a path outside this shape does the same.
MAX_SELECTION_CHARS = 200_000
_SAFE_PATH = re.compile(r"^tests/[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")

DISARMED = ("CUSTOMER_CONSOLE_DATABASE_URL unset", "TENANT_LADDER_DATABASE_URL unset")


def _cases(junit_dir: Path) -> list[ET.Element]:
    reports = sorted(junit_dir.rglob("*.xml"))
    if not reports:
        raise SystemExit(f"no JUnit reports under {junit_dir}")
    return [case for r in reports for case in ET.parse(r).getroot().iter("testcase")]


def _file(case: ET.Element) -> str:
    # xunit1 carries `file`. Fall back to the dotted classname for xunit2.
    if case.get("file"):
        return case.get("file", "").replace("\\", "/")
    parts = case.get("classname", "").split(".")
    while parts and not parts[-1].startswith("test"):
        parts.pop()
    return "/".join(parts) + ".py"


def _skip_reason(case: ET.Element) -> str | None:
    """The skip reason, or None when the case did not skip.

    A module that skips at collection (`allow_module_level=True`) is written
    with message "collection skipped" and the real reason in the element text.
    """
    skipped = case.find("skipped")
    if skipped is None:
        return None
    message = skipped.get("message", "")
    if message == "collection skipped":
        return " ".join((skipped.text or "").split()) or message
    return message


def _collection_skipped(case: ET.Element) -> bool:
    skipped = case.find("skipped")
    return skipped is not None and skipped.get("message") == "collection skipped"


def check(
    junit_dir: Path,
    must_run: list[str],
    never_skip: list[str],
    disarmed: tuple[str, ...] | list[str] = DISARMED,
    only_selected: list[str] | None = None,
    tree_root: Path | None = None,
) -> list[str]:
    """``only_selected`` None is a full run. A list (even empty) is a selection
    run, and then only the must-run files in that list have to run.

    ``tree_root`` set: EVERY must-run path has to exist there, selection or
    not. A PR that deletes an R8 suite but keeps its line would otherwise pass
    here and turn the next full run red."""
    missing = [
        f"a --must-run file is not in the tree: {p}. Delete its line in pr-check.yml too."
        for p in must_run
        if tree_root is not None and not (tree_root / p).is_file()
    ]
    if only_selected is not None:
        chosen = {p.replace("\\", "/") for p in only_selected}
        must_run = [p for p in must_run if p in chosen]
    cases = _cases(junit_dir)
    problems: list[str] = list(missing)
    ran: set[str] = set()
    for case in cases:
        path = _file(case)
        # A module skipped whole at collection ran nothing.
        if not _collection_skipped(case):
            ran.add(path)
        reason = _skip_reason(case)
        if reason is None:
            continue
        # The collection text carries a "Skipped:" prefix, so search it.
        if reason.startswith(tuple(disarmed)) or (
            _collection_skipped(case) and any(f"Skipped: {d}" in reason for d in disarmed)
        ):
            problems.append(f"R8 gate disarmed: {path}::{case.get('name')} skipped: {reason}")
        if path in never_skip:
            problems.append(f"a no-database fence skipped: {path}::{case.get('name')}: {reason}")
    for path in must_run:
        if path not in ran:
            problems.append(f"no shard ran {path}")
    print(f"checked {len(cases)} tests from {len(ran)} files")
    return problems


# ── r8-covered (CIV-4c) ──────────────────────────────────────────────────
# deploy.yml's `test` job has no database, so each R8 suite skips there. The
# release gate runs only the files that `affected_tests.py --r8` lists. A file
# that skips for an R8 reason and is not in that list runs nowhere.
R8_VARS = ("CUSTOMER_CONSOLE_DATABASE_URL", "TENANT_LADDER_DATABASE_URL")
_R8_WORD = re.compile(r"(?<![A-Za-z0-9_])R8(?![A-Za-z0-9_])")
_REAL_POSTGRES = re.compile(r"real postgres", re.IGNORECASE)


def is_r8_reason(reason: str) -> bool:
    """True when a skip reason says that an R8 database was not set.

    The reason starts with an R8 variable, or it names "R8" and also names a
    variable or "real Postgres". A Redis skip that names a variable later,
    with no "R8", is not an R8 reason. The collection text carries a path and
    a "Skipped:" prefix, which this drops first."""
    text = reason.split("Skipped: ", 1)[1] if "Skipped: " in reason else reason
    text = text.strip()
    if text.startswith(R8_VARS):
        return True
    if not _R8_WORD.search(text):
        return False
    return any(v in text for v in R8_VARS) or bool(_REAL_POSTGRES.search(text))


def _skip_paths(case: ET.Element) -> list[str]:
    """The files that own a skipped case: where the test is DEFINED (``file``)
    and the module that COLLECTED it (read from ``classname``). They differ when
    a module subclasses a gated class of another module. Both must be in the
    --r8 list, or the collecting module runs nowhere (CIV-4c verifier)."""
    paths = [_file(case)]
    parts = case.get("classname", "").split(".")
    while parts and not parts[-1].startswith("test"):
        parts.pop()
    if parts:
        collected = "/".join(parts) + ".py"
        if collected not in paths:
            paths.append(collected)
    return paths


def r8_covered(junit_dir: Path, r8_list: Path) -> list[str]:
    """Each file that skipped for an R8 reason and is not in ``r8_list``.

    Fails closed: no report, a report with no test, and a missing or empty
    list each give a problem. An empty list means the selector broke. A
    report with no test means no run. The check never passes on nothing."""
    try:
        listed = {
            line.strip().replace("\\", "/")
            for line in r8_list.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError as exc:
        return [f"cannot read the --r8 list {r8_list}: {exc}"]
    if not listed:
        return [f"the --r8 list {r8_list} is empty. affected_tests.py --r8 broke."]
    reports = sorted(junit_dir.rglob("*.xml")) if junit_dir.is_dir() else []
    if not reports:
        return [f"no JUnit report under {junit_dir}. The unit run wrote nothing."]
    try:
        cases = [c for r in reports for c in ET.parse(r).getroot().iter("testcase")]
    except ET.ParseError as exc:
        return [f"a JUnit report under {junit_dir} does not parse: {exc}"]
    if not cases:
        return [f"the JUnit reports under {junit_dir} hold no test. No unit run happened."]
    skipped: dict[str, str] = {}
    for case in cases:
        reason = _skip_reason(case)
        if reason is not None and is_r8_reason(reason):
            for path in _skip_paths(case):
                skipped.setdefault(path, f"{case.get('name')}: {reason}")
    missed = sorted(p for p in skipped if p not in listed)
    if not missed:
        print(f"R8 skips checked: {len(skipped)} files, all in the --r8 list")
    return [
        f"{p} skipped for an R8 reason and is not in the --r8 list, so the release "
        f"gate never runs it. Name an R8 mark in the file. {skipped[p]}"
        for p in missed
    ]


def durations(junit_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for case in _cases(junit_dir):
        path = _file(case)
        module = path[: -len(".py")].replace("/", ".")
        klass = case.get("classname", "")
        inner = klass[len(module) + 1 :] if klass.startswith(module + ".") else ""
        node = "::".join([path, *inner.split(".")] if inner else [path])
        out[f"{node}::{case.get('name')}"] = round(float(case.get("time", 0) or 0), 3)
    return dict(sorted(out.items()))


# ── repair (CIV-32, D218) ────────────────────────────────────────────────
# A repair round re-tests what failed and what the fix can reach. The decision
# is pure (``repair_decision``) so a test can drive it with fixture reports
# and fixture diffs. Every doubt returns mode ``full``, which means the normal
# PR selection, unchanged. The result never holds an empty list.
#
# A pull_request run tests the MERGE of the PR head and main. So the delta has
# two parts: the PR's own commits (old head to new head), and main's move
# (old base to new base). The ``changes`` job keeps both SHAs of the merge it
# tested in the ``gate-meta`` artifact. A run without that artifact gives full.
SHARD_JOB = "Unit tests (shard "
_SHARD_NAME = re.compile(r"^Unit tests \(shard (\d+)\)$")
_RUN_CONCLUSIONS = ("success", "failure")
GATE_META = "gate-meta.json"
GATE_META_ARTIFACT = "gate-meta"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _disarmed_skip(case: ET.Element) -> bool:
    """The same R8 test as ``check``. A shard failure stops the old guard before
    it reads skips, so a disarmed skip there was never judged."""
    reason = _skip_reason(case)
    if reason is None:
        return False
    return reason.startswith(DISARMED) or (
        _collection_skipped(case) and any(f"Skipped: {d}" in reason for d in DISARMED)
    )


def _collection_error(case: ET.Element) -> bool:
    """A collection error stops the whole shard before pytest-split cuts the
    selection, so no other test of that shard ran (Opus review, 2026-11-12:
    pytest 9.1 writes one case with an empty classname and
    `<error message="collection failure">`). Such a report is no baseline."""
    err = case.find("error")
    return err is not None and (
        "collection failure" in (err.get("message") or "")
        or not (case.get("classname") or "").strip()
    )


def _shard_verdicts(jobs: list[dict]) -> tuple[dict[int, str] | None, str]:
    """``{N: conclusion}`` of each ``Unit tests (shard N)`` job, or ``(None,
    reason)`` when a shard job has no number or no verdict, or none exists."""
    shard_jobs: dict[int, str] = {}
    for job in jobs:
        name = str(job.get("name", ""))
        if not name.startswith(SHARD_JOB):
            continue
        m = _SHARD_NAME.match(name)
        if not m:
            return None, f"a shard job has no number: {name!r}"
        if job.get("conclusion") not in _RUN_CONCLUSIONS:
            return None, f"{name} has no verdict ({job.get('conclusion')})"
        shard_jobs[int(m.group(1))] = str(job.get("conclusion"))
    if not shard_jobs:
        return None, "the old run has no shard job"
    return shard_jobs, ""


def old_failures(jobs: list[dict], junit_dir: Path) -> tuple[list[str] | None, str]:
    """``(files, "")``: the test files that the old run did not pass, sorted.

    ``jobs`` is the ``jobs`` list of the old run. Each ``Unit tests (shard N)``
    job is paired with ``junit-shard-N/shard-N.xml`` under ``junit_dir``. The
    result is ``(None, reason)`` when a report cannot be trusted:

    * a shard job has no verdict (cancelled, skipped) or no number,
    * a shard report is missing, extra, or does not parse,
    * a shard report holds no test case (the job failed before pytest, or the
      ``empty-report`` step wrote it),
    * a shard job failed, and its report holds no failed or errored case,
    * a failing case names no ``tests/...py`` file.

    A file counts as not passed when a case failed, errored, or skipped for a
    disarmed R8 reason."""
    shard_jobs, why = _shard_verdicts(jobs)
    if shard_jobs is None:
        return None, why
    expected = {
        (junit_dir / f"junit-shard-{n}" / f"shard-{n}.xml").resolve(): n for n in shard_jobs
    }
    found = {p.resolve() for p in junit_dir.rglob("*.xml")} if junit_dir.is_dir() else set()
    if found - set(expected):
        return None, "the old run has a report with no shard job"
    out: set[str] = set()
    for report, n in sorted(expected.items(), key=lambda kv: kv[1]):
        if report not in found:
            return None, f"shard {n} left no report"
        try:
            cases = list(ET.parse(report).getroot().iter("testcase"))
        except ET.ParseError:
            return None, f"the report of shard {n} does not parse"
        if not cases:
            return None, f"the report of shard {n} holds no test"
        failed = 0
        for case in cases:
            if _collection_error(case):
                return None, (
                    f"shard {n} stopped at a collection error in {_file(case) or '?'}, "
                    "so its other tests never ran"
                )
            bad = case.find("failure") is not None or case.find("error") is not None
            failed += bad
            if not bad and not _disarmed_skip(case):
                continue
            path = _file(case)
            if not _SAFE_PATH.match(path):
                return None, f"a case in shard {n} names no test file: {path!r}"
            out.add(path)
        if shard_jobs[n] == "failure" and not failed:
            return None, f"shard {n} failed with no failed test in its report"
    return sorted(out), ""


def find_old_run(runs: list[dict], repo: str, pr: int, new_sha: str) -> dict | None:
    """The newest finished run of this PR for an earlier head SHA.

    ``runs`` is the ``workflow_runs`` list of the GitHub API. The run has to
    come from the same head repository and list this PR. A cancelled run is
    skipped: its shards hold no verdict, and the delta from an older finished
    run still covers it."""
    best: dict | None = None
    for run in runs:
        head_repo = (run.get("head_repository") or {}).get("full_name")
        prs = [p.get("number") for p in run.get("pull_requests") or []]
        if (
            run.get("status") == "completed"
            and run.get("conclusion") in _RUN_CONCLUSIONS
            and run.get("head_sha") != new_sha
            and head_repo == repo
            and pr in prs
            and (best is None or str(run.get("created_at")) > str(best.get("created_at")))
        ):
            best = run
    return best


def repair_decision(
    old_failed: list[str] | None,
    merges: list[str] | None,
    descends: bool,
    delta_gate: dict | None,
    main_gate: dict | None,
    exists=lambda p: True,
) -> dict:
    """``{"mode": "repair"|"full", "reason": str, "files": [...]}``.

    ``old_failed`` None: no usable old report. ``merges``: the merge commits of
    the old head to the new head, None when git could not say. ``delta_gate``:
    the ``affected_tests.gate`` result for the PR's own delta. ``main_gate``:
    the same for main's move between the two runs, None when it is unknown.
    Each gate result already holds the SAFETY fences. The R8 must-run check
    then reads this list as it reads any selection (``--only-selected``)."""

    def full(reason: str) -> dict:
        return {"mode": "full", "reason": reason, "files": []}

    def bad(gate: object) -> bool:
        return not isinstance(gate, dict) or "full" not in gate or "files" not in gate

    if old_failed is None:
        return full("the old run left no usable shard report")
    if not descends:
        return full("the new head does not descend from the old head (force-push)")
    if merges is None:
        return full("git could not read the commits since the old head")
    if merges:
        return full("a merge commit sits between the old head and the new head")
    if bad(delta_gate):
        return full("the selector gave no answer for the delta")
    if bad(main_gate):
        return full("the move of main between the two runs is unknown")
    assert isinstance(delta_gate, dict) and isinstance(main_gate, dict)
    if delta_gate["full"]:
        return full("the delta alone selects the full suite")
    if main_gate["full"]:
        return full("the move of main since the old run selects the full suite")
    picked = (
        set(old_failed)
        | {str(f) for f in delta_gate["files"]}
        | {str(f) for f in main_gate["files"]}
    )
    files = sorted(p for p in picked if _SAFE_PATH.match(p) and exists(p))
    if not files:
        return full("the repair selection came out empty")
    return {
        "mode": "repair",
        "reason": f"{len(files)} files (failed in the old run, plus reach of the fix and of main)",
        "files": files,
    }


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def git_delta(old: str, new: str) -> tuple[bool, list[str] | None, list[str] | None]:
    """``(descends, merge commits, changed files)`` from ``old`` to ``new``."""
    if _git("merge-base", "--is-ancestor", old, new).returncode != 0:
        return False, None, None
    log = _git("log", "--merges", "--format=%H", f"{old}..{new}")
    diff = _git("diff", "--name-only", "--no-renames", old, new)
    if log.returncode != 0 or diff.returncode != 0:
        return True, None, None
    merges = [line for line in log.stdout.split() if line]
    changed = sorted(n.strip().replace("\\", "/") for n in diff.stdout.splitlines() if n.strip())
    return True, merges, changed


def have_commit(sha: str) -> bool:
    """True when ``sha`` is in the clone. Fetches it from origin once if not."""
    if not _SHA.match(sha):
        return False
    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0:
        return True
    _git("fetch", "--no-tags", "--quiet", "origin", sha)
    return _git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def gate_meta() -> dict | None:
    """``{"base": ..., "head": ...}``: the two parents of the merge commit that
    the checkout tested. None when HEAD is not a merge of two commits."""
    out = _git("rev-parse", "HEAD^1", "HEAD^2")
    shas = out.stdout.split() if out.returncode == 0 else []
    if len(shas) != 2 or not all(_SHA.match(s) for s in shas):
        return None
    return {"base": shas[0], "head": shas[1]}


def read_meta(path: Path) -> dict | None:
    """The ``gate-meta`` JSON, or None when it is absent or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(k), str) and _SHA.match(data[k]) for k in ("base", "head")):
        return None
    return {"base": data["base"], "head": data["head"]}


def _gh_json(*args: str) -> dict | list | None:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _gh_get(path: str, **fields: str) -> dict | list | None:
    """A read of the GitHub API. ``gh api`` turns ``-f`` into a POST body, and
    a POST to a read endpoint returns 404. So each read names GET."""
    args = ["api", "-X", "GET", path]
    for key, value in fields.items():
        args += ["-f", f"{key}={value}"]
    return _gh_json(*args)


def _download(repo: str, run_id: object, pattern: str, dest: Path) -> bool:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    got = subprocess.run(
        ["gh", "run", "download", str(run_id), "-R", repo, "-p", pattern, "-D", str(dest)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return got.returncode == 0


def repair(
    repo: str, pr: int, new_sha: str, gate_json: Path, workdir: Path, root: Path, meta: Path
) -> str:
    """Narrow ``gate_json`` for a repair round. Returns the summary line.

    ``meta`` is this run's ``gate-meta`` file. Overwrites ``gate_json`` only in
    repair mode. Every error returns the "full selection" line and leaves the
    file as the normal gate wrote it."""
    try:
        return _repair(repo, pr, new_sha, gate_json, workdir, root, meta)
    except Exception as exc:  # fail open: the normal selection
        return f"full selection: the repair step failed ({exc!r})"


def _repair(
    repo: str, pr: int, new_sha: str, gate_json: Path, workdir: Path, root: Path, meta: Path
) -> str:
    data = _gh_get(
        f"repos/{repo}/actions/workflows/pr-check.yml/runs",
        event="pull_request", status="completed", per_page="50",
    )  # fmt: skip
    runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
    old = find_old_run(runs, repo, pr, new_sha)
    if old is None:
        return "full selection: no earlier finished run of this PR"
    run_id, old_sha = old["id"], str(old["head_sha"])

    def full(reason: str) -> str:
        return f"full selection: {reason} (old run {run_id})"

    new_meta = read_meta(meta)
    if new_meta is None or new_meta["head"] != new_sha:
        return full("this run did not record the merge it tests")
    old_meta_dir = workdir / "old-meta"
    if not _download(repo, run_id, GATE_META_ARTIFACT, old_meta_dir):
        return full("the old run has no gate-meta artifact")
    metas = sorted(old_meta_dir.rglob(GATE_META))
    old_meta = read_meta(metas[0]) if len(metas) == 1 else None
    if old_meta is None or old_meta["head"] != old_sha:
        return full("the old run did not record the merge it tested")
    junit = workdir / "old-junit"
    if not _download(repo, run_id, "junit-shard-*", junit):
        return full("the shard reports of the old run did not download")
    jobs_data = _gh_get(f"repos/{repo}/actions/runs/{run_id}/jobs", per_page="100")
    jobs = jobs_data.get("jobs", []) if isinstance(jobs_data, dict) else []
    failed, why = old_failures(jobs, junit)
    if failed is None:
        return full(why)
    shas = (old_sha, new_sha, old_meta["base"], new_meta["base"])
    if not all(have_commit(s) for s in shas):
        return full("a commit of the old run or of this run is not in the clone")

    import affected_tests  # scripts/affected_tests.py, standard library only

    descends, merges, changed = git_delta(old_sha, new_sha)
    delta = None
    if descends and merges == [] and changed is not None:
        delta = affected_tests.gate(changed, root)
    main_gate = None
    if old_meta["base"] == new_meta["base"]:
        main_gate = {"full": False, "files": [], "reasons": []}
    else:
        main_descends, _, main_changed = git_delta(old_meta["base"], new_meta["base"])
        if main_descends and main_changed is not None:
            main_gate = affected_tests.gate(main_changed, root)
    decision = repair_decision(
        failed, merges, descends, delta, main_gate, exists=lambda p: (root / p).is_file()
    )
    if decision["mode"] != "repair":
        return full(decision["reason"])
    tmp = gate_json.with_suffix(".repair.tmp")
    tmp.write_text(json.dumps({"full": False, "files": decision["files"]}), encoding="utf-8")
    tmp.replace(gate_json)
    return f"repair mode: {decision['reason']}. Old run: {run_id}"


# CIV-17. How many unit shards a selection needs. CIV-17b (2026-11-07) counts
# measured SECONDS, not files: a Python PR selected 400 to 500 files, so a file
# count gave 6 shards for every one of them, and the tiers never applied. The
# seconds come from `.test_durations`, the file that pytest-split uses to cut
# the shards. A file with no timing counts DEFAULT_FILE_S. Without the timing
# file, the old tiers by file count apply, so `changes` and the shards still
# agree on the count.
ALL_SHARDS = 6
SHARD_TIERS = ((40, 1), (120, 3))
SHARD_TARGET_S = 600
DEFAULT_FILE_S = 10.0
DURATIONS_PATH = Path(".test_durations")


def file_seconds(path: Path | None = None) -> dict[str, float] | None:
    """Seconds of each test file, summed over its tests. None when unreadable."""
    path = path or DURATIONS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, float] = {}
        for node, secs in data.items():
            out[node.split("::", 1)[0]] = out.get(node.split("::", 1)[0], 0.0) + float(secs)
        return out or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def shard_count(mode: str, paths: list[str], seconds: dict[str, float] | None = None) -> int:
    """The shards that ``mode`` (from ``select_shard``) needs: 0 for an empty
    selection, ALL_SHARDS for a full run. A selection takes one shard for each
    SHARD_TARGET_S of measured time, at most ALL_SHARDS."""
    if mode == "empty":
        return 0
    if mode != "selection":
        return ALL_SHARDS
    if seconds:
        total = sum(seconds.get(p, DEFAULT_FILE_S) for p in paths)
        return max(1, min(ALL_SHARDS, math.ceil(total / SHARD_TARGET_S)))
    for limit, shards in SHARD_TIERS:
        if len(paths) <= limit:
            return shards
    return ALL_SHARDS


def shard_list(count: int) -> str:
    """The ``shards`` job output: a JSON list that the ``test`` matrix reads."""
    return json.dumps(list(range(1, count + 1)), separators=(",", ":"))


def gate_output(gate_json: Path, ignored: str = "") -> list[str]:
    """``full=``, ``files=`` and ``shards=`` lines for $GITHUB_OUTPUT.

    Fails open to full (and so to all shards). ``ignored`` is the output of
    ``fences.py pytest-ignore``: a shard drops those files from the selection,
    so the count has to drop them too."""
    lines = _gate_lines(gate_json)
    full = lines[0].partition("=")[2]
    files = lines[1].partition("=")[2]
    mode, paths = select_shard(full, files, ignored, "success")
    return [*lines, f"shards={shard_list(shard_count(mode, paths, file_seconds()))}"]


def _gate_lines(gate_json: Path) -> list[str]:
    try:
        data = json.loads(gate_json.read_text(encoding="utf-8"))
        full = bool(data["full"])
        files = [str(f) for f in data["files"]]
        if not full and not all(_SAFE_PATH.match(f) for f in files):
            return ["full=true", "files="]
        text = " ".join(files)
        if len(text) > MAX_SELECTION_CHARS:
            return ["full=true", "files="]
    except Exception:  # unreadable or malformed: run everything
        return ["full=true", "files="]
    return [f"full={'true' if full else 'false'}", f"files={'' if full else text}"]


def select_shard(
    full: str, files: str, ignored: str = "", changes_result: str = "success"
) -> tuple[str, list[str]]:
    """What a shard runs: ``full``, ``selection`` or ``empty``.

    Only the exact string ``false`` selects. An empty or unknown ``full`` (the
    ``changes`` job failed) runs the whole suite. A selection never reaches
    pytest as an empty list, because pytest then collects the whole tree."""
    if full != "false" or changes_result != "success":
        return "full", []
    paths = files.split()
    if not all(_SAFE_PATH.match(p) for p in paths):
        return "full", []
    skip = {t.removeprefix("--ignore=") for t in ignored.split()}
    paths = [p for p in paths if p not in skip]
    return ("selection", paths) if paths else ("empty", [])


_EMPTY_REPORT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<testsuites><testsuite name="pytest" errors="0" failures="0" skipped="0" '
    'tests="0" time="0.0"></testsuite></testsuites>'
)


def empty_report(out: Path) -> bool:
    """Write a zero-test JUnit report unless pytest already wrote one."""
    if out.exists():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_EMPTY_REPORT, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--junit", type=Path, required=True)
    c.add_argument("--must-run", nargs="*", default=[])
    c.add_argument("--never-skip", nargs="*", default=[])
    c.add_argument(
        "--only-selected",
        nargs="*",
        default=None,
        help="a selection run: check only the must-run files in this list",
    )
    c.add_argument(
        "--tree-root",
        type=Path,
        default=Path("."),
        help="the checkout: every --must-run path has to exist under it",
    )
    c.add_argument(
        "--disarmed",
        action="append",
        help="a skip reason that means an R8 gate is disarmed (repeatable)",
    )
    d = sub.add_parser("durations")
    d.add_argument("--junit", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("gate-output", help="turn affected_tests.py --gate JSON into step outputs")
    g.add_argument("--json", type=Path, required=True)
    g.add_argument("--ignored", default="", help="the output of fences.py pytest-ignore")
    sel = sub.add_parser("select", help="what this shard runs, as step outputs")
    sel.add_argument("--full", default="")
    sel.add_argument("--files", default="")
    sel.add_argument("--changes-result", default="", help="needs.changes.result")
    sel.add_argument("--ignored", default="", help="the output of fences.py pytest-ignore")
    e = sub.add_parser("empty-report", help="write a zero-test JUnit report if none exists")
    e.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("r8-covered", help="every R8 skip of a no-database run is in the --r8 list")
    r.add_argument("--junit", type=Path, required=True)
    r.add_argument("--list", type=Path, required=True, help="the output of affected_tests.py --r8")
    rp = sub.add_parser("repair", help="narrow the selection for a repair round (CIV-32)")
    rp.add_argument("--repo", required=True)
    rp.add_argument("--pr", type=int, required=True)
    rp.add_argument("--new-sha", required=True)
    rp.add_argument(
        "--json", type=Path, required=True, help="the gate JSON, rewritten in repair mode"
    )
    rp.add_argument("--workdir", type=Path, default=Path("."))
    rp.add_argument("--meta", type=Path, default=Path(GATE_META), help="this run's gate-meta file")
    gm = sub.add_parser("gate-meta", help="write the base and head of the tested merge (CIV-32)")
    gm.add_argument("--out", type=Path, default=Path(GATE_META))
    args = parser.parse_args(argv)

    if args.cmd == "repair":
        root = Path(".").resolve()
        print(repair(args.repo, args.pr, args.new_sha, args.json, args.workdir, root, args.meta))
        return 0
    if args.cmd == "gate-meta":
        # Never fails the step. No file means the next run cannot repair.
        meta = gate_meta()
        if meta is None:
            print("gate-meta: HEAD is not a merge of two commits, so no file")
            return 0
        args.out.write_text(json.dumps(meta), encoding="utf-8")
        print(f"gate-meta: base {meta['base']} head {meta['head']}")
        return 0
    if args.cmd == "r8-covered":
        problems = r8_covered(args.junit, args.list)
        for p in problems:
            print(f"::error::{p}")
        return 1 if problems else 0
    if args.cmd == "gate-output":
        print(chr(10).join(gate_output(args.json, args.ignored)))
        return 0
    if args.cmd == "select":
        mode, paths = select_shard(args.full, args.files, args.ignored, args.changes_result)
        print(f"mode={mode}")
        print(f"paths={' '.join(paths)}")
        print(f"splits={shard_count(mode, paths, file_seconds()) or ALL_SHARDS}")
        return 0
    if args.cmd == "empty-report":
        print("wrote an empty report" if empty_report(args.out) else "report exists")
        return 0

    if args.cmd == "durations":
        data = durations(args.junit)
        args.out.write_text(json.dumps(data, indent=0) + "\n", encoding="utf-8")
        print(f"wrote {len(data)} timings to {args.out}")
        return 0

    problems = check(
        args.junit,
        args.must_run,
        args.never_skip,
        args.disarmed or DISARMED,
        args.only_selected,
        args.tree_root,
    )
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
