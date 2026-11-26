"""The merge checks a branch's migration numbers against main and the box ledger (task 2813).

``scripts/migration_ledger_check.py`` reads ``~/lanes/migration-numbers.tsv``
(written only by ``reserve_migration.sh`` on the box) and ``scripts/premerge.sh``
runs it on every merge, before the early exit for a branch that contains main.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migration_ledger_check.py"
PREMERGE = (ROOT / "scripts" / "premerge.sh").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "scripts"))
import migration_ledger_check as mlc  # noqa: E402

HEADER = "number\tlane\ttask\tpr\tbooked_utc\n"


def _ledger(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "migration-numbers.tsv"
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


def _run(tmp_path: Path, ledger: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ledger", str(ledger), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
    )


def test_a_free_number_above_main_passes(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "481\tpc-sec\t3844\t2758\t2026-11-22T10:00Z")
    out = _run(tmp_path, ledger, "--main-max", "480", "--pr", "2758", "481")
    assert out.returncode == 0, out.stdout


def test_a_number_at_or_below_main_fails_and_names_the_next_free_one(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "483\tpc-sec\t3844\t2758\t2026-11-22T10:00Z")
    out = _run(tmp_path, ledger, "--main-max", "480", "--pr", "9", "479")
    assert out.returncode == 1
    assert "not above main's highest number (480)" in out.stdout
    assert "Book 484" in out.stdout  # one above the ledger's top, not just main's


def test_a_number_booked_for_another_pull_request_fails(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "481\tpc-sec\t3844\t2758\t2026-11-22T10:00Z")
    out = _run(tmp_path, ledger, "--main-max", "480", "--pr", "2900", "481")
    assert out.returncode == 1
    assert "booked for pull request 2758" in out.stdout and "Book 482" in out.stdout


def test_a_booking_with_no_pr_yet_is_not_a_collision(tmp_path: Path) -> None:
    """reserve_migration.sh writes `-` until the PR opens, so the owner is unknown."""
    ledger = _ledger(tmp_path, "481\tpc-sec\t3844\t-\t2026-11-22T10:00Z")
    out = _run(tmp_path, ledger, "--main-max", "480", "--pr", "2900", "481")
    assert out.returncode == 0, out.stdout


def test_with_no_ledger_only_the_main_check_runs(tmp_path: Path) -> None:
    missing = tmp_path / "none.tsv"
    assert _run(tmp_path, missing, "--main-max", "480", "481").returncode == 0
    low = _run(tmp_path, missing, "--main-max", "480", "480")
    assert low.returncode == 1 and "Book 481" in low.stdout


def test_two_lanes_that_book_at_once_hold_different_numbers() -> None:
    """The ledger's own rule: the next number is one above the top of main and the list."""
    rows = [{"number": "481", "lane": "a", "task": "1", "pr": "-"}]
    assert mlc.next_free(480, rows) == 482
    rows.append({"number": "482", "lane": "b", "task": "2", "pr": "-"})
    assert mlc.next_free(480, rows) == 483


def test_premerge_runs_the_ledger_check_before_its_early_exit() -> None:
    """A branch that contains main used to skip every R1 check."""
    call = PREMERGE.index("migration_ledger_check.py")
    early_exit = PREMERGE.index('if [ "$behind" = 0 ]')
    assert call < early_exit
    assert "PREMERGE_LEDGER" in PREMERGE and "PREMERGE_PR" in PREMERGE


def test_ledger_only_leaves_the_main_check_to_premerge(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    out = _run(tmp_path, ledger, "--main-max", "480", "--ledger-only", "479")
    assert out.returncode == 0, out.stdout
    assert "--ledger-only" in PREMERGE


def test_premerge_only_warns_on_a_ledger_hit_and_finds_the_checker_after_a_cd() -> None:
    """Findings of the verifier of PR 2816: a hard fail blocks a good merge."""
    block = PREMERGE[PREMERGE.index("0b. Migration numbers"):PREMERGE.index('if [ "$behind" = 0 ]')]
    assert "|| fail" not in block and "WARNING" in block
    assert PREMERGE.index("LEDGER_CHECK=") < PREMERGE.index('cd "$(git rev-parse --show-toplevel)"')
    assert "${HOME:-" in block
