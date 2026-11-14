"""Fence for scripts/ci_unit_guard.py and the sharded unit job that calls it.

The reports come from a REAL pytest run, not a hand-written XML sample, so a
change in how pytest writes a skip reason breaks this test, not the gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import ci_unit_guard as guard  # noqa: E402

_SUITE = """
import pytest

def test_runs():
    pass

@pytest.mark.skip(reason="TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres.")
def test_ladder_gate():
    pass

@pytest.mark.skip(reason="AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset — no Redis")
def test_redis_gate():
    pass

class TestGroup:
    @pytest.mark.parametrize("n", [1, 2])
    def test_param(self, n):
        pass
"""


def _report(tmp_path: Path, body: str = _SUITE, name: str = "test_sample.py") -> Path:
    (tmp_path / name).write_text(body, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    out = tmp_path / "junit"
    out.mkdir(exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            name,
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit1",
            f"--junitxml={out / 'shard-1.xml'}",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
    )
    return out


def test_a_skip_that_names_the_variable_first_is_a_disarmed_gate(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), [], [])
    assert len(problems) == 1
    assert "test_ladder_gate" in problems[0]


def test_a_redis_skip_that_mentions_the_variable_later_passes(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), [], [])
    assert not any("test_redis_gate" in p for p in problems)


def test_a_file_no_shard_ran_fails(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), ["test_sample.py", "test_missing.py"], [])
    assert "no shard ran test_missing.py" in problems
    assert "no shard ran test_sample.py" not in problems


def test_any_skip_in_a_never_skip_file_fails(tmp_path: Path) -> None:
    problems = guard.check(_report(tmp_path), [], ["test_sample.py"])
    assert any("no-database fence skipped" in p and "test_redis_gate" in p for p in problems)


def test_durations_use_pytest_split_node_ids(tmp_path: Path) -> None:
    data = guard.durations(_report(tmp_path))
    assert "test_sample.py::test_runs" in data
    assert "test_sample.py::TestGroup::test_param[1]" in data


# ── The workflow wiring ────────────────────────────────────────────────────

_WORKFLOW = _ROOT / ".github/workflows/pr-check.yml"


def _jobs() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_the_required_check_name_is_the_aggregator() -> None:
    """Branch protection requires a check named exactly "Unit tests"."""
    jobs = _jobs()
    named = [k for k, j in jobs.items() if j.get("name") == "Unit tests"]
    assert named == ["unit-tests"]
    # `changes` joins `needs` so the guard knows a selection run (CIV-3).
    assert jobs["unit-tests"]["needs"] == ["test", "changes"]
    assert jobs["unit-tests"]["if"] == "always()"
    run = "\n".join(s.get("run", "") for s in jobs["unit-tests"]["steps"])
    assert "scripts/ci_unit_guard.py check" in run
    # CIV-33c-2: the disarmed reasons are the `r8` key of tests/fences.json.
    assert "--lists-from-fences tests/fences.json" in run
    lists = guard.fence_lists((_ROOT / "tests/fences.json").read_text(encoding="utf-8"))
    assert set(guard.DISARMED) <= set(lists["--disarmed"])


def test_every_shard_runs_and_writes_a_report() -> None:
    test = _jobs()["test"]
    run = "\n".join(s.get("run", "") for s in test["steps"])
    # CIV-17: the matrix and the split count both follow the selection.
    assert test["strategy"]["matrix"]["shard"] == (
        "${{ fromJSON(needs.changes.outputs.shards || '[1,2,3,4,5,6]') }}"
    )
    assert "--splits ${{ steps.pick.outputs.splits }}" in run
    assert "--group ${{ matrix.shard }}" in run
    # CIV-8k: a hung test prints its stack after 900 s, so the log names the cause.
    assert "-o faulthandler_timeout=900" in run
    assert "junit_family=xunit1" in run
    assert "--junitxml=" in run


def test_the_timings_file_exists() -> None:
    """Without it pytest-split divides by test COUNT, and one 7-minute file
    lands in a shard beside a normal share."""
    assert (_ROOT / ".test_durations").is_file()


_MODULE_SKIP = """
import pytest

pytest.skip("CUSTOMER_CONSOLE_DATABASE_URL unset — R8", allow_module_level=True)


def test_never_runs():
    pass
