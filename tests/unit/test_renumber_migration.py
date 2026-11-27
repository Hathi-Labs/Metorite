"""scripts/renumber_migration.py moves a migration and rewrites its references (task 3928).

Each test builds a small git repo in a temp folder. The real tree is never touched.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "renumber_migration.py"
NAME = "_add_widget_table.sql"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    folder = tmp_path / "infra" / "postgres"
    folder.mkdir(parents=True)
    (folder / "100_first.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (folder / f"480{NAME}").write_text("CREATE TABLE widget();\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text(
        f"The table comes from 480{NAME}.\nSee migration 480 for the grants.\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fences.json").write_text(
        f'{{"files": ["infra/postgres/480{NAME}"]}}\n', encoding="utf-8"
    )
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2" + f"480{NAME}".encode())
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    return tmp_path


def _run(repo: Path, *args: str, env: dict | None = None):
    import os

    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--repo", str(repo), "--no-test"],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, **(env or {})},
    )


def test_it_moves_the_file_and_rewrites_each_reference(repo: Path) -> None:
    out = _run(repo, "480", "481")
    assert out.returncode == 0, out.stderr
    assert not (repo / "infra" / "postgres" / f"480{NAME}").exists()
    assert (repo / "infra" / "postgres" / f"481{NAME}").read_text(encoding="utf-8") == "CREATE TABLE widget();\n"
    assert f"481{NAME}" in (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    assert f"480{NAME}" not in (repo / "tests" / "fences.json").read_text(encoding="utf-8")
    assert f"481{NAME}" in (repo / "tests" / "fences.json").read_text(encoding="utf-8")
    # git sees a rename, so the history follows the file
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"], capture_output=True, text=True, check=True
    ).stdout
    assert "R" in status


def test_a_binary_file_is_left_alone(repo: Path) -> None:
    before = (repo / "blob.bin").read_bytes()
    assert _run(repo, "480", "481").returncode == 0
    assert (repo / "blob.bin").read_bytes() == before


def test_a_bare_number_in_prose_is_listed_for_review_and_not_rewritten(repo: Path) -> None:
    out = _run(repo, "480", "481")
    spec = (repo / "docs" / "spec.md").read_text(encoding="utf-8")
    assert "See migration 480 for the grants." in spec
    assert "CHECK BY HAND" in out.stdout and "docs/spec.md:2" in out.stdout


def test_a_taken_number_stops_the_run_and_changes_nothing(repo: Path) -> None:
    out = _run(repo, "480", "100")
    assert out.returncode == 2 and "taken" in out.stderr
    assert (repo / "infra" / "postgres" / f"480{NAME}").exists()


def test_an_unknown_number_stops_the_run(repo: Path) -> None:
    out = _run(repo, "999", "1000")
    assert out.returncode == 2 and "no tracked migration 999" in out.stderr


def test_with_no_new_number_it_books_one(repo: Path) -> None:
    out = _run(repo, "480", env={"RENUMBER_BOOK_CMD": "echo 'booked' && echo 482"})
    assert out.returncode == 0, out.stderr
    assert (repo / "infra" / "postgres" / f"482{NAME}").exists()


def test_with_no_new_number_and_no_lane_it_says_how_to_book(repo: Path) -> None:
    out = _run(repo, "480", env={"RENUMBER_BOOK_CMD": ""})
    assert out.returncode == 2 and "--lane and --task" in out.stderr


def test_the_width_of_the_old_number_is_kept(repo: Path) -> None:
    folder = repo / "infra" / "postgres"
    (folder / "097_old.sql").write_text("SELECT 2;\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pad")
    assert _run(repo, "97", "98").returncode == 0
    assert (folder / "098_old.sql").exists()


def test_a_longer_name_that_ends_like_the_old_one_is_not_rewritten(repo: Path) -> None:
    """Verifier finding: "80_x.sql" must not match inside "180_x.sql"."""
    folder = repo / "infra" / "postgres"
    (folder / "80_widget.sql").write_text("SELECT 3;\n", encoding="utf-8")
    (repo / "docs" / "other.md").write_text(
        "old 80_widget.sql and other 180_widget.sql here\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "near miss")
    assert _run(repo, "80", "81").returncode == 0
    text = (repo / "docs" / "other.md").read_text(encoding="utf-8")
    assert "old 81_widget.sql" in text and "other 180_widget.sql" in text


def test_a_lane_with_a_shell_character_is_refused(repo: Path) -> None:
    out = _run(repo, "480", "--lane", "x; touch /tmp/p", "--task", "1", env={"RENUMBER_BOOK_CMD": ""})
    assert out.returncode == 2 and "letters, digits" in out.stderr


def test_file_picks_one_of_two_files_with_the_same_number(repo: Path) -> None:
    """CIV-RN: a branch that merged main can hold main's 480 and its own 480."""
    (repo / "infra" / "postgres" / "480_main_took_it.sql").write_text("SELECT 2;\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main's 480")
    assert "ambiguous" in _run(repo, "480", "481").stderr
    out = _run(repo, "480", "481", "--file", f"infra/postgres/480{NAME}")
    assert out.returncode == 0, out.stderr
    assert (repo / "infra" / "postgres" / f"481{NAME}").exists()
    assert (repo / "infra" / "postgres" / "480_main_took_it.sql").exists()
    bad = _run(repo, "480", "482", "--file", "infra/postgres/100_first.sql")
    assert bad.returncode == 2 and "not a tracked migration 480" in bad.stderr


def test_width_sets_the_digits_of_the_new_name(repo: Path) -> None:
    """CIV-RN: the bot moves 480 to a work number, then back to three digits."""
    assert _run(repo, "480", "900000").returncode == 0
    assert (repo / "infra" / "postgres" / f"900000{NAME}").exists()
    out = _run(repo, "900000", "481", "--width", "3")
    assert out.returncode == 0, out.stderr
    assert (repo / "infra" / "postgres" / f"481{NAME}").exists()
    assert f"481{NAME}" in (repo / "tests" / "fences.json").read_text(encoding="utf-8")
