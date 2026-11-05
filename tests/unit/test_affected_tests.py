"""Fence for scripts/affected_tests.py, the inner-loop test selector.

It builds a small repo in tmp_path, so the rules are checked without the
real tree's 1,000 test files.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    for path in ("pyproject.toml", "uv.lock", ".test_durations"):
        assert sel.gate([path], root)["full"] is True, path


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


def test_gate_full_when_a_hub_module_has_too_many_test_importers(
    tmp_path: Path, monkeypatch
) -> None:
    root = _gate_repo(tmp_path)
    monkeypatch.setattr(sel, "HUB_LIMIT", 1)  # test_leaf and test_patch import leaf
    out = sel.gate(["packages/pkg/pkg/leaf.py"], root)
    assert out["full"] is True
    assert any("leaf" in r for r in out["reasons"])


def test_gate_not_full_for_a_module_under_the_hub_limit(tmp_path: Path) -> None:
    out = sel.gate(["packages/pkg/pkg/leaf.py"], _gate_repo(tmp_path))
    assert out["full"] is False
    assert "tests/unit/test_leaf.py" in out["files"]
    assert "tests/unit/test_user.py" in out["files"]


def test_gate_postgres_change_selects_every_db_gate_file(tmp_path: Path) -> None:
    out = sel.gate(["infra/postgres/300_new.sql"], _gate_repo(tmp_path))
    assert out["full"] is False
    assert "tests/unit/test_db.py" in out["files"]
    assert "tests/unit/test_migration_prefixes.py" in out["files"]


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