"""


def test_a_module_skipped_at_collection_is_disarmed_and_did_not_run(tmp_path: Path) -> None:
    """pytest writes it as "collection skipped", with the reason in the text."""
    junit = _report(tmp_path, _MODULE_SKIP, "test_gated.py")
    problems = guard.check(junit, ["test_gated.py"], [])
    assert any(p.startswith("R8 gate disarmed: test_gated.py") for p in problems)
    assert "no shard ran test_gated.py" in problems


# ── CIV-3: the PR tier runs a selection ────────────────────────────────────

SEL = ["tests/unit/test_sample.py", "tests/unit/test_other.py"]


def test_only_selected_checks_the_must_run_files_in_the_selection(tmp_path: Path) -> None:
    junit = _report(tmp_path)
    must = ["test_sample.py", "test_missing.py"]
    # A full run (None) still fails on the file no shard ran.
    assert "no shard ran test_missing.py" in guard.check(junit, must, [])
    # A selection that leaves the missing file out does not check it.
    assert "no shard ran test_missing.py" not in guard.check(
        junit, must, [], only_selected=["test_sample.py"]
    )
    # A selection that names it still checks it, so a lost shard is caught.
    assert "no shard ran test_missing.py" in guard.check(
        junit, must, [], only_selected=["test_sample.py", "test_missing.py"]
    )
    # An empty selection checks no must-run file.
    assert guard.check(junit, must, [], only_selected=[]) == guard.check(junit, [], [])


def test_only_selected_reaches_the_cli(tmp_path: Path) -> None:
    junit = _report(tmp_path)
    base = [sys.executable, str(_ROOT / "scripts/ci_unit_guard.py"), "check", "--junit", str(junit)]
    (tmp_path / "test_missing.py").write_text("", encoding="utf-8")
    args = [*base, "--tree-root", str(tmp_path), "--disarmed", "NEVER_MATCHES unset"]
    full = subprocess.run([*args, "--must-run", "test_missing.py"], capture_output=True, text=True)
    assert full.returncode == 1 and "no shard ran test_missing.py" in full.stdout
    sel = subprocess.run(
        [*args, "--only-selected", "test_sample.py", "--must-run", "test_missing.py"],
        capture_output=True,
        text=True,
    )
    assert sel.returncode == 0, sel.stdout
    empty = subprocess.run(
        [*args, "--only-selected", "--must-run", "test_missing.py"], capture_output=True, text=True
    )
    assert empty.returncode == 0, empty.stdout


def test_a_skipped_pytest_step_still_leaves_a_report_the_guard_reads(tmp_path: Path) -> None:
    out = tmp_path / "junit" / "shard-3.xml"
    assert guard.empty_report(out) is True
    assert guard.check(out.parent, ["tests/unit/test_x.py"], [], only_selected=[]) == []
    # Without the report the guard stops, which is why the step exists.
    with pytest.raises(SystemExit):
        guard.check(tmp_path / "nothing", [], [])


def test_the_empty_report_never_replaces_a_real_one(tmp_path: Path) -> None:
    out = tmp_path / "shard-1.xml"
    out.write_text("real", encoding="utf-8")
    assert guard.empty_report(out) is False
    assert out.read_text(encoding="utf-8") == "real"


def test_an_empty_pytest_group_exits_five(tmp_path: Path) -> None:
    """The workflow turns exit 5 into a pass on a selection run, and only there."""
    (tmp_path / "test_one.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    res = subprocess.run(
        [
            sys.executable, "-m", "pytest", "test_one.py", "-q", "-p", "no:cacheprovider",
            "--splits", "6", "--group", "6", "--splitting-algorithm", "duration_based_chunks",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 5, res.stdout + res.stderr


@pytest.mark.parametrize(
    "full,files,ignored,mode,paths",
    [
        ("false", " ".join(SEL), "", "selection", SEL),
        ("false", "", "", "empty", []),
        ("false", SEL[0], "--ignore=" + SEL[0], "empty", []),
        ("false", " ".join(SEL), "--ignore=" + SEL[1], "selection", [SEL[0]]),
        ("true", " ".join(SEL), "", "full", []),
        ("", " ".join(SEL), "", "full", []),
        ("", "", "", "full", []),
        ("false", "tests/unit/a.py ../etc/x.py", "", "full", []),
        ("false", "tests/unit/a.py;rm", "", "full", []),
    ],
)
def test_select_shard_never_hands_pytest_an_empty_list(full, files, ignored, mode, paths) -> None:
    assert guard.select_shard(full, files, ignored) == (mode, paths)


def test_gate_output_fails_open(tmp_path: Path) -> None:
    def run(text: str | None) -> list[str]:
        f = tmp_path / "gate.json"
        f.unlink(missing_ok=True)
        if text is not None:
            f.write_text(text, encoding="utf-8")
        # `shards=` (CIV-17) has its own tests below.
        return [x for x in guard.gate_output(f) if not x.startswith("shards=")]

    ok = '{"full": false, "files": ["tests/unit/a.py", "tests/unit/b.py"], "reasons": []}'
    assert run(ok) == ["full=false", "files=tests/unit/a.py tests/unit/b.py"]
    assert run('{"full": true, "files": ["tests/unit/a.py"], "reasons": ["x"]}') == [
        "full=true",
        "files=",
    ]
    assert run('{"full": false, "files": [], "reasons": []}') == ["full=false", "files="]
    assert run(None) == ["full=true", "files="]  # the selector wrote nothing
    assert run("not json") == ["full=true", "files="]
    assert run('{"files": []}') == ["full=true", "files="]
    assert run('{"full": false, "files": ["../x.py"]}') == ["full=true", "files="]
    big = '{"full": false, "files": ["tests/unit/' + "a" * 200_000 + '.py"]}'
    assert run(big) == ["full=true", "files="]


def _steps(job: str) -> list[dict]:
    return _jobs()[job]["steps"]


def test_the_shards_wait_for_changes_and_still_run_when_it_fails() -> None:
    test = _jobs()["test"]
    assert test["needs"] == "changes"
    # `!cancelled()` runs the shards after a failed `changes` (fail open). A
    # plain `needs` would skip them, and a skipped dependency is not a pass.
    cond = test["if"].replace("${{", "").replace("}}", "").strip()
    assert cond.startswith("!cancelled()")
    assert "always()" not in cond
    # CIV-17: the only other term is an empty selection list.
    assert cond == "!cancelled() && needs.changes.outputs.shards != '[]'"


def test_the_changes_job_exports_the_selection_from_a_step_before_diff() -> None:
    job = _jobs()["changes"]
    assert job["outputs"]["full"] == "${{ steps.gate.outputs.full }}"
    assert job["outputs"]["files"] == "${{ steps.gate.outputs.files }}"
    ids = [s.get("id") for s in job["steps"]]
    assert ids.index("gate") < ids.index("diff") == len(ids) - 1
    gate = job["steps"][ids.index("gate")]
    assert "--gate --base" in gate["run"]
    assert gate["env"]["BASE"] == "${{ github.event.pull_request.base.sha }}"
    assert "uv " not in gate["run"]  # the job has plain python3


def test_an_empty_selection_skips_pytest_and_exit_five_passes_only_on_a_selection() -> None:
    by_name = {s.get("name", ""): s for s in _steps("test")}
    run = by_name["Run unit tests (this shard)"]
    assert run["if"] == "steps.pick.outputs.mode != 'empty'"
    assert "-eq 5" in run["run"] and '"$SHARD_MODE" = "selection"' in run["run"]
    assert "tests/unit/" in run["run"]  # the full run still collects the tree
    empty = by_name["Write an empty report when no test ran"]
    assert "empty-report" in empty["run"]
    assert "!cancelled()" in empty["if"] and "mode != 'full'" in empty["if"]


def test_only_a_full_run_publishes_the_timings() -> None:
    steps = {s.get("name", s.get("uses", "")): s for s in _steps("unit-tests")}
    for key in ("Write the measured timings", "actions/upload-artifact@v4"):
        cond = steps[key]["if"]
        assert "needs.changes.outputs.full == 'true'" in cond
        assert "needs.changes.result == 'success'" in cond


def test_the_guard_step_passes_only_selected_on_a_selection_run() -> None:
    step = next(s for s in _steps("unit-tests") if s.get("name", "").startswith("The R8 suites ran"))
    assert step["env"]["FULL"] == "${{ needs.changes.outputs.full }}"
    assert 'if [ "$FULL" = "false" ]' in step["run"]
    assert "--only-selected $FILES" in step["run"]
    # CIV-33c-2: the lists are the `r8` key of tests/fences.json, not literals here.
    assert "--lists-from-fences tests/fences.json" in step["run"]
    assert "--must-run" not in step["run"] and "--never-skip" not in step["run"]
    assert "tests/unit/test_auto_park.py" in guard.fence_lists(
        (_ROOT / "tests/fences.json").read_text(encoding="utf-8")
    )["--must-run"]


def test_a_must_run_path_missing_from_the_tree_always_fails(tmp_path: Path) -> None:
    """A PR that deletes an R8 suite but keeps its line must go red at once."""
    junit = _report(tmp_path)
    tree = tmp_path / "tree"
    (tree / "tests/unit").mkdir(parents=True)
    (tree / "tests/unit/test_alive.py").write_text("", encoding="utf-8")
    must = ["tests/unit/test_alive.py", "tests/unit/test_deleted.py"]
    for only in (None, [], ["tests/unit/test_alive.py"]):
        problems = guard.check(junit, must, [], only_selected=only, tree_root=tree)
        assert any("not in the tree: tests/unit/test_deleted.py" in p for p in problems), only
        assert not any("test_alive.py" in p and "tree" in p for p in problems), only


# ── CIV-33c-2: the lists are the `r8` key of tests/fences.json ─────────────


def _r8_report(tmp_path: Path, ran: list[str], skipped: list[str] | None = None) -> Path:
    """A report with one passing test for each file in `ran`, and one skipped
    test for each file in `skipped`."""
    cases = "".join(
        f'<testcase classname="{f[:-3].replace("/", ".")}" name="test_ok" file="{f}" time="0.1"/>'
        for f in ran
    ) + "".join(
        f'<testcase classname="{f[:-3].replace("/", ".")}" name="test_s" file="{f}" time="0.1">'
        '<skipped type="pytest.skip" message="a plain skip"/></testcase>'
        for f in skipped or []
    )
    out = tmp_path / "junit"
    out.mkdir(exist_ok=True)
    (out / "shard-1.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        f"{cases}</testsuite></testsuites>",
        encoding="utf-8",
    )
    return out


def _fences_file(tmp_path: Path, r8: object) -> Path:
    path = tmp_path / "fences.json"
    path.write_text(json.dumps({"r8": r8}) if r8 is not None else "{}", encoding="utf-8")
    return path


def _guard_cli(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts/ci_unit_guard.py"), "check", *map(str, args)],
        capture_output=True,
        text=True,
    )


_A, _B, _C = "tests/unit/test_a.py", "tests/unit/test_b.py", "tests/unit/test_c.py"
_OK = {"disarmed": ["NEVER_MATCHES unset"], "never_skip": [_A], "must_run": [_A, _B]}


def test_lists_from_fences_applies_the_must_run_and_never_skip_lists(tmp_path: Path) -> None:
    fences = _fences_file(tmp_path, _OK)
    tree = ["--tree-root", tmp_path]
    for f in (_A, _B):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("", encoding="utf-8")
    good = _guard_cli("--junit", _r8_report(tmp_path, [_A, _B]), "--lists-from-fences", fences, *tree)
    assert good.returncode == 0, good.stdout
    assert "2 must-run, 1 never-skip" in good.stdout
    lost = _guard_cli("--junit", _r8_report(tmp_path, [_A]), "--lists-from-fences", fences, *tree)
    assert lost.returncode == 1 and f"no shard ran {_B}" in lost.stdout
    skip = _guard_cli(
        "--junit", _r8_report(tmp_path, [_B], skipped=[_A]), "--lists-from-fences", fences, *tree
    )
    assert skip.returncode == 1 and "skipped" in skip.stdout


def test_lists_from_fences_still_takes_a_selection(tmp_path: Path) -> None:
    fences = _fences_file(tmp_path, _OK)
    for f in (_A, _B):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("", encoding="utf-8")
    done = _guard_cli(
        "--junit", _r8_report(tmp_path, [_A]), "--lists-from-fences", fences,
        "--tree-root", tmp_path, "--only-selected", _A,
    )
    assert done.returncode == 0, done.stdout


@pytest.mark.parametrize(
    "r8",
    [
        None,
        "text",
        {**_OK, "must_run": []},
        {**_OK, "never_skip": []},
        {"never_skip": [_A], "must_run": [_A]},
        {**_OK, "must_run": "tests/unit/test_a.py"},
        {**_OK, "must_run": [_A, "tests/unit/$(evil).py"]},
    ],
)
def test_lists_from_fences_fails_closed(tmp_path: Path, r8: object) -> None:
    """A missing key, an empty list or a bad path never becomes an empty list."""
    junit = _r8_report(tmp_path, [_A, _B])
    done = _guard_cli("--junit", junit, "--lists-from-fences", _fences_file(tmp_path, r8))
    assert done.returncode == 1
    assert "R8 guard lists unknown" in done.stdout
    assert "checked " not in done.stdout


def test_lists_from_fences_fails_closed_on_a_file_that_is_not_json_or_not_there(tmp_path: Path) -> None:
    junit = _r8_report(tmp_path, [_A])
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    for path in (bad, tmp_path / "missing.json"):
        done = _guard_cli("--junit", junit, "--lists-from-fences", path)
        assert done.returncode == 1 and "R8 guard lists unknown" in done.stdout


def test_the_real_fences_hold_a_readable_r8_key() -> None:
    lists = guard.fence_lists((_ROOT / "tests/fences.json").read_text(encoding="utf-8"))
    assert len(lists["--must-run"]) > 100
    assert lists["--disarmed"]
    assert len(set(lists["--must-run"])) == len(lists["--must-run"]), "a file is listed twice"
    # The guard's own check needs every path in the tree.
    missing = [p for p in lists["--must-run"] + lists["--never-skip"] if not (_ROOT / p).is_file()]
    assert not missing, missing


# ── CIV-33c-2: no test reads a workflow for an R8-listed path ──────────────

# These files hold the guard itself. They name listed paths in fixtures.
_READS_WORKFLOWS_OK = {
    "test_ci_unit_guard.py",
    "test_green_pointer.py",
    "test_deploy_pipeline.py",
    "test_affected_tests.py",
    "test_fence_lists.py",
}
_WORKFLOW_WORDS = ("workflows", "pr-check.yml", "deploy.yml")


def _function_nodes(tree):
    import ast

    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def workflow_readers_of_listed_paths(files: list[Path], listed: set[str]) -> list[str]:
    """`file::function` for each function that reads a workflow file and names
    a listed path in a string. The lists left the workflow text in CIV-33c-2, so
    such a check looks for a line that is gone. It reads `tests/unit/_r8_lists.py`."""
    import ast

    found: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "tests/unit/" not in text or not any(w in text for w in _WORKFLOW_WORDS):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for fn in _function_nodes(tree):
            reads = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr in ("read_text", "read_bytes", "open")
                and any(w in (ast.get_source_segment(text, c) or "") for w in _WORKFLOW_WORDS)
                for c in ast.walk(fn)
            )
            if not reads:
                continue
            for c in ast.walk(fn):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    plain = c.value.replace(chr(92) * 2 + ".", ".").replace(chr(92) + ".", ".")
                    if any(p in plain for p in listed):
                        found.append(f"{path.name}::{fn.name}")
                        break
    return found


def test_no_test_reads_a_workflow_for_a_path_of_the_r8_lists(tmp_path: Path) -> None:
    lists = guard.fence_lists((_ROOT / "tests/fences.json").read_text(encoding="utf-8"))
    listed = set(lists["--must-run"]) | set(lists["--never-skip"])
    files = [p for p in sorted((_ROOT / "tests/unit").glob("test_*.py")) if p.name not in _READS_WORKFLOWS_OK]
    bad = workflow_readers_of_listed_paths(files, listed)
    assert not bad, f"these read a workflow for an R8 path, and use tests/unit/_r8_lists.py instead: {bad}"
    # The scan itself finds the old shape.
    old = tmp_path / "test_old_shape.py"
    old.write_text(
        "import re\n"
        "def test_x():\n"
        "    wf = (ROOT / '.github' / 'workflows' / 'pr-check.yml').read_text()\n"
        "    assert re.search('tests/unit/test_auto_park.py', wf)\n",
        encoding="utf-8",
    )
    assert workflow_readers_of_listed_paths([old], listed) == ["test_old_shape.py::test_x"]
    ok = tmp_path / "test_new_shape.py"
    ok.write_text(
        "def test_x():\n    assert 'tests/unit/test_auto_park.py' in r8_lists.must_run()  # pr-check.yml\n",
        encoding="utf-8",
    )
    assert workflow_readers_of_listed_paths([ok], listed) == []


def test_a_failed_changes_job_means_a_full_run() -> None:
    assert guard.select_shard("false", SEL[0], "", "success") == ("selection", [SEL[0]])
    for result in ("failure", "skipped", "cancelled", ""):
        assert guard.select_shard("false", SEL[0], "", result) == ("full", []), result


def test_the_workflow_requires_a_successful_changes_job_for_a_selection() -> None:
    pick = next(s for s in _steps("test") if s.get("id") == "pick")
    assert pick["env"]["CHANGES"] == "${{ needs.changes.result }}"
    assert '--changes-result "$CHANGES"' in pick["run"]
    guard_step = next(s for s in _steps("unit-tests") if s.get("name", "").startswith("The R8"))
    assert guard_step["env"]["CHANGES"] == "${{ needs.changes.result }}"
    assert '[ "$CHANGES" = "success" ]' in guard_step["run"]


def test_the_shard_step_env_names_cannot_leak_into_a_script_under_test() -> None:
    """The tests start scripts with the shard's environment. `MODE=full`
    reached scripts/vps_pull.sh, which reads $MODE, and it exited 2 ("unknown
    MODE: full") in test_deploy_serialize.py on PR 1749. Every env name of
    the step carries the SHARD_ prefix, which no script reads."""
    by_name = {s.get("name", ""): s for s in _steps("test")}
    env = by_name["Run unit tests (this shard)"].get("env", {})
    assert env, "the step passes its selection through env"
    bad = sorted(k for k in env if not k.startswith("SHARD_"))
    assert not bad, f"generic env names leak into the scripts under test: {bad}"


# ── CIV-4c: every R8 skip of the no-database run is in the --r8 list ──────
#
# deploy.yml's `test` job has no database, so each R8 suite skips there. The
# release gate runs `affected_tests.py --r8` against two real databases. A
# file that skips for an R8 reason and is not in that list runs nowhere.

_LADDER = "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with the ladder"
_CONSOLE = "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "


def _case(file: str, name: str, skip: str | None = None, collection: bool = False) -> str:
    if skip is None:
        inner = ""
    elif collection:
        inner = f'<skipped message="collection skipped">{file}:3: Skipped: {skip}</skipped>'
    else:
        inner = f'<skipped type="pytest.skip" message="{skip}">{file}:9: {skip}</skipped>'
    klass = file[: -len(".py")].replace("/", ".")
    return f'<testcase classname="{klass}" file="{file}" name="{name}" time="0.1">{inner}</testcase>'


def _junit(tmp_path: Path, *cases: str) -> Path:
    out = tmp_path / "junit"
    out.mkdir(exist_ok=True)
    body = "".join(cases)
    (out / "test.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
        f'tests="{len(cases)}">{body}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return out


def _list(tmp_path: Path, *paths: str) -> Path:
    f = tmp_path / "r8_files.txt"
    f.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
    return f


A = "tests/unit/test_a_r8.py"
B = "tests/unit/test_b.py"


def test_r8_covered_fails_for_an_r8_skip_outside_the_list(tmp_path: Path) -> None:
    junit = _junit(tmp_path, _case(A, "test_x", _LADDER), _case(B, "test_y", _CONSOLE))
    problems = guard.r8_covered(junit, _list(tmp_path, A))
    assert len(problems) == 1, problems
    assert B in problems[0] and "--r8" in problems[0]


def test_r8_covered_passes_when_every_r8_skip_is_listed(tmp_path: Path, capsys) -> None:
    junit = _junit(
        tmp_path,
        _case(A, "test_x", _LADDER),
        _case(A, "test_x2", _LADDER),
        _case(B, "test_y", _CONSOLE),
        _case("tests/unit/test_c.py", "test_runs"),
    )
    assert guard.r8_covered(junit, _list(tmp_path, A, B)) == []
    assert "R8 skips checked: 2 files, all in the --r8 list" in capsys.readouterr().out


@pytest.mark.parametrize(
    "reason",
    [
        "AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset — no Redis",
        "AGENT_WRITES_REDIS_URL or TENANT_LADDER_DATABASE_URL unset — this class "
        "runs the nonce and the cap on a REAL Redis 7.",
        "Windows only",
        "requires docker",
        "gateway not installed",
    ],
)
def test_r8_covered_ignores_a_skip_of_another_reason(tmp_path: Path, reason: str) -> None:
    junit = _junit(tmp_path, _case(A, "test_x", _LADDER), _case(B, "test_y", reason))
    assert guard.r8_covered(junit, _list(tmp_path, A)) == []


def test_r8_covered_counts_a_module_skipped_at_collection(tmp_path: Path) -> None:
    junit = _junit(tmp_path, _case(A, "test_x", _LADDER), _case(B, "test_b", _CONSOLE, True))
    problems = guard.r8_covered(junit, _list(tmp_path, A))
    assert len(problems) == 1 and B in problems[0], problems


def test_r8_covered_reads_a_real_collection_skip(tmp_path: Path) -> None:
    """The report of a real pytest run, so a change in pytest's format shows here."""
    junit = _report(tmp_path, _MODULE_SKIP, "test_gated.py")
    assert any("test_gated.py" in p for p in guard.r8_covered(junit, _list(tmp_path, "other.py")))
    assert guard.r8_covered(junit, _list(tmp_path, "test_gated.py")) == []


_TEST_SKIP = """
import pytest


def test_needs_the_database():
    pytest.skip("TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres")
"""


def test_r8_covered_reads_a_real_test_skip(tmp_path: Path) -> None:
    """CIV-4c review P2. A skip inside a test, written by a real pytest run: if
    pytest changed the `message` of a normal skip, the guard would read nothing
    and pass with 0 files. Here that shows as a red test."""
    junit = _report(tmp_path, _TEST_SKIP, "test_needs_db.py")
    assert any("test_needs_db.py" in p for p in guard.r8_covered(junit, _list(tmp_path, "other.py")))
    assert guard.r8_covered(junit, _list(tmp_path, "test_needs_db.py")) == []


def test_r8_covered_names_the_module_that_collects_an_inherited_gated_class(tmp_path: Path) -> None:
    """CIV-4c verifier. `test_reuse.py` subclasses a gated class of
    `test_base.py`. JUnit `file` says test_base.py, `classname` says
    test_reuse. With only test_base.py in the list, test_reuse.py runs nowhere
    and the guard must say so."""
    (tmp_path / "test_base.py").write_text(
        "import pytest\n\n\nclass TestBaseR8:\n    def test_it(self):\n"
        "        pytest.skip('TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres')\n",
        encoding="utf-8",
    )
    junit = _report(
        tmp_path, "from test_base import TestBaseR8\n\n\nclass TestReuse(TestBaseR8):\n    pass\n",
        "test_reuse.py",
    )
    only_base = guard.r8_covered(junit, _list(tmp_path, "test_base.py"))
    assert len(only_base) == 1 and "test_reuse.py" in only_base[0], only_base
    assert guard.r8_covered(junit, _list(tmp_path, "test_base.py", "test_reuse.py")) == []


@pytest.mark.parametrize(
    "reason",
    [
        "TENANT_LADDER_DATABASE_URL unset",
        "CUSTOMER_CONSOLE_DATABASE_URL unset",
        "TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres",
        "TENANT_LADDER_DATABASE_URL unset - R8 needs Postgres",
        "TENANT_LADDER_DATABASE_URL unset. ⚠️ This suite is the ONLY one that",
        "TENANT_LADDER_DATABASE_URL unset or CUSTOMER_CONSOLE_DATABASE_URL unset",
        "R8 requires a REAL Postgres; set CUSTOMER_CONSOLE_DATABASE_URL",
        "R8: set TENANT_LADDER_DATABASE_URL (scripts/dev_db.sh)",
        "R8 needs TENANT_LADDER_DATABASE_URL",
        "R8 requires a REAL Postgres.",
    ],
)
def test_r8_covered_knows_each_r8_reason_of_the_repo(tmp_path: Path, reason: str) -> None:
    assert guard.is_r8_reason(reason), reason
    junit = _junit(tmp_path, _case(A, "test_x", _LADDER), _case(B, "test_y", reason))
    assert len(guard.r8_covered(junit, _list(tmp_path, A))) == 1


def test_r8_covered_fails_closed_on_no_report(tmp_path: Path) -> None:
    listed = _list(tmp_path, A)
    (tmp_path / "empty").mkdir()
    assert guard.r8_covered(tmp_path / "empty", listed)  # a directory with no report
    assert guard.r8_covered(tmp_path / "absent", listed)  # no directory
    zero = tmp_path / "zero"
    zero.mkdir()
    (zero / "test.xml").write_text(_EMPTY, encoding="utf-8")
    assert any("no test" in p for p in guard.r8_covered(zero, listed))  # zero cases


_EMPTY = '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="0"/></testsuites>'


def test_r8_covered_fails_closed_on_an_empty_or_absent_list(tmp_path: Path) -> None:
    junit = _junit(tmp_path, _case(A, "test_x", _LADDER))
    empty = tmp_path / "blank.txt"
    empty.write_text("\n\n", encoding="utf-8")
    assert any("empty" in p for p in guard.r8_covered(junit, empty))
    assert guard.r8_covered(junit, tmp_path / "nothing.txt")


def test_r8_covered_reaches_the_cli(tmp_path: Path) -> None:
    junit = _junit(tmp_path, _case(A, "test_x", _LADDER), _case(B, "test_y", _CONSOLE))
    base = [sys.executable, str(_ROOT / "scripts/ci_unit_guard.py"), "r8-covered", "--junit", str(junit)]
    bad = subprocess.run([*base, "--list", str(_list(tmp_path, A))], capture_output=True, text=True)
    assert bad.returncode == 1 and "::error::" in bad.stdout and B in bad.stdout, bad.stdout
    good = subprocess.run([*base, "--list", str(_list(tmp_path, A, B))], capture_output=True, text=True)
    assert good.returncode == 0, good.stdout + good.stderr
    assert "R8 skips checked: 2 files" in good.stdout


# ── CIV-17: the shard count follows the selection ──────────────────────────


def test_shard_count_by_mode_and_size() -> None:
    many = [f"tests/unit/test_{i}.py" for i in range(200)]
    assert guard.shard_count("empty", []) == 0
    assert guard.shard_count("full", []) == guard.ALL_SHARDS
    assert guard.shard_count("selection", many[:1]) == 1
    assert guard.shard_count("selection", many[:40]) == 1
    assert guard.shard_count("selection", many[:41]) == 3
    assert guard.shard_count("selection", many[:120]) == 3
    assert guard.shard_count("selection", many[:121]) == guard.ALL_SHARDS
    assert guard.shard_list(0) == "[]"
    assert guard.shard_list(3) == "[1,2,3]"


def _gate(tmp_path: Path, data: object, ignored: str = "") -> dict[str, str]:
    f = tmp_path / "gate.json"
    f.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return dict(line.split("=", 1) for line in guard.gate_output(f, ignored))


def test_gate_output_names_the_shards(tmp_path: Path) -> None:
    a, b = "tests/unit/test_a.py", "tests/unit/test_b.py"
    assert _gate(tmp_path, {"full": False, "files": [a, b]})["shards"] == "[1]"
    # A docs-only PR: no test file, so no shard.
    assert _gate(tmp_path, {"full": False, "files": []})["shards"] == "[]"
    # Every selected file is a process fence a shard would drop: also none.
    ignored = f"--ignore={a} --ignore={b}"
    assert _gate(tmp_path, {"full": False, "files": [a, b]}, ignored)["shards"] == "[]"
    # A full result runs all six.
    assert _gate(tmp_path, {"full": True, "files": []})["shards"] == "[1,2,3,4,5,6]"


def test_gate_output_never_reads_a_failure_as_an_empty_selection(tmp_path: Path) -> None:
    """The #1077 trap. A broken or missing result is a FULL run, never `[]`."""
    for bad in ("not json", "{}", '{"full": false}', '{"full": false, "files": ["../evil.py"]}'):
        out = _gate(tmp_path, bad)
        assert out["full"] == "true" and out["shards"] == "[1,2,3,4,5,6]", bad
    missing = guard.gate_output(tmp_path / "absent.json")
    assert "full=true" in missing and "shards=[1,2,3,4,5,6]" in missing


def test_the_pick_step_and_the_changes_job_agree_on_the_count(monkeypatch) -> None:
    monkeypatch.setattr(guard, "file_seconds", lambda *a, **k: None)  # the tiers by file count
    for n in (1, 40, 41, 120, 121, 300):
        paths = [f"tests/unit/test_{i}.py" for i in range(n)]
        mode, picked = guard.select_shard("false", " ".join(paths), "", "success")
        assert mode == "selection"
        assert guard.shard_list(guard.shard_count(mode, picked)) == _gate_shards(paths)


def _gate_shards(paths: list[str]) -> str:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        return _gate(Path(d), {"full": False, "files": paths})["shards"]


def test_an_empty_selection_skips_the_shards_and_only_that_reads_as_a_pass() -> None:
    jobs = _jobs()
    # The matrix is never empty when it is read: the job is skipped first.
    assert "needs.changes.outputs.shards != '[]'" in jobs["test"]["if"]
    steps = {s.get("name"): s for s in jobs["unit-tests"]["steps"]}
    run = steps["Every shard passed"]["run"]
    # `skipped` passes ONLY with a successful changes job and an empty list.
    assert '[ "$TEST" = "skipped" ] && [ "$CHANGES" = "success" ] && [ "$SHARDS" = "[]" ]' in run
    assert '[ "$TEST" = "success" ] || exit 1' in run
    assert steps["Empty selection report"]["if"] == "needs.changes.outputs.shards == '[]'"


def test_no_job_skips_on_a_draft() -> None:
    """D-PM-21 and #1077. No browser test is skipped without the owner's word,
    and a skipped REQUIRED check on a draft's head SHA reads as passed. So no
    job reads the draft flag, and the PR trigger keeps its default types."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request.draft" not in text
    on = yaml.safe_load(text)[True]
    assert "types" not in (on["pull_request"] or {})


# ── CIV-17b: the shard count follows measured seconds ──────────────────────


def test_shard_count_by_seconds() -> None:
    sec = {"tests/unit/test_a.py": 300.0, "tests/unit/test_b.py": 301.0, "tests/unit/test_big.py": 5000.0}
    a, b, big = sec
    assert guard.shard_count("selection", [a], sec) == 1
    assert guard.shard_count("selection", [a, b], sec) == 2  # 601 s: one shard holds 600 s
    assert guard.shard_count("selection", [big], sec) == guard.ALL_SHARDS  # capped
    # a file with no timing counts DEFAULT_FILE_S (a new test)
    new = [f"tests/unit/test_new_{i}.py" for i in range(61)]
    assert guard.shard_count("selection", new, sec) == 2  # 610 s
    assert guard.shard_count("empty", [], sec) == 0
    assert guard.shard_count("full", [], sec) == guard.ALL_SHARDS


def test_without_timings_the_tiers_by_file_count_still_apply() -> None:
    few = [f"tests/unit/test_{i}.py" for i in range(41)]
    assert guard.shard_count("selection", few, None) == 3
    assert guard.shard_count("selection", few, {}) == 3


def test_file_seconds_reads_pytest_splits_format_and_fails_to_none(tmp_path: Path) -> None:
    f = tmp_path / ".test_durations"
    f.write_text(json.dumps({"tests/unit/test_a.py::t1": 1.5, "tests/unit/test_a.py::t2": 2.0,
                             "tests/unit/test_b.py::T::t": 3}), encoding="utf-8")
    assert guard.file_seconds(f) == {"tests/unit/test_a.py": 3.5, "tests/unit/test_b.py": 3.0}
    f.write_text("not json", encoding="utf-8")
    assert guard.file_seconds(f) is None
    assert guard.file_seconds(tmp_path / "missing") is None
    f.write_text("{}", encoding="utf-8")
    assert guard.file_seconds(f) is None


def test_gate_output_uses_the_timings_and_agrees_with_the_pick_step(tmp_path: Path, monkeypatch) -> None:
    durations = tmp_path / ".test_durations"
    durations.write_text(json.dumps({"tests/unit/test_a.py::t": 700.0, "tests/unit/test_b.py::t": 100.0}),
                         encoding="utf-8")
    monkeypatch.setattr(guard, "DURATIONS_PATH", durations)
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"full": False, "files": ["tests/unit/test_a.py", "tests/unit/test_b.py"]}),
                    encoding="utf-8")
    out = dict(x.split("=", 1) for x in guard.gate_output(gate))
    assert out["shards"] == "[1,2]"  # 800 s
    mode, paths = guard.select_shard("false", out["files"], "", "success")
    assert guard.shard_count(mode, paths, guard.file_seconds()) == 2
    # a docs-only PR still starts no shard, and a failed result still starts all six
    gate.write_text(json.dumps({"full": False, "files": []}), encoding="utf-8")
    assert dict(x.split("=", 1) for x in guard.gate_output(gate))["shards"] == "[]"
    assert dict(x.split("=", 1) for x in guard.gate_output(tmp_path / "absent.json"))["shards"] == "[1,2,3,4,5,6]"


def test_missing_timing_entries_are_named_in_a_warning() -> None:
    # CIV-33b: a selected file with no `.test_durations` entry is a visible gap.
    warns = guard.missing_timing_warnings(
        ["tests/unit/test_a.py", "tests/unit/test_new.py", "tests/unit/test_new2.py"],
        {"tests/unit/test_a.py": 3.0},
    )
    assert warns[0].startswith("::warning::") and "2 " in warns[0]
    assert "tests/unit/test_new.py" in warns[0] and "tests/unit/test_new2.py" in warns[0]
    assert "tests/unit/test_a.py" not in warns[0]
    assert guard.missing_timing_warnings(["tests/unit/test_a.py"], {"tests/unit/test_a.py": 1.0}) == []
    assert guard.missing_timing_warnings(["tests/unit/test_a.py"], None) == []


def test_select_prints_the_warning_and_still_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    durations = tmp_path / ".test_durations"
    durations.write_text(json.dumps({"tests/unit/test_a.py::t": 1.0}), encoding="utf-8")
    monkeypatch.setattr(guard, "DURATIONS_PATH", durations)
    rc = guard.main(["select", "--full", "false", "--files", "tests/unit/test_a.py tests/unit/test_b.py",
                     "--changes-result", "success"])
    assert rc == 0
    out = capsys.readouterr()
    assert "tests/unit/test_b.py" in out.err  # stderr: stdout is tee-d into GITHUB_OUTPUT
    assert "::warning::" not in out.out


# ── CIV-32 (D218): a repair round re-tests what failed and what the fix reaches ──

_SAFETY = ["tests/unit/test_safety_one.py", "tests/unit/test_safety_two.py"]
_NO_MOVE = {"full": False, "files": [], "reasons": []}


def _repair_case(file: str, name: str, outcome: str | None = None, reason: str = "x") -> str:
    inner = f'<{outcome} message="{reason}">boom</{outcome}>' if outcome else ""
    mod = file[: -len(".py")].replace("/", ".")
    return f'<testcase classname="{mod}" name="{name}" file="{file}" time="0.1">{inner}</testcase>'


def _write_report(d: Path, name: str, *cases: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f'<testsuites><testsuite name="pytest">{"".join(cases)}</testsuite></testsuites>',
        encoding="utf-8",
    )


def _shard(junit: Path, n: int, *cases: str) -> None:
    """The layout of `gh run download -p junit-shard-*`: one folder per artifact."""
    _write_report(junit / f"junit-shard-{n}", f"shard-{n}.xml", *cases)


def _job(n: int, conclusion: str = "success") -> dict:
    return {"name": f"Unit tests (shard {n})", "status": "completed", "conclusion": conclusion}


def _gate_result(files: list[str], full: bool = False) -> dict:
    return {"full": full, "files": sorted({*files, *_SAFETY})}


def test_repair_selects_the_failed_files_plus_the_reach_of_the_fix(tmp_path: Path) -> None:
    _shard(
        tmp_path,
        1,
        _repair_case("tests/unit/test_a.py", "t1", "failure"),
        _repair_case("tests/unit/test_b.py", "t1"),
        _repair_case("tests/unit/test_c.py", "t1", "error"),
    )
    failed, why = guard.old_failures([_job(1, "failure")], tmp_path)
    assert (failed, why) == (["tests/unit/test_a.py", "tests/unit/test_c.py"], "")
    out = guard.repair_decision(failed, [], True, _gate_result(["tests/unit/test_d.py"]), _NO_MOVE)
    assert out["mode"] == "repair"
    assert out["files"] == sorted(
        ["tests/unit/test_a.py", "tests/unit/test_c.py", "tests/unit/test_d.py", *_SAFETY]
    )
    assert "failed in the old run" in out["reason"]
    assert "test_b.py" not in " ".join(out["files"])  # a passing file of the old run stays out


def test_the_reach_of_mains_move_joins_the_selection() -> None:
    main = {"full": False, "files": ["tests/unit/test_main.py", *_SAFETY], "reasons": []}
    out = guard.repair_decision([], [], True, _gate_result([]), main)
    assert out["mode"] == "repair" and "tests/unit/test_main.py" in out["files"]
    # main's move selects the full suite, or nobody knows it: the normal selection.
    whole = guard.repair_decision([], [], True, _gate_result([]), {"full": True, "files": []})
    assert whole["mode"] == "full" and "main" in whole["reason"]
    unknown = guard.repair_decision([], [], True, _gate_result([]), None)
    assert unknown["mode"] == "full" and "main" in unknown["reason"]


def test_the_decision_takes_no_always_list() -> None:
    """P2: the must-run and SAFETY lists reach a repair selection as they reach
    any selection: the gate adds SAFETY, and `check --only-selected` reads it."""
    import inspect

    assert "always" not in inspect.signature(guard.repair_decision).parameters


def test_a_merge_of_main_in_the_delta_gives_the_normal_selection() -> None:
    out = guard.repair_decision(["tests/unit/test_a.py"], ["abc123"], True, _gate_result([]), _NO_MOVE)
    assert out["mode"] == "full" and out["files"] == []
    assert "merge" in out["reason"]


def test_a_missing_or_broken_old_report_gives_the_normal_selection(tmp_path: Path) -> None:
    assert guard.old_failures([_job(1)], tmp_path / "absent")[0] is None
    _write_report(tmp_path / "junit-shard-1", "shard-1.xml")
    (tmp_path / "junit-shard-1" / "shard-1.xml").write_text("<testsuites><oops", encoding="utf-8")
    failed, why = guard.old_failures([_job(1)], tmp_path)
    assert failed is None and "parse" in why
    assert guard.repair_decision(None, [], True, _gate_result([]), _NO_MOVE)["mode"] == "full"


def test_a_shard_that_ran_no_test_is_not_trusted(tmp_path: Path) -> None:
    """P0-2. A shard can fail before pytest (uv sync, the DB check). Then the
    empty-report step writes a zero-test report, and the job reads `failure`."""
    _shard(tmp_path, 1, _repair_case("tests/unit/test_a.py", "t"))
    _shard(tmp_path, 2)  # what `empty-report` writes
    for conclusion in ("failure", "success"):
        failed, why = guard.old_failures([_job(1), _job(2, conclusion)], tmp_path)
        assert failed is None and "shard 2" in why and "no test" in why


def test_a_failed_shard_with_no_failed_case_is_not_trusted(tmp_path: Path) -> None:
    """P0-2. The job failed, but its report says every test passed: pytest
    crashed after it wrote part of the report, or a later step failed."""
    _shard(tmp_path, 1, _repair_case("tests/unit/test_a.py", "t"))
    failed, why = guard.old_failures([_job(1, "failure")], tmp_path)
    assert failed is None and "shard 1 failed" in why


def test_a_collection_error_in_the_old_run_is_not_a_baseline(tmp_path: Path) -> None:
    """Opus re-review (2026-11-12): an ImportError in test_b stops each shard at
    collection, so test_a and test_c never ran. pytest 9.1.1 with xunit1 writes
    ONE case: an empty classname, the module as the name, the file, and
    `<error message="collection failure">`. Trusting it would re-test only B."""
    junit = tmp_path / "junit-shard-1"
    junit.mkdir(parents=True)
    (junit / "shard-1.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
        'errors="1" failures="0" skipped="0" tests="1"><testcase classname="" '
        'name="tests.unit.test_b" file="tests/unit/test_b.py">'
        '<error message="collection failure">ImportError: No module named nope_missing'
        "</error></testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    failed, why = guard.old_failures([_job(1, "failure")], tmp_path)
    assert failed is None and "collection error" in why and "test_b.py" in why


def test_an_interrupt_after_a_real_failure_is_not_a_baseline(tmp_path: Path) -> None:
    """Opus re-review P2 (2026-11-12): test_a fails, then test_b raises
    KeyboardInterrupt, so test_c never runs. pytest 9.1.1 with xunit1 writes the
    failure of test_a and an empty case with no name. Trusting the report
    would re-test only test_a."""
    junit = tmp_path / "junit-shard-1"
    junit.mkdir(parents=True)
    (junit / "shard-1.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
        'errors="0" failures="1" skipped="0" tests="2"><testcase classname="tests.unit.test_a" '
        'name="test_x" file="tests/unit/test_a.py"><failure message="assert False">E</failure>'
        '</testcase><testcase time="0.000" /></testsuite></testsuites>',
        encoding="utf-8",
    )
    failed, why = guard.old_failures([_job(1, "failure")], tmp_path)
    assert failed is None and "interrupt" in why


def test_each_shard_job_is_paired_with_its_own_report(tmp_path: Path) -> None:
    ok = _repair_case("tests/unit/test_a.py", "t")
    _shard(tmp_path, 1, ok)
    assert guard.old_failures([_job(1)], tmp_path) == ([], "")
    # Shard 2 left no report. The count of reports no longer hides it.
    assert "shard 2 left no report" in guard.old_failures([_job(1), _job(2)], tmp_path)[1]
    # A report that no shard job owns.
    _shard(tmp_path, 3, ok)
    assert "no shard job" in guard.old_failures([_job(1)], tmp_path)[1]
    # A shard with no verdict, a shard with no number, no shard at all.
    assert guard.old_failures([_job(1, "cancelled")], tmp_path)[0] is None
    skipped = {"name": "Unit tests (shard ${{ matrix.shard }})", "conclusion": "skipped"}
    assert guard.old_failures([skipped], tmp_path)[0] is None
    assert guard.old_failures([{"name": "Unit tests", "conclusion": "success"}], tmp_path)[0] is None


def test_a_disarmed_r8_skip_counts_as_not_passed(tmp_path: Path) -> None:
    """A shard failure stops the old guard before it reads skips. So a file that
    skipped for a disarmed R8 reason was never judged, and it runs again."""
    _shard(
        tmp_path,
        1,
        _repair_case("tests/unit/test_a.py", "t", "failure"),
        _repair_case("tests/unit/test_r8.py", "t", "skipped", "TENANT_LADDER_DATABASE_URL unset"),
        _repair_case("tests/unit/test_win.py", "t", "skipped", "windows only"),
    )
    failed, _ = guard.old_failures([_job(1, "failure")], tmp_path)
    assert failed == ["tests/unit/test_a.py", "tests/unit/test_r8.py"]


def test_a_collection_error_with_no_file_gives_the_normal_selection(tmp_path: Path) -> None:
    _shard(tmp_path, 1, '<testcase classname="" name="x"><error/></testcase>')
    assert guard.old_failures([_job(1, "failure")], tmp_path)[0] is None


def test_a_force_push_gives_the_normal_selection() -> None:
    out = guard.repair_decision(["tests/unit/test_a.py"], None, False, None, _NO_MOVE)
    assert out["mode"] == "full" and "force-push" in out["reason"]


def test_a_delta_that_selects_the_full_suite_gives_the_normal_selection() -> None:
    whole = {"full": True, "files": [], "reasons": ["conftest"]}
    out = guard.repair_decision([], [], True, whole, _NO_MOVE)
    assert out["mode"] == "full" and out["files"] == []


def test_a_delta_with_no_answer_gives_the_normal_selection() -> None:
    assert guard.repair_decision([], [], True, None, _NO_MOVE)["mode"] == "full"
    assert guard.repair_decision([], [], True, {"files": []}, _NO_MOVE)["mode"] == "full"


def test_no_failure_and_no_reach_keeps_the_minimum_set_and_never_goes_empty() -> None:
    out = guard.repair_decision([], [], True, _gate_result([]), _NO_MOVE)
    assert out["mode"] == "repair"
    assert out["files"] == sorted(_SAFETY)
    # Even a gate with no file at all must not hand pytest an empty list.
    bare = guard.repair_decision([], [], True, {"full": False, "files": []}, _NO_MOVE)
    assert bare["mode"] == "full" and bare["files"] == []


def test_a_failed_file_that_the_fix_deleted_leaves_the_selection() -> None:
    gone = {"tests/unit/test_gone.py"}
    out = guard.repair_decision(
        ["tests/unit/test_gone.py"], [], True, _gate_result([]), _NO_MOVE,
        exists=lambda p: p not in gone,
    )  # fmt: skip
    assert "tests/unit/test_gone.py" not in out["files"]


def test_a_path_outside_the_safe_shape_never_reaches_pytest() -> None:
    out = guard.repair_decision(["-evil.py"], [], True, _gate_result([]), _NO_MOVE)
    assert "-evil.py" not in out["files"]


def test_a_repair_selection_goes_through_the_same_shard_count_as_any_other(tmp_path: Path) -> None:
    out = guard.repair_decision(["tests/unit/test_a.py"], [], True, _gate_result([]), _NO_MOVE)
    gate_json = tmp_path / "gate.json"
    gate_json.write_text(json.dumps({"full": False, "files": out["files"]}), encoding="utf-8")
    lines = guard.gate_output(gate_json)
    assert lines[0] == "full=false" and lines[2] == "shards=[1]"


def _run(**over) -> dict:
    run = {
        "id": 10,
        "status": "completed",
        "conclusion": "failure",
        "head_sha": "old",
        "created_at": "2026-11-13T10:00:00Z",
        "head_repository": {"full_name": "o/r"},
        "pull_requests": [{"number": 7}],
    }
    return {**run, **over}


def test_the_old_run_is_the_newest_finished_run_of_this_pr_for_an_earlier_head() -> None:
    runs = [
        _run(id=1, created_at="2026-11-13T09:00:00Z", head_sha="older"),
        _run(id=2),
        _run(id=3, created_at="2026-11-13T11:00:00Z", conclusion="cancelled"),
        _run(id=4, created_at="2026-11-13T12:00:00Z", status="in_progress", conclusion=None),
        _run(id=5, created_at="2026-11-13T12:30:00Z", head_sha="new"),
        _run(id=6, created_at="2026-11-13T12:40:00Z", head_repository={"full_name": "fork/r"}),
        _run(id=7, created_at="2026-11-13T12:50:00Z", pull_requests=[{"number": 8}]),
    ]
    got = guard.find_old_run(runs, "o/r", 7, "new")
    assert got is not None and got["id"] == 2
    assert guard.find_old_run([], "o/r", 7, "new") is None


def _git_repo(tmp_path: Path):
    def git(*a: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q", "-b", "main")
    return git


def _commit(root: Path, git, files: dict[str, str], msg: str) -> str:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        git("add", rel)
    git("commit", "-q", "-m", msg)
    return git("rev-parse", "HEAD")


def test_git_delta_reads_descent_merges_and_changed_files(tmp_path: Path, monkeypatch) -> None:
    git = _git_repo(tmp_path)

    def commit(name: str) -> str:
        return _commit(tmp_path, git, {name: name}, name)

    base = commit("base.txt")
    git("switch", "-q", "-c", "pr")
    old = commit("a.txt")
    new = commit("b.txt")
    monkeypatch.chdir(tmp_path)
    assert guard.git_delta(old, new) == (True, [], ["b.txt"])
    # A merge of main: main moves on, and the PR merges it.
    git("switch", "-q", "main")
    commit("m.txt")
    git("switch", "-q", "pr")
    git("merge", "-q", "--no-ff", "-m", "merge main", "main")
    merged = git("rev-parse", "HEAD")
    descends, merges, _ = guard.git_delta(new, merged)
    assert descends and merges == [merged]
    # gate-meta reads the two parents of the checked-out merge, first parent
    # first. GitHub's pull/N/merge has main first and the PR head second.
    assert guard.gate_meta() == {"base": new, "head": git("rev-parse", "main")}
    # A force-push: the new head does not contain the old head.
    git("switch", "-q", "-c", "rewrite", base)
    other = commit("c.txt")
    assert guard.git_delta(old, other) == (False, None, None)
    assert guard.git_delta("0" * 40, other)[0] is False
    assert guard.gate_meta() is None  # not a merge commit
    assert guard.have_commit(other) and not guard.have_commit("f" * 40)


def test_read_meta_takes_only_two_full_shas(tmp_path: Path) -> None:
    p = tmp_path / "gate-meta.json"
    sha = "a" * 40
    p.write_text(json.dumps({"base": sha, "head": sha}), encoding="utf-8")
    assert guard.read_meta(p) == {"base": sha, "head": sha}
    for bad in ({"base": sha}, {"base": "main", "head": sha}, [sha, sha]):
        p.write_text(json.dumps(bad), encoding="utf-8")
        assert guard.read_meta(p) is None
    assert guard.read_meta(tmp_path / "absent.json") is None


class _FakeGh:
    """GitHub, as `gh` sees it. An `api` call with no `-X GET` and a `-f`
    field is a POST, and GitHub answers a POST to a read endpoint with 404."""

    def __init__(self, real, runs: list[dict], jobs: list[dict], meta: dict, shards: dict) -> None:
        self.real, self.runs, self.jobs, self.meta, self.shards = real, runs, jobs, meta, shards
        self.calls: list[list[str]] = []

    def __call__(self, argv, *a, **k):
        if argv[0] != "gh":
            return self.real(argv, *a, **k)
        self.calls.append(list(argv))
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if argv[1] == "api":
            is_get = "-X" in argv and argv[argv.index("-X") + 1] == "GET"
            if "-f" in argv and not is_get:
                return subprocess.CompletedProcess(argv, 1, '{"message": "Not Found"}', "HTTP 404")
            path = next(x for x in argv[2:] if x.startswith("repos/"))
            if path.endswith("/runs"):
                return ok(json.dumps({"workflow_runs": self.runs}))
            if path.endswith("/jobs"):
                return ok(json.dumps({"jobs": self.jobs}))
        if argv[1:3] == ["run", "download"]:
            pattern, dest = argv[argv.index("-p") + 1], Path(argv[argv.index("-D") + 1])
            if pattern == "gate-meta" and getattr(self, "no_meta", False):
                return subprocess.CompletedProcess(argv, 1, "", "no artifact matches")
            if pattern == "gate-meta":
                (dest / "gate-meta").mkdir(parents=True)
                (dest / "gate-meta" / "gate-meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
            else:
                for n, cases in self.shards.items():
                    _shard(dest, n, *cases)
            return ok()
        return subprocess.CompletedProcess(argv, 1, "", "unknown gh call")


def _pr_tree(tmp_path: Path):
    """A repo where main moves under an open PR. Returns the git helper and the SHAs."""
    root = tmp_path / "repo"
    root.mkdir()
    git = _git_repo(root)
    base0 = _commit(
        root, git,
        {
            "packages/pkg/pkg/__init__.py": "",
            "packages/pkg/pkg/leaf.py": "X = 1\n",
            "packages/pkg/pkg/other.py": "Y = 1\n",
            "tests/fences.json": '{"python": {"safety": ["tests/unit/test_safe.py"], "process": []}}',
            "tests/unit/test_safe.py": "import os\n",
            "tests/unit/test_other.py": "from pkg.other import Y\n",
        },
        "main 0",
    )  # fmt: skip
    git("switch", "-q", "-c", "pr")
    old_head = _commit(root, git, {"tests/unit/test_pr.py": "from pkg.leaf import X\n"}, "pr 1")
    new_head = _commit(root, git, {"docs/fix.md": "a fix\n"}, "pr 2: the fix")
    return root, git, base0, old_head, new_head


def _drive(tmp_path: Path, monkeypatch, root: Path, git, base0: str, old_head: str, new_head: str):
    """Check out the new merge, as pull_request does, and run the repair step."""
    git("switch", "-q", "--detach", "main")
    git("merge", "-q", "--no-ff", "-m", "merge", new_head)
    monkeypatch.chdir(root)
    meta = root.parent / "gate-meta.json"
    meta.write_text(json.dumps(guard.gate_meta()), encoding="utf-8")
    gate_json = root.parent / "gate.json"
    gate_json.write_text('{"full": true, "files": []}', encoding="utf-8")
    passing = [_repair_case(t, "t") for t in ("tests/unit/test_pr.py", "tests/unit/test_safe.py")]
    fake = _FakeGh(
        subprocess.run,
        runs=[_run(id=99, head_sha=old_head)],
        jobs=[_job(1)],
        meta={"base": base0, "head": old_head},
        shards={1: passing},
    )
    monkeypatch.setattr(guard.subprocess, "run", fake)
    line = guard.repair("o/r", 7, new_head, gate_json, root.parent / "work", root, meta)
    monkeypatch.undo()
    return line, json.loads(gate_json.read_text(encoding="utf-8")), fake


def test_a_move_of_main_selects_the_pr_tests_that_import_what_main_changed(
    tmp_path: Path, monkeypatch
) -> None:
    """P1. The old run tested the PR on main 0. main then changed pkg.leaf,
    which the PR's own test imports. The fix touches only a doc, so the PR's
    delta reaches nothing. main's move still selects the test."""
    root, git, base0, old_head, new_head = _pr_tree(tmp_path)
    git("switch", "-q", "main")
    _commit(root, git, {"packages/pkg/pkg/leaf.py": "X = 2\n"}, "main 1: leaf moves")
    line, gate, fake = _drive(tmp_path, monkeypatch, root, git, base0, old_head, new_head)
    assert line.startswith("repair mode:"), line
    assert gate["full"] is False
    assert "tests/unit/test_pr.py" in gate["files"]
    assert "tests/unit/test_safe.py" in gate["files"]  # SAFETY, from the gate
    assert "tests/unit/test_other.py" not in gate["files"]
    # P0-1: every read of the API names GET.
    api = [c for c in fake.calls if c[1] == "api"]
    assert len(api) == 2 and all(c[2:4] == ["-X", "GET"] for c in api), api


def test_with_main_still_the_pr_test_stays_out(tmp_path: Path, monkeypatch) -> None:
    root, git, base0, old_head, new_head = _pr_tree(tmp_path)
    line, gate, _ = _drive(tmp_path, monkeypatch, root, git, base0, old_head, new_head)
    assert line.startswith("repair mode:"), line
    assert gate["files"] == ["tests/unit/test_safe.py"]


def test_a_move_of_main_that_needs_the_full_suite_gives_the_normal_selection(
    tmp_path: Path, monkeypatch
) -> None:
    root, git, base0, old_head, new_head = _pr_tree(tmp_path)
    git("switch", "-q", "main")
    _commit(root, git, {"tests/conftest.py": "\n"}, "main 1: a conftest")
    line, gate, _ = _drive(tmp_path, monkeypatch, root, git, base0, old_head, new_head)
    assert line.startswith("full selection:") and "main" in line, line
    assert gate == {"full": True, "files": []}


def test_a_run_with_no_gate_meta_gives_the_normal_selection(tmp_path: Path, monkeypatch) -> None:
    """An old run from before CIV-32, or a run whose meta step failed."""
    root, _git, base0, old_head, new_head = _pr_tree(tmp_path)
    runs = {"workflow_runs": [_run(id=99, head_sha=old_head)]}
    monkeypatch.setattr(guard, "_gh_json", lambda *a: runs)
    gate_json = tmp_path / "gate.json"
    gate_json.write_text('{"full": true, "files": []}', encoding="utf-8")
    # This run wrote no meta.
    line = guard.repair("o/r", 7, new_head, gate_json, tmp_path / "w", root, tmp_path / "absent.json")
    assert line.startswith("full selection:") and "did not record" in line, line
    # The old run kept no gate-meta artifact: the download fails.
    meta = tmp_path / "gate-meta.json"
    meta.write_text(json.dumps({"base": base0, "head": new_head}), encoding="utf-8")
    fake = _FakeGh(subprocess.run, runs=runs["workflow_runs"], jobs=[], meta={}, shards={})
    fake.no_meta = True
    monkeypatch.setattr(guard.subprocess, "run", fake)
    line = guard.repair("o/r", 7, new_head, gate_json, tmp_path / "w", root, meta)
    monkeypatch.undo()
    assert line.startswith("full selection:") and "gate-meta" in line, line
    assert json.loads(gate_json.read_text(encoding="utf-8")) == {"full": True, "files": []}


def test_the_repair_step_fails_open_to_the_normal_selection(tmp_path: Path, monkeypatch) -> None:
    gate_json = tmp_path / "gate.json"
    gate_json.write_text('{"full": false, "files": ["tests/unit/test_a.py"]}', encoding="utf-8")
    meta = tmp_path / "gate-meta.json"
    monkeypatch.setattr(guard, "_gh_json", lambda *a: None)
    assert guard.repair("o/r", 7, "new", gate_json, tmp_path, tmp_path, meta).startswith("full selection:")

    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(guard, "_gh_json", boom)
    assert guard.repair("o/r", 7, "new", gate_json, tmp_path, tmp_path, meta).startswith("full selection:")
    assert json.loads(gate_json.read_text(encoding="utf-8"))["files"] == ["tests/unit/test_a.py"]


def test_the_changes_job_runs_the_repair_step_only_on_a_synchronize_push() -> None:
    job = _jobs()["changes"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    gate = next(s for s in job["steps"] if s.get("id") == "gate")
    run = gate["run"]
    assert "ci_unit_guard.py repair" in run
    assert "synchronize" in run and "pull_request" in run
    assert "GITHUB_STEP_SUMMARY" in run
    # The meta comes first, the repair runs AFTER the normal gate wrote
    # gate.json, and gate-output reads the result.
    order = ("ci_unit_guard.py gate-meta", "--gate --base", "ci_unit_guard.py repair", "gate-output")
    assert [run.index(s) for s in order] == sorted(run.index(s) for s in order)
    assert "--meta gate-meta.json" in run
    assert gate["env"]["GH_TOKEN"] == "${{ github.token }}"
    # The head SHA of the PR, not the synthetic merge commit that checkout builds.
    assert gate["env"]["NEW_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert "uv " not in run


def test_the_changes_job_keeps_the_tested_merge_for_the_next_push() -> None:
    steps = _jobs()["changes"]["steps"]
    ids = [s.get("id") for s in steps]
    keep = next(i for i, s in enumerate(steps) if "upload-artifact" in str(s.get("uses", "")))
    assert ids.index("gate") < keep < ids.index("diff") == len(steps) - 1
    up = steps[keep]
    assert up["with"]["name"] == guard.GATE_META_ARTIFACT == "gate-meta"
    assert up["with"]["path"] == guard.GATE_META
    assert up["with"]["if-no-files-found"] == "ignore"
    assert up["if"] == "${{ !cancelled() }}"
    # The merge ref needs both parents and main's history.
    assert steps[0]["with"]["fetch-depth"] == 0


def test_a_selection_passes_tests_unit_once_with_the_plugin() -> None:
    """CIV-33a: pytest 9.1 re-lists tests/unit for each path argument, so a
    selection run hands pytest `tests/unit/` once and the plugin drops the rest."""
    steps = _jobs()["test"]["steps"]
    run = next(s["run"] for s in steps if s.get("name") == "Run unit tests (this shard)")
    assert "-p pytest_only_selected" in run
    assert "SHARD_SELECTED_FILES=" in run
    assert 'PLUGIN="-p pytest_only_selected"' in run
    # The selection branch resets the targets to the one directory.
    assert run.count('SHARD_TARGETS="tests/unit/"') == 2
    assert "python -m pytest $SHARD_TARGETS $PLUGIN " in run
    assert (_ROOT / "scripts/pytest_only_selected.py").is_file()


def test_a_selection_with_no_case_at_all_fails(tmp_path: Path) -> None:
    """CIV-33a (Opus review of PR 2192): with one tests/unit argument and the
    plugin, a broken path match collects nothing in every shard, and each shard
    exits 5 as "drew no test". The guard must turn that red."""
    out = tmp_path / "junit" / "shard-1.xml"
    assert guard.empty_report(out) is True
    problems = guard.check(out.parent, [], [], only_selected=["tests/unit/test_x.py"])
    assert any("no shard reported a single test case" in p for p in problems), problems
    # An empty selection, and a selection of process fences only, stay green.
    assert guard.check(out.parent, [], [], only_selected=[]) == []
    fence = sorted(guard._process_fences())
    if fence:
        assert guard.check(out.parent, [], [], only_selected=[fence[0]]) == []
    # A selection that reported cases is not touched by this rule.
    (tmp_path / "ok").mkdir()
    junit = _report(tmp_path / "ok")
    assert not any(
        "no shard reported" in p
        for p in guard.check(junit, [], [], only_selected=["tests/unit/test_x.py"])
    )
