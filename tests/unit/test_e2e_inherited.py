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
