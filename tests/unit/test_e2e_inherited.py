"""CIV-29a (D218): the inherited-failure compare for the browser suite."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "e2e_inherited.py"


def report(*failed: str, passed: tuple[str, ...] = ("ok one",)) -> dict:
    specs = [{"title": t, "file": "a.spec.ts", "ok": False} for t in failed]
    specs += [{"title": t, "file": "a.spec.ts", "ok": True} for t in passed]
    return {"suites": [{"title": "a.spec.ts", "specs": [], "suites": [{"title": "grp", "specs": specs}]}]}


def run(tmp_path, pr, main=None, code=0):
    p = tmp_path / "pr.json"
    p.write_text(json.dumps(pr), encoding="utf-8")
    cmd = [sys.executable, str(SCRIPT), "--pr", str(p), "--exit-code", str(code)]
    if main is not None:
        m = tmp_path / "main.json"
        m.write_text(json.dumps(main), encoding="utf-8")
        cmd += ["--main", str(m)]
    else:
        cmd += ["--main", str(tmp_path / "absent.json")]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def test_all_failures_inherited_passes(tmp_path):
    r = run(tmp_path, report("x", "y"), report("x", "y", "z"), code=1)
    assert r.returncode == 0, r.stdout
    assert "inherited" in r.stdout


def test_inherited_failures_do_not_hide_a_run_error(tmp_path):
    """Every failed test is inherited, but the run also crashed: the job fails."""
    pr = report("x")
    pr["errors"] = [{"message": "Error: worker process exited unexpectedly"}]
    r = run(tmp_path, pr, report("x"), code=1)
    assert r.returncode == 1, r.stdout
    assert "run error" in r.stdout


def test_main_reports_of_all_shards_are_joined(tmp_path):
    """A spec that main fails in another shard is still inherited."""
    p = tmp_path / "pr.json"
    p.write_text(json.dumps(report("x", "y")), encoding="utf-8")
    m1, m2 = tmp_path / "m1.json", tmp_path / "m2.json"
    m1.write_text(json.dumps(report("x")), encoding="utf-8")
    m2.write_text(json.dumps(report("y")), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--pr", str(p), "--main", str(m1),
                        "--main", str(m2), "--main", str(tmp_path / "absent.json"),
                        "--exit-code", "1"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout


def test_only_a_trusted_nightly_is_read():
    """The run lookup must filter to schedule or dispatch events (a fork PR
    from its own `main` can plant a nightly result, Opus review 2026-11-12)."""
    wf = (ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    step = wf[wf.index("Compare with main's newest nightly"):wf.index("Keep the report when it fails")]
    assert '.event == "schedule"' in step
    assert "databaseId,conclusion,event" in step


def test_one_new_failure_fails(tmp_path):
    r = run(tmp_path, report("x", "new"), report("x"), code=1)
    assert r.returncode == 1
    assert "new: a.spec.ts :: a.spec.ts > grp > new" in r.stdout


def test_missing_main_result_fails_with_a_clear_line(tmp_path):
    r = run(tmp_path, report("x"), None, code=1)
    assert r.returncode == 1
    assert "could not be read" in r.stdout


def test_no_failures_passes(tmp_path):
    assert run(tmp_path, report(), report("x"), code=0).returncode == 0


def test_nonzero_exit_without_a_failed_test_fails(tmp_path):
    assert run(tmp_path, report(), report(), code=1).returncode == 1


def test_unreadable_pr_report_fails_when_suite_failed(tmp_path):
    (tmp_path / "pr.json").write_text("{", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--pr", str(tmp_path / "pr.json"), "--exit-code", "1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert r.returncode == 1
