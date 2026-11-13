"""Fence for scripts/affected_tests.py, the inner-loop test selector.

It builds a small repo in tmp_path, so the rules are checked without the
real tree's 1,000 test files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import affected_tests as sel  # noqa: E402


def _write(root: Path, rel: str, body: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    _write(tmp_path, "packages/pkg/pkg/__init__.py")
    _write(tmp_path, "packages/pkg/pkg/leaf.py", "X = 1\n")
    _write(tmp_path, "packages/pkg/pkg/user.py", "from pkg import leaf\n")
    _write(tmp_path, "packages/pkg/pkg/hub.py", "import pkg.user\n")
    _write(tmp_path, "tests/unit/test_leaf.py", "from pkg.leaf import X\n")
    _write(tmp_path, "tests/unit/test_user.py", "import pkg.user\n")
    _write(tmp_path, "tests/unit/test_patch.py", 'patch("pkg.leaf.X")\n')
    _write(tmp_path, "tests/unit/test_doc.py", 'read("docs/guide_to_things.md")\n')
    _write(tmp_path, "tests/unit/test_other.py", "import os\n")
    return tmp_path


def test_the_module_name_follows_init_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert sel.module_name("packages/pkg/pkg/leaf.py", root) == "pkg.leaf"
    assert sel.module_name("packages/pkg/pkg/__init__.py", root) == "pkg"


def test_a_leaf_change_picks_direct_and_one_level_up(tmp_path: Path) -> None:
    picked, full, _ = sel.select(["packages/pkg/pkg/leaf.py"], _repo(tmp_path))
    assert "tests/unit/test_leaf.py" in picked  # from pkg.leaf import X
    assert "tests/unit/test_patch.py" in picked  # a dotted string
    assert "tests/unit/test_user.py" in picked  # imports a module that imports leaf
    assert "tests/unit/test_other.py" not in picked
    assert full == []


def test_a_test_that_names_a_doc_path_is_picked(tmp_path: Path) -> None:
    picked, _, _ = sel.select(["docs/guide_to_things.md"], _repo(tmp_path))
    assert picked == ["tests/unit/test_doc.py"]


def test_a_hub_is_not_expanded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sel, "HUB_LIMIT", 0)
    picked, _, _ = sel.select(["packages/pkg/pkg/leaf.py"], _repo(tmp_path))
    assert "tests/unit/test_user.py" not in picked


def test_a_shared_config_change_asks_for_the_full_suite(tmp_path: Path) -> None:
    _, full, _ = sel.select(["pyproject.toml", "tests/unit/conftest.py"], _repo(tmp_path))
    assert full == ["pyproject.toml", "tests/unit/conftest.py"]


def test_frontend_files_go_to_vitest(tmp_path: Path) -> None:
    f = "workbench/control_plane/src/lib/nav.ts"
    picked, _, frontend = sel.select([f], _repo(tmp_path))
    assert frontend == [f]
    assert picked == []


def test_a_migration_change_runs_the_prefix_guard(tmp_path: Path) -> None:
    picked, _, _ = sel.select(["infra/postgres/300_new.sql"], _repo(tmp_path))
    assert "tests/unit/test_migration_prefixes.py" in picked


# ── The import forms a regex parser lost (verifier, 2026-10-16) ────────────


def test_consecutive_from_lines_are_all_read() -> None:
    names = sel.imports("from a import b\nfrom a.c import d\n\nfrom agent_edge import tools\n")
    assert {"a.b", "a.c.d", "agent_edge.tools"} <= names


def test_an_alias_is_dropped() -> None:
    names = sel.imports("from agent_edge import metadata as edge_metadata\nimport x.y as z\n")
    assert "agent_edge.metadata" in names
    assert "x.y" in names
    assert not any(" as " in n for n in names)


def test_a_parenthesised_import_over_lines_is_read() -> None:
    names = sel.imports("from pkg import (\n    leaf,\n    user as u,\n)\n")
    assert {"pkg.leaf", "pkg.user"} <= names


def test_a_relative_import_resolves_against_its_module() -> None:
    assert "pkg.leaf" in sel.imports("from . import leaf\n", "pkg.user")
    assert "pkg.leaf" in sel.imports("from .leaf import X\n", "pkg.user")
    assert "pkg.leaf" in sel.imports("from .leaf import X\n", "pkg", is_package=True)
    assert "a.leaf" in sel.imports("from ..leaf import X\n", "a.b.c")


def test_a_file_that_does_not_parse_still_gives_its_imports() -> None:
    names = sel.imports("from a import b as c\nfrom d import e\ndef broken(:\n")
    assert {"a.b", "d.e"} <= names


def test_a_relative_importer_counts_as_one_level_up(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(root, "packages/pkg/pkg/rel.py", "from . import leaf\n")
    _write(root, "tests/unit/test_rel.py", "from pkg import rel\n")
    picked, _, _ = sel.select(["packages/pkg/pkg/leaf.py"], root)
    assert "tests/unit/test_rel.py" in picked


def test_each_next_app_gets_its_own_vitest_command() -> None:
    cmds = sel.frontend_commands(
        ["workbench/control_plane/src/a.ts", "workbench/operator_console/src/app/page.tsx"]
    )
    assert cmds == [
        "cd workbench/control_plane && npx tsc --noEmit && npx vitest related --run src/a.ts",
        "cd workbench/operator_console && npx tsc --noEmit && npx vitest related --run src/app/page.tsx",
    ]


# ── --gate: the PR tier's selection, fail open (CIV-2) ─────────────────────


def _gate_repo(tmp_path: Path) -> Path:
    root = _repo(tmp_path)
    _write(root, "tests/fences.json", '{"python": {"safety": ["tests/unit/test_other.py"], "process": []}}')
    _write(root, "tests/unit/test_db.py", "pytestmark = _DB_GATE\n")
    _write(root, "tests/unit/test_migration_prefixes.py", "import os\n")
    _write(root, "tests/unit/test_nav.py", 'read("workbench/control_plane/src/lib/nav.ts")\n')
    return root


def test_gate_full_on_any_conftest_basename(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    for path in ("tests/e2e/conftest.py", "apps/gateway/conftest.py", "tests/unit/sub/conftest.py"):
        out = sel.gate([path], root)
        assert out["full"] is True, path
        assert out["reasons"], path


def test_gate_full_on_underscore_helper_under_tests(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    assert sel.gate(["tests/unit/_helpers.py"], root)["full"] is True
    assert sel.gate(["tests/_shared/x.py"], root)["full"] is False  # dir, not name


def test_gate_full_on_shared_config_and_timings(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    for path in ("pyproject.toml", "uv.lock"):
        assert sel.gate([path], root)["full"] is True, path


def test_timings_refresh_is_not_a_full_run(tmp_path: Path) -> None:
    # CIV-33b: a refresh PR changes only `.test_durations`, so it runs a small selection.
    root = _gate_repo(tmp_path)
    assert sel.gate([".test_durations"], root)["full"] is False


def test_gate_full_on_ci_and_selector_paths(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    for path in (
        ".github/workflows/pr-check.yml",
        "scripts/affected_tests.py",
        "scripts/fences.py",
        "scripts/ci_unit_guard.py",
        "scripts/ci_local.sh",
        "scripts/ci_pc/doctor.sh",
        "packages/acb_common/acb_common/db.py",
    ):
        assert sel.gate([path], root)["full"] is True, path


# ── CIV-33c-2: the .github/ rule, and an edit of tests/fences.json ─────────


@pytest.mark.parametrize(
    "path",
    [
        ".github/ISSUE_TEMPLATE/bug.md",
        ".github/CODEOWNERS",
        ".github/dependabot.yml",
        ".github/pull_request_template.md",
    ],
)
def test_gate_a_github_file_that_runs_no_test_is_not_full(tmp_path: Path, path: str) -> None:
    out = sel.gate([path], _gate_repo(tmp_path))
    assert out["full"] is False, out["reasons"]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/pr-check.yml",
        ".github/workflows/deploy.yml",
        ".github/workflows/anything_new.yml",
        ".github/actions/setup/action.yml",
    ],
)
def test_gate_a_workflow_or_action_stays_full(tmp_path: Path, path: str) -> None:
    out = sel.gate([path], _gate_repo(tmp_path))
    assert out["full"] is True
    assert any(".github/" in r for r in out["reasons"])


def _fences_repo(tmp_path: Path) -> Path:
    root = _gate_repo(tmp_path)
    for name in sel.FENCE_GUARD_TESTS:
        _write(root, name, "import os\n")
    _write(root, "tests/unit/test_new_r8.py", "import os\n")
    # Every R8 self-check names the file. A path match would select all of them.
    _write(root, "tests/unit/test_names_fences.py", 'read("tests/fences.json")\n')
    return root


def _fences(must_run: list[str], *, comment: str = "x") -> str:
    import json

    return json.dumps(
        {
            "_comment": [comment],
            "python": {"safety": ["tests/unit/test_other.py"], "process": []},
            "r8": {"disarmed": ["D unset"], "never_skip": ["tests/unit/a.py"], "must_run": must_run},
        }
    )


def _fences_gate(root: Path, base: str, head: str) -> dict:
    return sel.gate(["tests/fences.json"], root, texts={"tests/fences.json": (base, head)})


def test_gate_a_fences_edit_selects_the_added_file_and_the_guard_tests(tmp_path: Path) -> None:
    root = _fences_repo(tmp_path)
    out = _fences_gate(
        root, _fences(["tests/unit/test_old.py"]), _fences(["tests/unit/test_old.py", "tests/unit/test_new_r8.py"])
    )
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_new_r8.py" in out["files"]
    assert set(sel.FENCE_GUARD_TESTS) <= set(out["files"])
    assert "tests/unit/test_names_fences.py" not in out["files"]
    assert "tests/unit/test_old.py" not in out["files"]


def test_gate_a_fences_edit_that_removes_or_comments_adds_no_suite(tmp_path: Path) -> None:
    root = _fences_repo(tmp_path)
    base = _fences(["tests/unit/test_new_r8.py", "tests/unit/test_old.py"])
    for head in (_fences(["tests/unit/test_old.py"]), _fences(base_list(), comment="new words")):
        out = _fences_gate(root, base, head)
        assert out["full"] is False, out["reasons"]
        assert "tests/unit/test_new_r8.py" not in out["files"]
        assert set(sel.FENCE_GUARD_TESTS) <= set(out["files"])


def base_list() -> list[str]:
    return ["tests/unit/test_new_r8.py", "tests/unit/test_old.py"]


@pytest.mark.parametrize(
    ("base", "head"),
    [
        (_fences([]), "{ not json"),
        ("{ not json", _fences([])),
        ("", _fences([])),
        (_fences([]), "[]"),
        (_fences([]), '{"r8": {"must_run": 3}}'),
        (_fences([]), '{"r8": {"must_run": [1, 2]}}'),
    ],
)
def test_gate_a_fences_edit_that_cannot_be_read_is_full(tmp_path: Path, base: str, head: str) -> None:
    out = _fences_gate(_fences_repo(tmp_path), base, head)
    assert out["full"] is True
    assert any("tests/fences.json" in r for r in out["reasons"])


def test_gate_a_fences_edit_with_no_base_text_is_full(tmp_path: Path) -> None:
    out = sel.gate(["tests/fences.json"], _fences_repo(tmp_path))
    assert out["full"] is True


def test_gate_a_fences_edit_beside_a_workflow_is_still_full(tmp_path: Path) -> None:
    root = _fences_repo(tmp_path)
    out = sel.gate(
        ["tests/fences.json", ".github/workflows/pr-check.yml"],
        root,
        texts={"tests/fences.json": (_fences([]), _fences(["tests/unit/test_new_r8.py"]))},
    )
    assert out["full"] is True


def test_gate_full_when_a_hub_module_has_too_many_test_importers(
    tmp_path: Path, monkeypatch
) -> None:
    # CIV-33c-1. A hub goes FULL only above the ceiling share of the test files.
    root = _gate_repo(tmp_path)
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)  # test_leaf and test_patch import leaf
    monkeypatch.setattr(sel, "HUB_CEILING_SHARE", 0.1, raising=False)  # 0 of 9 files
    out = sel.gate(["packages/pkg/pkg/leaf.py"], root)
    assert out["full"] is True
    assert any("leaf" in r for r in out["reasons"])


def test_gate_a_module_under_the_hub_limit_is_never_full_in_a_small_tree(
    tmp_path: Path, monkeypatch
) -> None:
    # The ceiling never falls below HUB_LIMIT. In a 9-file tree, 20% is 1, and
    # without the floor a module with 2 test importers went FULL.
    root = _gate_repo(tmp_path)
    monkeypatch.setattr(sel, "HUB_LIMIT", 40)
    monkeypatch.setattr(sel, "HUB_CEILING_SHARE", 0.2, raising=False)
    out = sel.gate(["packages/pkg/pkg/leaf.py"], root)
    assert out["full"] is False, out["reasons"]
    assert {"tests/unit/test_leaf.py", "tests/unit/test_patch.py"} <= set(out["files"])


def test_gate_a_hub_below_the_ceiling_selects_its_importers(
    tmp_path: Path, monkeypatch
) -> None:
    root = _gate_repo(tmp_path)
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)  # leaf is a hub: two test importers
    monkeypatch.setattr(sel, "HUB_CEILING_SHARE", 0.9, raising=False)
    out = sel.gate(["packages/pkg/pkg/leaf.py"], root)
    assert out["full"] is False
    assert {"tests/unit/test_leaf.py", "tests/unit/test_patch.py"} <= set(out["files"])
    assert "tests/unit/test_other.py" in out["files"]  # the SAFETY fence stays


# ── CIV-33c-1: a flag-only diff of settings.py or env_guard.py ──────────────
_SETTINGS = "packages/acb_common/acb_common/settings.py"
_GUARD = "packages/acb_common/acb_common/env_guard.py"
_SETTINGS_BASE = (
    "class Settings:\n    old_flag: str = ''\n    model_config = 1\n\n\n"
    "def reader():\n    x = 1\n    return x\n"
)
_GUARD_BASE = 'PLATFORM_ENV_NAMES = frozenset({\n    "OLD_FLAG",\n})\n'


def _flag_repo(tmp_path: Path) -> Path:
    root = _gate_repo(tmp_path)
    _write(root, _SETTINGS, _SETTINGS_BASE)
    _write(root, _GUARD, _GUARD_BASE)
    _write(root, "tests/unit/test_flag_reader.py", 'monkeypatch.setenv("NEW_THING_ORGS", "*")\n')
    _write(root, "tests/unit/test_settings_x.py", "import acb_common.settings\n")
    _write(root, "tests/unit/test_hardening.py", "from acb_common import env_guard\n")
    _write(root, "tests/unit/test_unrelated.py", "import acb_common.settings\n")
    return root


def _added(path: str, base: str, after: int, *lines: str) -> tuple[str, dict]:
    """A diff and the base and head texts of `path`, with `lines` after line `after`."""
    rows = base.splitlines()
    head = "\n".join([*rows[:after], *lines, *rows[after:]]) + "\n"
    body = "".join(f"+{ln}\n" for ln in lines)
    diff = (
        f"diff --git a/{path} b/{path}\nindex 1..2 100644\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -{after},0 +{after + 1},{len(lines)} @@\n{body}"
    )
    return diff, {path: (base, head)}


def _flag_case(*lines: str, after: int = 3) -> tuple[str, dict]:
    return _added(_SETTINGS, _SETTINGS_BASE, after, *lines)


def _gate_flag(root: Path, changed: list[str], case: tuple[str, dict]) -> dict:
    return sel.gate(changed, root, diff=case[0], texts=case[1])


def test_gate_a_flag_only_diff_selects_the_tests_that_name_the_flag(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _flag_case("    # a comment", '    new_thing_orgs: str = ""')
    out = _gate_flag(root, [_SETTINGS], case)
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_flag_reader.py" in out["files"]  # names NEW_THING_ORGS
    assert "tests/unit/test_settings_x.py" in out["files"]  # a settings test, by name
    assert "tests/unit/test_hardening.py" in out["files"]  # imports env_guard
    assert "tests/unit/test_other.py" in out["files"]  # the SAFETY fence stays
    assert "tests/unit/test_unrelated.py" not in out["files"]


def test_gate_an_env_guard_name_added_is_a_flag_only_diff(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _added(_GUARD, _GUARD_BASE, 2, '    "NEW_THING_ORGS",')
    out = _gate_flag(root, [_GUARD], case)
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_flag_reader.py" in out["files"]


def test_gate_an_env_guard_name_that_exists_is_full(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _added(_GUARD, _GUARD_BASE, 2, '    "OLD_FLAG",')
    assert _gate_flag(root, [_GUARD], case)["full"] is True


def test_gate_a_modified_line_in_settings_is_full(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _flag_case('    new_thing_orgs: str = ""')
    diff = case[0].replace("@@ -3,0 +4,1 @@\n", "@@ -3 +4 @@\n-    model_config = 1\n")
    out = _gate_flag(root, [_SETTINGS], (diff, case[1]))
    assert out["full"] is True
    assert any("settings.py" in r for r in out["reasons"])


def test_gate_a_line_that_is_not_a_field_is_full(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _flag_case("    def helper(self): return 1")
    assert _gate_flag(root, [_SETTINGS], case)["full"] is True


def test_gate_a_field_that_exists_already_is_full(tmp_path: Path) -> None:
    # A second `old_flag` changes the default: it is not a new flag.
    root = _flag_repo(tmp_path)
    case = _flag_case('    old_flag: str = "x"')
    assert _gate_flag(root, [_SETTINGS], case)["full"] is True


def test_gate_a_model_config_is_full(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _flag_case("    model_config: int = 2")
    assert _gate_flag(root, [_SETTINGS], case)["full"] is True


def test_gate_an_assignment_inside_a_function_is_full(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    case = _flag_case('    raw: str = "raise"', after=7)
    assert _gate_flag(root, [_SETTINGS], case)["full"] is True


def test_gate_the_code_that_reads_an_added_flag_is_selected(tmp_path: Path) -> None:
    # A reader that uses getattr names the flag in code, and a test imports it.
    root = _flag_repo(tmp_path)
    _write(root, "packages/pkg/pkg/reader.py", 'v = getattr(settings, "new_thing_orgs", "")\n')
    _write(root, "tests/unit/test_reader_user.py", "import pkg.reader\n")
    out = _gate_flag(root, [_SETTINGS], _flag_case('    new_thing_orgs: str = ""'))
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_reader_user.py" in out["files"]


def test_gate_a_flag_addition_plus_another_acb_common_file_is_full(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    other = "packages/acb_common/acb_common/db.py"
    out = _gate_flag(root, [_SETTINGS, other], _flag_case('    new_thing_orgs: str = ""'))
    assert out["full"] is True
    assert any("db.py" in r for r in out["reasons"])


def test_gate_a_flag_diff_keeps_the_github_reason(tmp_path: Path) -> None:
    root = _flag_repo(tmp_path)
    changed = [_SETTINGS, ".github/workflows/pr-check.yml"]
    out = _gate_flag(root, changed, _flag_case('    new_thing_orgs: str = ""'))
    assert out["full"] is True
    assert any(".github/" in r for r in out["reasons"])


def test_gate_with_no_diff_keeps_the_acb_common_prefix_full(tmp_path: Path) -> None:
    assert sel.gate([_SETTINGS], _flag_repo(tmp_path))["full"] is True


def test_gate_diff_reads_the_flag_files_between_two_commits(tmp_path: Path) -> None:
    import subprocess

    root = _flag_repo(tmp_path)

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "a")
    base = git("rev-parse", "HEAD")
    p = root / _SETTINGS
    text = p.read_text(encoding="utf-8").replace(
        "    model_config = 1\n", "    model_config = 1\n    new_thing_orgs: str = ''\n"
    )
    p.write_text(text, encoding="utf-8")
    git("commit", "-qam", "b")
    out = sel.gate(
        [_SETTINGS], root, diff=sel.gate_diff(base, root), texts=sel.gate_flag_texts(base, root)
    )
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_flag_reader.py" in out["files"]


# ── CIV-33c-1 review: an app entry module, the DB re-export, test fixtures ──
def _app_tree(root: Path, monkeypatch, hub: bool = True) -> Path:
    """`pkg.main` builds an app and imports `mid`, and `mid` imports `low`. With
    `hub`, `low` has more test importers than HUB_LIMIT: main's rules ran such a
    change FULL, so the app-entry rule applies (review round 2)."""
    monkeypatch.setattr(sel, "HUB_CEILING_SHARE", 0.9)
    app = "from fastapi import FastAPI\napp = FastAPI()\nfrom pkg import mid\n"
    _write(root, "packages/pkg/pkg/main.py", app)
    _write(root, "packages/pkg/pkg/mid.py", "from pkg import low\n")
    _write(root, "packages/pkg/pkg/low.py", "X = 2\n")
    _write(root, "tests/unit/test_low.py", "from pkg.low import X\n")
    if hub:
        monkeypatch.setattr(sel, "HUB_LIMIT", 0)  # test_low imports low
    return root


def test_gate_a_change_below_an_app_entry_selects_the_tests_of_the_app(
    tmp_path: Path, monkeypatch
) -> None:
    # The test imports only `pkg.main`. `low` sits two levels down, so `select`
    # does not expand it. The app imports it, so the test can break.
    root = _app_tree(_gate_repo(tmp_path), monkeypatch)
    _write(root, "tests/unit/test_app_drive.py", "from pkg.main import app\n")
    out = sel.gate(["packages/pkg/pkg/low.py"], root)
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_app_drive.py" in out["files"]


def test_gate_the_app_entry_rule_keeps_out_of_a_change_that_main_selects(
    tmp_path: Path, monkeypatch
) -> None:
    # Review round 2. Main's rules select for a change that no hub holds, and
    # the gate keeps that selection. The app-entry rule only replaces a FULL run.
    root = _app_tree(_gate_repo(tmp_path), monkeypatch, hub=False)
    _write(root, "tests/unit/test_app_drive.py", "from pkg.main import app\n")
    out = sel.gate(["packages/pkg/pkg/low.py"], root)
    assert out["full"] is False, out["reasons"]
    assert "tests/unit/test_low.py" in out["files"]
    assert "tests/unit/test_app_drive.py" not in out["files"]


def test_gate_the_gateway_db_re_export_is_the_db_seam_and_full(tmp_path: Path) -> None:
    out = sel.gate(["apps/services/gateway/gateway/db.py"], _gate_repo(tmp_path))
    assert out["full"] is True


def test_gate_a_changed_test_module_selects_the_users_of_its_users(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    _write(root, "tests/unit/test_fix_a.py", "def fixture_a():\n    pass\n")
    _write(root, "tests/unit/test_fix_b.py", "from tests.unit.test_fix_a import fixture_a\n")
    _write(root, "tests/unit/test_fix_c.py", "from tests.unit.test_fix_b import fixture_a\n")
    out = sel.gate(["tests/unit/test_fix_a.py"], root)
    assert out["full"] is False, out["reasons"]
    assert {"tests/unit/test_fix_b.py", "tests/unit/test_fix_c.py"} <= set(out["files"])


# ── CIV-33c-1 review round 2: a narrow closure, app entries, a strict flag rule ──
def _with_init(root: Path) -> Path:
    """The real tests/ and tests/unit/ are packages. A test module is then
    `tests.unit.test_x`, and its bare stem is not a module name."""
    _write(root, "tests/__init__.py")
    _write(root, "tests/unit/__init__.py")
    return root


def test_gate_the_closure_does_not_start_at_a_scanner_or_a_safety_fence(
    tmp_path: Path, monkeypatch
) -> None:
    # P1. Every Python change selects the scanners and the SAFETY fences. A test
    # that imports one of them only for that reason is not affected.
    monkeypatch.setattr(sel, "HUB_CEILING_SHARE", 0.9)
    root = _gate_repo(tmp_path)
    _write(root, "tests/unit/test_scan.py", "D.rglob('*.py')\nX = 1\n")
    _write(root, "tests/unit/test_scan_user.py", "from tests.unit.test_scan import X\n")
    _write(root, "tests/unit/test_fence_user.py", "from tests.unit.test_other import os\n")
    _write(root, "tests/unit/test_leaf_user.py", "from tests.unit.test_leaf import X\n")
    # A scanner that also imports the changed module still starts the closure.
    _write(root, "tests/unit/test_scan_leaf.py", "from pkg.leaf import X\nD.rglob('*.py')\n")
    _write(root, "tests/unit/test_scan_leaf_user.py", "from tests.unit.test_scan_leaf import X\n")
    files = set(sel.gate(["packages/pkg/pkg/leaf.py"], root)["files"])
    assert {"tests/unit/test_scan.py", "tests/unit/test_other.py"} <= files  # the floor stays
    assert "tests/unit/test_leaf_user.py" in files  # test_leaf imports the changed module
    assert "tests/unit/test_scan_leaf_user.py" in files
    assert "tests/unit/test_scan_user.py" not in files
    assert "tests/unit/test_fence_user.py" not in files


def test_gate_a_test_helper_that_builds_an_app_is_an_app_entry(
    tmp_path: Path, monkeypatch
) -> None:
    # P1. `_crm_app.crm_app()` mounts the CRM router in one shared app. A change
    # two levels below the router reaches each suite that calls the helper.
    root = _app_tree(_with_init(_gate_repo(tmp_path)), monkeypatch)
    helper = (
        "def make():\n    from fastapi import FastAPI\n    from pkg import mid\n\n"
        "    app = FastAPI()\n    return app\n"
    )
    _write(root, "tests/unit/_app_helper.py", helper)
    _write(root, "tests/unit/test_helper_user.py", "from tests.unit._app_helper import make\n")
    files = sel.gate(["packages/pkg/pkg/low.py"], root)["files"]
    assert "tests/unit/test_helper_user.py" in files
    assert "tests/unit/_app_helper.py" not in files


@pytest.mark.parametrize(
    "line", ["from pkg import main\n", "from pkg import main as app_module\n"]
)
def test_gate_an_app_entry_imported_by_its_leaf_selects_the_test(
    tmp_path: Path, monkeypatch, line: str
) -> None:
    # P2. `from gateway import main` and `from agent_edge import app as m` do
    # not spell the dotted entry name, and the test still drives the app.
    root = _app_tree(_gate_repo(tmp_path), monkeypatch)
    _write(root, "tests/unit/test_app_drive.py", line)
    files = sel.gate(["packages/pkg/pkg/low.py"], root)["files"]
    assert "tests/unit/test_app_drive.py" in files


@pytest.mark.parametrize(
    "line",
    [
        '    new_thing_x: str = __import__("os").environ.setdefault("ACB_ENV", "dev")',
        "    new_thing_x: str = Field(default_factory=lambda: make())",
        '    new_thing_x: str = Field(default=os.environ["X"])',
        "    new_thing_x: str",  # no default: a required field fails each Settings()
        '    new_thing_x: probe() = ""',
    ],
)
def test_gate_a_new_field_that_runs_code_is_full(tmp_path: Path, line: str) -> None:
    # P3. Only a literal default, or Field() with literal arguments, is a flag.
    root = _flag_repo(tmp_path)
    assert _gate_flag(root, [_SETTINGS], _flag_case(line))["full"] is True


@pytest.mark.parametrize(
    "line",
    [
        '    new_thing_orgs: str = ""',
        "    new_thing_max: int = -1",
        "    new_thing_bytes: int = 2 * 1024 * 1024",
        "    new_thing_ids: list[str] = []",
        '    new_thing_mode: Literal["a", "b"] = "a"',
        "    new_thing_size: int | None = None",
        '    new_thing_url: str = Field(default="x", description="y")',
    ],
)
def test_gate_a_new_field_with_a_literal_default_is_a_flag(tmp_path: Path, line: str) -> None:
    root = _flag_repo(tmp_path)
    out = _gate_flag(root, [_SETTINGS], _flag_case(line))
    assert out["full"] is False, out["reasons"]


def test_gate_a_test_imported_by_its_bare_stem_brings_its_users(tmp_path: Path) -> None:
    # P3. test_kit_core.py puts tests/unit on sys.path, then runs
    # `from test_playbook_org_neutral import hits`.
    root = _with_init(_gate_repo(tmp_path))
    _write(root, "tests/unit/test_base_rule.py", "def hits():\n    return 1\n")
    _write(root, "tests/unit/test_stem_a.py", "from test_base_rule import hits\n")
    _write(root, "tests/unit/test_stem_b.py", "from test_stem_a import hits\n")
    files = set(sel.gate(["tests/unit/test_base_rule.py"], root)["files"])
    assert {"tests/unit/test_stem_a.py", "tests/unit/test_stem_b.py"} <= files


def test_gate_the_closure_passes_through_a_test_helper(tmp_path: Path) -> None:
    # `_agent_board_r8` imports the fixtures of `test_agent_runs_r8`, and 17
    # suites import the helper. A change that test_agent_runs_r8 imports
    # reaches them through the helper.
    root = _with_init(_gate_repo(tmp_path))
    _write(root, "tests/unit/test_runs.py", "from pkg.leaf import X\n")
    _write(root, "tests/unit/_board.py", "from tests.unit.test_runs import X\n")
    _write(root, "tests/unit/test_board_user.py", "from tests.unit._board import X\n")
    files = sel.gate(["packages/pkg/pkg/leaf.py"], root)["files"]
    assert "tests/unit/test_board_user.py" in files
    assert "tests/unit/_board.py" not in files


def test_gate_not_full_for_a_module_under_the_hub_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sel, "HUB_CEILING_SHARE", 0.9)  # a 9-file repo has a tiny ceiling
    out = sel.gate(["packages/pkg/pkg/leaf.py"], _gate_repo(tmp_path))
    assert out["full"] is False
    assert "tests/unit/test_leaf.py" in out["files"]
    assert "tests/unit/test_user.py" in out["files"]


def test_gate_postgres_change_selects_every_db_gate_file(tmp_path: Path) -> None:
    out = sel.gate(["infra/postgres/300_new.sql"], _gate_repo(tmp_path))
    assert out["full"] is False
    assert "tests/unit/test_db.py" in out["files"]
    assert "tests/unit/test_migration_prefixes.py" in out["files"]


# ── --r8: the one rule for an R8 file (CIV-4b) ──────────────────────────────


def _r8_repo(tmp_path: Path) -> Path:
    root = _gate_repo(tmp_path)
    _write(root, "tests/fences.json", (
        '{"python": {"safety": ["tests/unit/test_other.py"],'
        ' "process": ["tests/unit/test_process_fence.py"]}}'
    ))
    # The helper holds the gate. Its users name no variable.
    _write(root, "tests/unit/_tenant_ladder.py", 'URL = os.environ.get("TENANT_LADDER_DATABASE_URL")\n')
    _write(root, "tests/unit/_plain_fakes.py", "X = 1\n")
    _write(root, "tests/unit/test_via_helper.py", "from tests.unit._tenant_ladder import URL\n")
    _write(root, "tests/unit/test_via_module.py", "from tests.unit import _tenant_ladder\n")
    _write(root, "tests/unit/test_names_var.py", 'os.environ["CUSTOMER_CONSOLE_DATABASE_URL"]\n')
    _write(root, "tests/unit/test_plain_helper.py", "from tests.unit._plain_fakes import X\n")
    # A longer name that holds the helper's name is not the helper.
    _write(root, "tests/unit/test_lookalike.py", "my_tenant_ladder_notes = 1\n")
    _write(root, "tests/unit/test_process_fence.py", 'read("TENANT_LADDER_DATABASE_URL")\n')
    return root


def test_r8_files_finds_the_gate_the_variables_and_the_helper_users(tmp_path: Path) -> None:
    """Measured 2026-11-05: `_DB_GATE` alone found 348 of the 454 files that
    skip without a database. The helper users were most of the rest."""
    got = sel.r8_files(_r8_repo(tmp_path))
    assert got == [
        "tests/unit/test_db.py",
        "tests/unit/test_names_var.py",
        "tests/unit/test_process_fence.py",
        "tests/unit/test_via_helper.py",
        "tests/unit/test_via_module.py",
    ]


def test_r8_gate_files_leave_out_the_process_fences(tmp_path: Path) -> None:
    # pytest's --ignore does not drop a file named on the command line, so
    # the list itself must leave an advisory fence out of a release gate.
    got = sel.r8_gate_files(_r8_repo(tmp_path))
    assert "tests/unit/test_process_fence.py" not in got
    assert "tests/unit/test_via_helper.py" in got


def test_gate_postgres_change_selects_every_r8_file(tmp_path: Path) -> None:
    out = sel.gate(["infra/postgres/300_new.sql"], _r8_repo(tmp_path))
    assert set(sel.r8_files(_r8_repo(tmp_path))) <= set(out["files"])
    assert "tests/unit/test_plain_helper.py" not in out["files"]


def test_r8_cli_prints_one_file_a_line(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sel, "r8_gate_files", lambda root=sel.ROOT: ["tests/unit/a.py", "tests/unit/b.py"])
    assert sel.main(["--r8"]) == 0
    assert capsys.readouterr().out == "tests/unit/a.py\ntests/unit/b.py\n"


def test_gate_frontend_file_is_matched_by_name_without_import_tracing(tmp_path: Path) -> None:
    out = sel.gate(["workbench/control_plane/src/lib/nav.ts"], _gate_repo(tmp_path))
    assert out["full"] is False
    assert "tests/unit/test_nav.py" in out["files"]
    assert "tests/unit/test_leaf.py" not in out["files"]


def test_gate_docs_change_selects_the_naming_test(tmp_path: Path) -> None:
    out = sel.gate(["docs/guide_to_things.md"], _gate_repo(tmp_path))
    assert "tests/unit/test_doc.py" in out["files"]
    assert out["full"] is False


def test_gate_always_lists_the_safety_fences(tmp_path: Path) -> None:
    out = sel.gate(["docs/unrelated.md"], _gate_repo(tmp_path))
    assert "tests/unit/test_other.py" in out["files"]


def test_gate_fails_open_on_an_error(tmp_path: Path) -> None:
    # No tests/fences.json in this root: the selector cannot finish.
    out = sel.gate(["docs/x.md"], _repo(tmp_path))
    assert out["full"] is True
    assert any("error" in r.lower() for r in out["reasons"])


def test_gate_json_cli_reads_the_three_dot_diff(tmp_path: Path, capsys, monkeypatch) -> None:
    seen: list[str] = []

    def fake(base: str, root: Path = sel.ROOT) -> list[str]:
        seen.append(base)
        return ["pyproject.toml"]

    monkeypatch.setattr(sel, "gate_changed_files", fake)
    assert sel.main(["--gate", "--base", "abc123"]) == 0
    import json

    out = json.loads(capsys.readouterr().out)
    assert seen == ["abc123"]
    assert set(out) == {"full", "files", "reasons"}
    assert out["full"] is True


def test_selector_imports_only_the_standard_library() -> None:
    import ast

    tree = ast.parse((_ROOT / "scripts" / "affected_tests.py").read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            mods.add(n.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"fences"}  # fences.py is stdlib-only too
    assert mods <= allowed, mods - allowed


def test_gate_diff_lists_both_paths_of_a_rename(tmp_path: Path) -> None:
    # With rename detection, git lists only the new path, and the tests that
    # import the old module are missed. `--no-renames` lists both.
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    _write(tmp_path, "apps/x/old.py", "VALUE = 1\n" * 20)
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("mv", "apps/x/old.py", "apps/x/new.py")
    git("commit", "-qm", "rename")
    assert sel.gate_changed_files(base, root=tmp_path) == ["apps/x/new.py", "apps/x/old.py"]


def test_picked_list_drops_known_local_hangers(capsys):
    """CIV-15: a picked list never runs a known local hanger, and says so."""
    picked = ["tests/unit/test_leaf.py", "tests/unit/test_memory_integration.py",
              "tests/unit/test_calendar_plan_decide.py"]
    assert sel.drop_local_hangs(picked) == ["tests/unit/test_leaf.py"]
    out = capsys.readouterr().out
    assert "test_memory_integration.py" in out and "test_calendar_plan_decide.py" in out


def test_hung_test_fails_within_timeout_and_names_itself(tmp_path):
    """CIV-15: a test that sleeps forever fails fast with its own name."""
    import subprocess
    t = tmp_path / "test_hang_probe.py"
    t.write_text("import time\n\ndef test_sleeps_forever():\n    time.sleep(3600)\n",
                 encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(t), "-q", "-p", "no:cacheprovider",
         "--timeout=2", "-c", str(_ROOT / "pyproject.toml"), "--rootdir", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
    assert "Timeout" in r.stdout and "test_sleeps_forever" in r.stdout


def test_pyproject_sets_a_default_per_test_timeout():
    import tomllib
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["tool"]["pytest"]["ini_options"]["timeout"] <= 600

# ── CIV-3 review round 1 ───────────────────────────────────────────────────


def _namespace_repo(tmp_path: Path) -> Path:
    root = _gate_repo(tmp_path)
    _write(root, "apps/services/gw/pyproject.toml", "[project]\nname='gw'\n")
    _write(root, "apps/services/gw/gw/__init__.py")
    _write(root, "apps/services/gw/gw/routes/actions.py", "X = 1\n")  # no __init__.py
    _write(root, "apps/services/gw/gw/routes/chan/__init__.py")
    _write(root, "apps/services/gw/gw/routes/chan/inbound.py", "X = 1\n")
    _write(root, "tests/unit/test_actions_routes.py", "from gw.routes import actions\n")
    _write(root, "tests/unit/test_inbound.py", "import gw.routes.chan.inbound\n")
    return root


def test_a_module_under_a_namespace_package_gets_its_full_dotted_name(tmp_path: Path) -> None:
    root = _namespace_repo(tmp_path)
    assert sel.module_name("apps/services/gw/gw/routes/actions.py", root) == "gw.routes.actions"
    assert sel.module_name("apps/services/gw/gw/routes/chan/inbound.py", root) == (
        "gw.routes.chan.inbound"
    )
    assert sel.module_name("apps/services/gw/gw/__init__.py", root) == "gw"


def test_gate_selects_the_importers_of_a_namespace_module(tmp_path: Path) -> None:
    root = _namespace_repo(tmp_path)
    out = sel.gate(["apps/services/gw/gw/routes/actions.py"], root)
    assert out["full"] is False
    assert "tests/unit/test_actions_routes.py" in out["files"]
    out = sel.gate(["apps/services/gw/gw/routes/chan/inbound.py"], root)
    assert "tests/unit/test_inbound.py" in out["files"]


def test_the_real_gateway_routes_resolve_to_the_names_the_tests_import() -> None:
    assert sel.module_name("apps/services/gateway/gateway/routes/actions.py") == (
        "gateway.routes.actions"
    )
    assert sel.module_name(
        "apps/services/gateway/gateway/routes/whatsapp_channel/inbound.py"
    ) == "gateway.routes.whatsapp_channel.inbound"
    out = sel.gate(["apps/services/gateway/gateway/routes/actions.py"])
    for t in ("test_actions_routes.py", "test_org_access_enforcement.py", "test_shell_needs.py"):
        assert f"tests/unit/{t}" in out["files"], t


def test_a_migration_selects_every_r8_suite_not_only_those_with_db_gate(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    _write(root, "tests/unit/test_ladder_only.py", "from _tenant_ladder import build\n")
    _write(root, "tests/unit/test_url_only.py", "os.environ['TENANT_LADDER_DATABASE_URL']\n")
    _write(root, "tests/unit/test_console_url.py", "os.environ['CUSTOMER_CONSOLE_DATABASE_URL']\n")
    _write(root, "tests/unit/test_cc_ladder.py", "import _customer_console_ladder\n")
    out = sel.gate(["infra/postgres/999_x.sql"], root)
    for t in ("test_db", "test_ladder_only", "test_url_only", "test_console_url", "test_cc_ladder"):
        assert f"tests/unit/{t}.py" in out["files"], t
    assert "tests/unit/test_user.py" not in out["files"]


def test_a_console_change_selects_every_console_r8_suite(tmp_path: Path) -> None:
    root = _gate_repo(tmp_path)
    _write(root, "tests/unit/test_customer_console_sql.py", "import os\n")
    _write(root, "tests/unit/test_cc_ladder.py", "import _customer_console_ladder\n")
    _write(root, "tests/unit/test_console_url.py", "os.environ['CUSTOMER_CONSOLE_DATABASE_URL']\n")
    out = sel.gate(["infra/customer_console/0005_x.sql"], root)
    for t in ("test_customer_console_sql", "test_cc_ladder", "test_console_url"):
        assert f"tests/unit/{t}.py" in out["files"], t
    assert "tests/unit/test_user.py" not in out["files"]


# ── CIV-3 review round 2 ───────────────────────────────────────────────────


def test_a_module_outside_a_pyproject_root_is_found_by_its_root_relative_name(
    tmp_path: Path,
) -> None:
    root = _gate_repo(tmp_path)
    _write(root, "evals/coding_engine/checkers.py", "X = 1\n")  # no __init__.py
    _write(
        root,
        "tests/unit/test_coding_eval_checkers.py",
        "from evals.coding_engine.checkers import X\n",
    )
    out = sel.gate(["evals/coding_engine/checkers.py"], root)
    assert out["full"] is False
    assert "tests/unit/test_coding_eval_checkers.py" in out["files"]


def test_the_real_evals_checkers_reach_their_tests() -> None:
    out = sel.gate(["evals/coding_engine/checkers.py"])
    assert "tests/unit/test_coding_eval_checkers.py" in out["files"]


def test_a_test_that_composes_the_path_from_quoted_parts_is_picked(tmp_path: Path) -> None:
    _gate_repo(tmp_path)
    # Hyphenated names parse as no module, so only the new rule can pick them.
    _write(tmp_path, "apps/svc/box/pkg-x/a-guard.py", "X = 1\n")
    _write(tmp_path, "apps/svc/other/app-y/run-it.py", "X = 1\n")
    _write(
        tmp_path,
        "tests/unit/test_composed.py",
        'D = ROOT / "box" / "pkg-x"\nsrc = (D / "a-guard.py").read_text()\n',
    )
    _write(tmp_path, "tests/unit/test_ctx.py", 'A = ROOT / "other/app-y"\nA.joinpath("run-it.py")\n')
    assert "tests/unit/test_composed.py" in sel.gate(["apps/svc/box/pkg-x/a-guard.py"], tmp_path)["files"]
    out = sel.gate(["apps/svc/other/app-y/run-it.py"], tmp_path)["files"]
    assert "tests/unit/test_ctx.py" in out
    wrong = {"tests/unit/test_wrong_folder.py": 'x = "run-it.py"\ny = "elsewhere"\n'}
    assert sel._composed_path_readers("apps/svc/other/app-y/run-it.py", wrong) == set()


def test_the_real_composed_path_readers_are_picked() -> None:
    cases = {
        "apps/skills/skill-projects/skill_projects/guarded.py": "test_held_card_writes.py",
        "apps/skills/skill-projects/skill_projects/writes.py": "test_held_card_writes.py",
        "apps/skills/skill-projects/skill_projects/forms.py": "test_held_card_writes.py",
        "apps/skills/skill-projects/skill_projects/inbox.py": "test_held_card_writes.py",
        "apps/services/email_ingestion/email_ingestion/inbound.py": "test_recipient_intent.py",
        "apps/services/meeting_bot/app/meet.py": "test_meeting_bot_call_end.py",
        "apps/services/meeting_bot/app/main.py": "test_meeting_bot_concurrency.py",
    }
    for path, test in cases.items():
        out = sel.gate([path])
        assert out["full"] or f"tests/unit/{test}" in out["files"], path


# ── CIV-3 review round 4 ───────────────────────────────────────────────────


def test_a_name_written_after_a_slash_is_a_composed_path_reader(tmp_path: Path) -> None:
    _gate_repo(tmp_path)
    _write(tmp_path, "apps/svc/g-drive/au-th.py", "X = 1\n")
    _write(tmp_path, "tests/unit/test_slash.py", 'FILES = ["g-drive/au-th.py"]\n')
    assert "tests/unit/test_slash.py" in sel.gate(["apps/svc/g-drive/au-th.py"], tmp_path)["files"]


def test_the_real_folder_scanners_are_picked() -> None:
    cases = {
        "apps/skills/skill-projects/skill_projects/guarded.py": "test_projects_agent_grants.py",
        "apps/services/gateway/gateway/routes/tasks/ai.py": "test_tasks_ai_no_thinking.py",
        "apps/services/gateway/gateway/routes/crm/auto_lead.py": "test_crm_mirror_readonly_scan.py",
        "apps/services/gateway/gateway/routes/crm/reports.py": "test_crm_mirror_readonly_scan.py",
        "apps/services/gateway/gateway/routes/crm/terms.py": "test_crm_mirror_readonly_scan.py",
        "apps/services/gateway/gateway/routes/email/automation/held_card.py": (
            "test_email_automation_tenancy.py"
        ),
        "apps/services/gateway/gateway/routes/projects/analytics.py": "test_projects_routes.py",
        "apps/services/gateway/gateway/kb_sources/google_drive/auth.py": (
            "test_kb_sources_no_global_creds.py"
        ),
        "apps/services/gateway/gateway/kb_sources/google_drive/client.py": (
            "test_kb_sources_no_global_creds.py"
        ),
    }
    for path, test in cases.items():
        assert f"tests/unit/{test}" in sel.gate([path])["files"], path



# ── CIV-3 review round 5: every scanning test (owner option A, task 1631) ───

_IDIOMS = {
    "glob": 'for p in D.glob("*.py"):\n    pass\n',
    "rglob": 'for p in D.rglob("*.py"):\n    pass\n',
    "glob.glob": 'import glob\nglob.glob("a/*.py")\n',
    "iterdir": "for p in D.iterdir():\n    pass\n",
    "os.walk": "for r, d, f in os.walk(D):\n    pass\n",
    "os.listdir": "os.listdir(D)\n",
    "scandir": "os.scandir(D)\n",
    "ls-files": 'run(["git", "ls-files", "-z"])\n',
    "git grep": 'run(["git", "grep", "-n", "X"])\n',
    "grep argv": 'run(["grep", "-rn", "X", "."])\n',
    "rg argv": "run(['rg', 'X'])\n",
    "fnmatch": "import fnmatch\nfnmatch.fnmatch(n, '*.py')\n",
    "iter_modules": "pkgutil.iter_modules(path)\n",
    "walk_packages": "pkgutil.walk_packages(path)\n",
    "ls-tree": 'run(["git", "ls-tree", "-r", "HEAD"])\n',
    "Path.walk": "for r, d, f in D.walk():\n    pass\n",
    "iglob": "import glob\nglob.iglob('a/*.py')\n",
    "importlib.resources": "importlib.resources.files('pkg')\n",
}


def test_every_scan_idiom_selects_the_test_for_any_python_change(tmp_path: Path) -> None:
    _gate_repo(tmp_path)
    _write(tmp_path, "apps/svc/zz-mod/x-y.py", "X = 1\n")
    _write(tmp_path, "docs/note.md", "text\n")
    _write(tmp_path, "tests/unit/test_plain.py", "assert 1\n")
    _write(tmp_path, "tests/unit/test_ast.py", "for n in ast.walk(tree):\n    pass\n")
    for i, body in enumerate(_IDIOMS.values()):
        _write(tmp_path, f"tests/unit/test_scan_{i:02d}.py", body)
    out = sel.gate(["apps/svc/zz-mod/x-y.py"], tmp_path)["files"]
    for i, name in enumerate(_IDIOMS):
        assert f"tests/unit/test_scan_{i:02d}.py" in out, name
    assert "tests/unit/test_plain.py" not in out
    assert "tests/unit/test_ast.py" not in out  # ast.walk is not a file scan
    docs = sel.gate(["docs/note.md"], tmp_path)["files"]
    assert not [f for f in docs if "test_scan_" in f]


def test_a_git_ls_files_scanner_is_picked_by_a_python_change(tmp_path: Path) -> None:
    _gate_repo(tmp_path)
    _write(tmp_path, "apps/svc/zz-mod/x-y.py", "X = 1\n")
    _write(
        tmp_path,
        "tests/unit/test_tracked.py",
        'out = subprocess.run(["git", "ls-files", "-z", "--", "*.py"])\n',
    )
    assert "tests/unit/test_tracked.py" in sel.gate(["apps/svc/zz-mod/x-y.py"], tmp_path)["files"]


def test_a_change_inside_tests_selects_the_scanners(tmp_path: Path) -> None:
    _gate_repo(tmp_path)
    _write(tmp_path, "tests/unit/test_scan.py", "D.rglob('*.py')\n")
    _write(tmp_path, "tests/unit/test_edit.py", "assert 1\n")
    out = sel.gate(["tests/unit/test_edit.py"], tmp_path)["files"]
    assert "tests/unit/test_scan.py" in out


def test_a_python_change_in_a_workbench_app_selects_the_scanners(tmp_path: Path) -> None:
    _gate_repo(tmp_path)
    _write(tmp_path, "tests/unit/test_scan.py", "D.rglob('*.py')\n")
    _write(tmp_path, "workbench/control_plane/scripts/x.py", "X = 1\n")
    out = sel.gate(["workbench/control_plane/scripts/x.py"], tmp_path)["files"]
    assert "tests/unit/test_scan.py" in out


def test_the_real_tests_and_workbench_python_changes_select_the_scanners() -> None:
    cases = {
        "tests/unit/test_email_triage.py": (
            "test_no_sales_assistant.py",
            "test_customer_console_credits.py",
        ),
        "workbench/control_plane/scripts/characters/mcp.py": (
            "test_object_store.py",
            "test_gmail_one_scope_set.py",
        ),
    }
    for path, tests in cases.items():
        assert (_ROOT / path).exists(), path
        out = sel.gate([path])
        for t in tests:
            assert (_ROOT / "tests/unit" / t).exists(), t
            assert f"tests/unit/{t}" in out["files"], (path, t)


def test_the_real_missed_scanners_follow_a_python_change() -> None:
    cases = {
        "apps/services/agent_edge/agent_edge/app.py": (
            "test_object_store.py",
            "test_gmail_one_scope_set.py",
            "test_third_party_notices.py",
        ),
        "apps/services/gateway/gateway/routes/email/transport/folders.py": (
            "test_stalled_session_cannot_freeze_the_db.py",
            "test_job_effects_fence.py",
            "test_feed_crm_kinds.py",
            "test_kb_flags.py",
        ),
    }
    for path, tests in cases.items():
        assert (_ROOT / path).exists(), path
        out = sel.gate([path])
        for t in tests:
            assert out["full"] or f"tests/unit/{t}" in out["files"], (path, t)


def test_the_real_tree_scanners_follow_a_python_change_and_not_a_doc() -> None:
    scanners = (
        "test_customer_child_env_no_github_token.py",
        "test_crm_kill_switch.py",
        "test_email_owner_predicate_seam.py",
        "test_signin_requests.py",
        "test_artifact_registry.py",
    )
    for path in ("apps/services/gateway/gateway/routes/crm/auto_lead.py", "scripts/gen_artifact_kit.py"):
        out = sel.gate([path])
        for t in scanners:
            assert out["full"] or f"tests/unit/{t}" in out["files"], (path, t)
    docs = sel.gate(["project-docs/specs/" + "ci_velocity" + ".md"])
    for t in scanners:
        assert f"tests/unit/{t}" not in docs["files"], t


def test_a_docs_only_change_selects_only_safety_fences_among_scanners() -> None:
    import json

    safety = set(json.loads((_ROOT / "tests/fences.json").read_text(encoding="utf-8"))["python"]["safety"])
    bodies = {
        f"tests/unit/{p.name}": p.read_text(encoding="utf-8", errors="replace")
        for p in (_ROOT / "tests/unit").glob("test_*.py")
    }
    scanners = sel.scanning_tests(bodies)
    assert len(scanners) > 200
    doc = "project-docs/specs/" + "ci_velocity" + ".md"  # not whole: the test must not name it
    out = sel.gate([doc])
    extra = {
        f for f in out["files"] if f in scanners and Path(f).name not in safety and f not in safety
    }
    assert not extra, sorted(extra)[:5]


def test_a_function_level_router_import_of_a_hub_package_is_picked(tmp_path: Path, monkeypatch) -> None:
    """CIV-18 (task 1991). The package registers `leaf` as an import side
    effect. A test imports the package router INSIDE a function and mounts it.
    The package is a hub, so the one-level rule skips its importers. The
    router importer must still run when `leaf` changes."""
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)
    root = _repo(tmp_path)
    _write(root, "packages/pkg/pkg/__init__.py", "from pkg import leaf as _leaf\nrouter = object()\n")
    _write(
        root,
        "tests/unit/test_mounts_router_r8.py",
        "def _app():\n    from pkg import router\n    return router\n",
    )
    _write(root, "tests/unit/test_reads_pkg_a.py", "import pkg\n")
    _write(root, "tests/unit/test_reads_pkg_b.py", "import pkg\n")
    picked, _, _ = sel.select(["packages/pkg/pkg/leaf.py"], root)
    assert "tests/unit/test_mounts_router_r8.py" in picked
    # The rule is narrow: other importers of a hub package stay out.
    assert "tests/unit/test_reads_pkg_a.py" not in picked


def _router_repo(tmp_path: Path) -> Path:
    """A package `pkg.api` whose __init__ registers `routes` (which imports
    `deep` through a sibling) and exposes `router`. The package is a hub."""
    root = _repo(tmp_path)
    base = "packages/pkg/pkg/api"
    _write(root, f"{base}/deep.py", "Y = 1\n")
    _write(root, f"{base}/routes.py", "from pkg.api import deep\n")
    _write(root, f"{base}/__init__.py", "from pkg.api import routes as _r\nrouter = object()\n")
    _write(root, "tests/unit/test_hub_a.py", "import pkg.api\n")
    _write(root, "tests/unit/test_hub_b.py", "import pkg.api\n")
    return root


def test_a_module_alias_router_read_is_picked(tmp_path: Path, monkeypatch) -> None:
    """CIV-18 review P1. `from parent import pkg as p` then `p.router`."""
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)
    root = _router_repo(tmp_path)
    _write(root, "tests/unit/test_alias_r8.py", "from pkg import api as p\napp = p.router\n")
    _write(root, "tests/unit/test_alias2_r8.py", "import pkg.api as q\napp = q.router\n")
    picked, _, _ = sel.select(["packages/pkg/pkg/api/routes.py"], root)
    assert "tests/unit/test_alias_r8.py" in picked
    assert "tests/unit/test_alias2_r8.py" in picked
    assert "tests/unit/test_hub_a.py" not in picked
    assert "pkg.api.router" in sel.imports("import pkg.api as q\napp = q.router\n")
    assert "pkg.api.router" in sel.imports("from pkg import api as p\napp = p.router\n")


def test_a_module_that_reaches_the_package_through_a_sibling_is_picked(
    tmp_path: Path, monkeypatch
) -> None:
    """CIV-18 review P1. `deep` is imported by `routes`, `routes` by the
    package. The router importer must run when `deep` changes."""
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)
    root = _router_repo(tmp_path)
    _write(root, "tests/unit/test_mounts_r8.py", "def _a():\n    from pkg.api import router\n    return router\n")
    picked, _, _ = sel.select(["packages/pkg/pkg/api/deep.py"], root)
    assert "tests/unit/test_mounts_r8.py" in picked
    assert "tests/unit/test_hub_a.py" not in picked


def test_a_test_helper_that_imports_a_test_module_carries_the_change(tmp_path: Path) -> None:
    """CIV-3c (task 1720). `_board.py` imports `test_runs`, and a suite imports
    `_board`. A change to `test_runs` must select the suite."""
    root = _repo(tmp_path)
    _write(root, "tests/__init__.py")
    _write(root, "tests/unit/__init__.py")
    _write(root, "tests/unit/test_runs.py", "X = 1\n")
    _write(root, "tests/unit/_board.py", "from tests.unit.test_runs import X\n")
    _write(root, "tests/unit/test_board_user.py", "from tests.unit._board import X\n")
    _write(root, "tests/unit/test_unrelated.py", "import os\n")
    picked, _, _ = sel.select(["tests/unit/test_runs.py"], root)
    assert "tests/unit/test_board_user.py" in picked
    assert "tests/unit/test_unrelated.py" not in picked


def test_the_gate_selects_every_suite_behind_the_agent_board_helper() -> None:
    """CIV-3c acceptance, on the real tree. `_agent_board_r8` imports
    `test_agent_runs_r8`, and every suite that imports the helper must come
    with a change to that module."""
    root = sel.ROOT
    helper = "_agent_board_r8"
    importers = {
        f.relative_to(root).as_posix()
        for f in (root / "tests" / "unit").glob("test_*.py")
        if helper in f.read_text(encoding="utf-8", errors="replace")
    }
    assert len(importers) >= 10, "the helper is imported by many suites"
    result = sel.gate(["tests/unit/test_agent_runs_r8.py"], root)
    assert not result["full"], result["reasons"]
    assert importers <= set(result["files"]), sorted(importers - set(result["files"]))


def test_a_test_helper_has_no_hub_limit(tmp_path: Path, monkeypatch) -> None:
    """CIV-3c review. A helper that more than HUB_LIMIT suites import still
    carries a change of a product module it imports to all of them."""
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)
    root = _repo(tmp_path)
    _write(root, "tests/__init__.py")
    _write(root, "tests/unit/__init__.py")
    _write(root, "tests/unit/_fakes.py", "from pkg import leaf\n")
    _write(root, "tests/unit/test_fake_a.py", "from tests.unit._fakes import leaf\n")
    _write(root, "tests/unit/test_fake_b.py", "from tests.unit._fakes import leaf\n")
    _write(root, "tests/unit/test_fake_c.py", "from tests.unit._fakes import leaf\n")
    picked, _, _ = sel.select(["packages/pkg/pkg/leaf.py"], root)
    assert {"tests/unit/test_fake_a.py", "tests/unit/test_fake_b.py",
            "tests/unit/test_fake_c.py"} <= set(picked)


# ── CIV-3d: only a glob that can list a Python file makes a test a scanner ──


@pytest.mark.parametrize("body,scanner", [
    ('from pathlib import Path\nfor p in Path("x").glob("*.sql"): pass\n', False),
    ('import glob\nglob.glob("infra/**/*.json", recursive=True)\n', False),
    ('Path("x").rglob("*.ts")\nPath("y").glob("a/*.md")\n', False),
    ('Path("x").glob("*.py")\n', True),
    ('Path("x").rglob("*")\n', True),
    ('Path("x").glob(PATTERN)\n', True),
    ('Path("x").glob(f"*{ext}")\n', True),
    ('Path("x").glob(pattern="*.sql")\n', True),  # a keyword argument is not read: fail open
    ('Path("x").glob("*.sql")\nPath("y").glob("*.py")\n', True),  # one risky call is enough
    ('Path("x").glob("*.sql")\nos.walk(".")\n', True),  # another idiom still counts
    ('Path("x").glob("*.sql")\nPath(".").iterdir()\n', True),
    ('Path("x").glob("*.sql"\n', True),  # does not parse: fail open
    ('x = 1\n', False),
])
def test_a_scanner_is_a_test_that_can_list_python_files(body: str, scanner: bool) -> None:
    assert sel._is_scanner(body) is scanner
    both = sel.scanning_tests({"tests/unit/test_x.py": body})
    expected = scanner and bool(sel._SCAN_IDIOM.search(body))
    assert (("tests/unit/test_x.py" in both) is expected)


def test_the_sql_glob_files_of_the_real_tree_are_no_longer_scanners() -> None:
    tests = sel._texts(sorted((sel.ROOT / sel.TESTS).glob("test_*.py")), sel.ROOT)
    scanners = sel.scanning_tests(tests)
    r8 = set(sel.r8_files())
    # Before CIV-3d 146 R8 files were scanners. The ones that only glob data files are not.
    assert len(scanners & r8) < 100, len(scanners & r8)
    # The scanners that read Python stay.
    # CIV-33d moved the CRM route scan into test_crm_mirror_readonly_scan.py.
    for must in ("tests/unit/test_data_engine.py", "tests/unit/test_crm_mirror_readonly_scan.py"):
        assert must in scanners, must
