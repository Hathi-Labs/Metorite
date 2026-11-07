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
        "apps/services/gateway/gateway/routes/crm/auto_lead.py": "test_crm_mirror_readonly.py",
        "apps/services/gateway/gateway/routes/crm/reports.py": "test_crm_mirror_readonly.py",
        "apps/services/gateway/gateway/routes/crm/terms.py": "test_crm_mirror_readonly.py",
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
