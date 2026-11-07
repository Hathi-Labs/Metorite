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
    for reason in guard.DISARMED:
        assert f'--disarmed "{reason}"' in run


def test_every_shard_runs_and_writes_a_report() -> None:
    test = _jobs()["test"]
    run = "\n".join(s.get("run", "") for s in test["steps"])
    # CIV-17: the matrix and the split count both follow the selection.
    assert test["strategy"]["matrix"]["shard"] == (
        "${{ fromJSON(needs.changes.outputs.shards || '[1,2,3,4,5,6]') }}"
    )
    assert "--splits ${{ steps.pick.outputs.splits }}" in run
    assert "--group ${{ matrix.shard }}" in run
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
    # The literal list stays in the YAML: 40+ suites assert their own name there.
    assert "--must-run" in step["run"] and "tests/unit/test_auto_park.py" in step["run"]


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


def test_the_pick_step_and_the_changes_job_agree_on_the_count() -> None:
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
