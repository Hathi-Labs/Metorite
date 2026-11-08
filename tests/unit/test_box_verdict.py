"""CIV-22 (task 2288): the verdict of a local CI run on the home box.

R7 — the rule names its fence. A box run may count a failed unit test as
"expected" only when the test is in scripts/ci_pc/box_known_env.txt, and
GitHub's pr-check must never read that list. Each test below breaks when the
rule breaks.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci_pc"))
import box_verdict as bv  # noqa: E402

KNOWN = ROOT / "scripts" / "ci_pc" / "box_known_env.txt"
RUN_SUITE = (ROOT / "scripts" / "ci_pc" / "run_suite.sh").read_text(encoding="utf-8")
SCRIPT = ROOT / "scripts" / "ci_pc" / "box_verdict.py"

CONFINE = (
    "tests/unit/test_web_reader_confine.py::TestTheConfinement::test_each_attack_works_unconfined"
)


def _report(
    tmp_path: Path, failed: list[tuple[str, str, str]], passed: int = 3, msg: str = "x"
) -> Path:
    """A xunit1 report: failed = (file, classname, name). msg is the failure message."""
    cases = "".join(
        f'<testcase classname="{c}" name="{n}" file="{f}"><failure message="{msg}"/></testcase>'
        for f, c, n in failed
    )
    cases += "".join(
        f'<testcase classname="tests.unit.test_ok" name="t{i}" file="tests/unit/test_ok.py"/>'
        for i in range(passed)
    )
    p = tmp_path / "shard-1.xml"
    p.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return p


CONFINE_CASE = (
    "tests/unit/test_web_reader_confine.py",
    "tests.unit.test_web_reader_confine.TestTheConfinement",
    "test_each_attack_works_unconfined",
)
OTHER_CASE = ("tests/unit/test_other.py", "tests.unit.test_other", "test_real_bug")


def _shard(tmp_path, report, known=KNOWN):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "shard", str(known), str(report)],
        capture_output=True,
        text=True,
    )


def test_the_list_parses_and_every_entry_has_a_reason_and_an_open_task():
    known = bv.load_known(KNOWN)
    assert known, "the list is empty: delete the feature, or fill the list"
    assert len(known) <= 10, "the list must stay short (task 2288)"
    for test_id, (reason, task, text) in known.items():
        assert len(text) >= 8, (
            f"{test_id}: the text of the failure message is too short to mean anything"
        )
        assert re.match(r"tests/unit/test_\w+\.py::", test_id), test_id
        assert len(reason) >= 30 and "flaky" not in reason.lower(), (test_id, reason)
        assert task.isdigit()


def test_every_entry_names_a_test_that_exists():
    for test_id in bv.load_known(KNOWN):
        path, _, rest = test_id.partition("::")
        text = (ROOT / path).read_text(encoding="utf-8")
        assert f"def {rest.rsplit('::', 1)[-1].split('[')[0]}(" in text, (
            f"{test_id} no longer exists: drop the entry"
        )


def test_a_bad_line_is_an_error(tmp_path):
    bad = tmp_path / "k.txt"
    bad.write_text("tests/unit/test_a.py::test_b\tno task here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bv.load_known(bad)


BOX_MSG = "assert &apos;mode2&apos; == &apos;mode0&apos;"


def test_a_shard_with_only_listed_failures_is_expected(tmp_path):
    r = _shard(tmp_path, _report(tmp_path, [CONFINE_CASE], msg=BOX_MSG))
    assert r.returncode == 0 and r.stdout.strip() == CONFINE


def test_a_listed_test_with_another_message_is_a_real_failure(tmp_path):
    # An earlier assertion of the control (the secret file, a fork) breaks: same
    # test id, another message. That is a regression, and the shard stays red.
    r = _shard(
        tmp_path,
        _report(
            tmp_path, [CONFINE_CASE], msg="assert got[&apos;fork&apos;] == &apos;allowed&apos;"
        ),
    )
    assert r.returncode == 1 and CONFINE in r.stdout


def test_one_failure_outside_the_list_keeps_the_shard_red(tmp_path):
    r = _shard(tmp_path, _report(tmp_path, [CONFINE_CASE, OTHER_CASE], msg=BOX_MSG))
    assert r.returncode == 1 and "tests/unit/test_other.py::test_real_bug" in r.stdout
    assert CONFINE not in r.stdout


def test_a_failed_shard_with_no_failed_test_is_not_expected(tmp_path):
    # A crash, a timeout or a lost report leaves no failed test to excuse.
    r = _shard(tmp_path, _report(tmp_path, []))
    assert r.returncode == 1
    r = _shard(tmp_path, tmp_path / "missing.xml")
    assert r.returncode == 1


def test_the_verdict_line_names_the_commit_the_mode_and_the_count():
    assert (
        bv.verdict_line("GREEN", "a" * 40, "selection", 2, [])
        == f"GREEN {'a' * 40} selection expected=2"
    )
    assert bv.verdict_line("RED", "b" * 40, "full", 0, ["shard-2", "lint"]).endswith(
        "expected=0 unexpected=shard-2,lint"
    )


def test_run_suite_counts_a_listed_failure_and_writes_the_verdict():
    assert "scripts/ci_pc/box_known_env.txt" in RUN_SUITE
    assert 'box_verdict.py shard "$KNOWN_ENV"' in RUN_SUITE
    # Only pytest exit code 1 can be excused (2 to 5 are not a failed test).
    assert '"$RUN/shard-$i.rc")" = 1 ]' in RUN_SUITE
    # A shard with a state file does not turn the unit guard red.
    assert re.search(r'\[ -f "\$RUN/shard-\$i\.state" \] \|\| shards_ok=failure', RUN_SUITE)
    assert 'box_verdict.py line "$verdict"' in RUN_SUITE and '"$RUN/verdict"' in RUN_SUITE
    # RED is a failure of the script: only an unexpected FAIL sets `fail`.
    summary = RUN_SUITE.split("ADVISORY=", 1)[1]
    assert summary.index("res=expected") < summary.index("else res=FAIL; fail=1")
    assert RUN_SUITE.count("fail=1") == 1


def test_github_ci_never_reads_the_list():
    for path in (ROOT / ".github").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "box_known_env" not in text and "box_verdict" not in text, path
    for name in ("affected_tests.py", "ci_unit_guard.py", "fences.py", "premerge.sh"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "box_known_env" not in text, name
