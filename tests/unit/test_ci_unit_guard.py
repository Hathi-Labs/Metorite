"""Fence for scripts/ci_unit_guard.py and the sharded unit job that calls it.

The reports come from a REAL pytest run, not a hand-written XML sample, so a
change in how pytest writes a skip reason breaks this test, not the gate.
"""

from __future__ import annotations

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
    shards = test["strategy"]["matrix"]["shard"]
    run = "\n".join(s.get("run", "") for s in test["steps"])
    assert f"--splits {len(shards)}" in run
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
    args = base + ["--disarmed", "NEVER_MATCHES unset"]
    full = subprocess.run(args + ["--must-run", "test_missing.py"], capture_output=True, text=True)
    assert full.returncode == 1 and "no shard ran test_missing.py" in full.stdout
    sel = subprocess.run(
        args + ["--only-selected", "test_sample.py", "--must-run", "test_missing.py"],
        capture_output=True,
        text=True,
    )
    assert sel.returncode == 0, sel.stdout
    empty = subprocess.run(
        args + ["--only-selected", "--must-run", "test_missing.py"], capture_output=True, text=True
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
        return guard.gate_output(f)

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
    assert test["if"].replace("${{", "").replace("}}", "").strip() == "!cancelled()"
    assert "always()" not in test["if"]


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
    assert "-eq 5" in run["run"] and '"$MODE" = "selection"' in run["run"]
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
