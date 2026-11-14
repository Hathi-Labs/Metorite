"""CIV-29d (D218): the map from a changed path to the browser specs that cover it.

Fence for scripts/e2e_select.py, scripts/e2e_spec_map.json and the wiring in
.github/workflows/pr-check.yml. A map that misses a spec, names a path that does
not exist, or lets a shared path select a part, fails here.
"""
from __future__ import annotations

import fnmatch
import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("e2e_select", REPO / "scripts" / "e2e_select.py")
sel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sel)  # type: ignore[union-attr]

MAP = sel.load_map()
ROOT = MAP["root"]
CP = REPO / ROOT
E2E = CP / "e2e"
CONFIG = (CP / "playwright.config.ts").read_text(encoding="utf-8")
SPECS = sel.list_specs(E2E)
QUARANTINED = sel.quarantined(CONFIG)
LIVE = [s for s in SPECS if s not in QUARANTINED]


_MEMO: dict = {}


def run(*paths: str) -> tuple[str, list[str]]:
    return sel.select([ROOT + p for p in paths], MAP, E2E, CONFIG, _MEMO)


def _all_rel_paths() -> list[str]:
    out = []
    for p in CP.rglob("*"):
        if "node_modules" in p.parts or ".next" in p.parts:
            continue
        out.append(p.relative_to(CP).as_posix())
    return out


def _covered_specs() -> set[str]:
    out: set[str] = set(MAP["always_run"])
    for area in MAP["areas"].values():
        for glob in area["specs"]:
            out |= {s for s in SPECS if fnmatch.fnmatch(s, glob)}
    return out


def test_an_unmapped_spec_joins_every_partial_selection():
    """A new spec can merge before its map entry. It must then run on every
    partial selection, and this fence must not turn main red (2026-11-14)."""
    assert SPECS, "no spec found"
    assert sel.unmapped_specs(MAP, SPECS) == set(SPECS) - _covered_specs() - {
        s for r in MAP.get("importer_rules", []) for g in r.get("specs", [])
        for s in SPECS if fnmatch.fnmatch(s, g)
    }
    fake = {**MAP, "areas": {k: dict(v) for k, v in MAP["areas"].items()}}
    dropped = "studio-app-rail.spec.ts"
    fake["areas"]["other-apps"]["specs"] = [
        s for s in fake["areas"]["other-apps"]["specs"] if s != dropped
    ]
    assert dropped in sel.unmapped_specs(fake, SPECS)
    mode, picked = sel.select([ROOT + "src/app/crm/page.tsx"], fake, E2E, CONFIG, {})
    assert mode == "partial"
    assert dropped in picked


def test_the_map_names_each_spec_today():
    """Advisory for the author of a new spec: name it in an area. The selector
    runs an unmapped spec on every UI PR until then, so this only lists them."""
    unmapped = sel.unmapped_specs(MAP, SPECS)
    if unmapped:
        pytest.skip(f"specs with no map entry (they run on every UI PR): {sorted(unmapped)}")


def test_every_spec_glob_matches_a_spec():
    for name, area in MAP["areas"].items():
        for glob in area["specs"]:
            assert any(fnmatch.fnmatch(s, glob) for s in SPECS), f"{name}: {glob} matches no spec"
    for s in MAP["always_run"]:
        assert s in SPECS


def test_every_mapped_path_prefix_exists():
    rels = _all_rel_paths()
    prefixes = list(MAP["all"])
    for area in MAP["areas"].values():
        prefixes += area["paths"]
    missing = [x for x in prefixes if not any(r.startswith(x) for r in rels)]
    assert missing == []


@pytest.mark.parametrize("path", [
    "package.json", "package-lock.json", "playwright.config.ts", "next.config.ts",
    "src/proxy.ts", "src/app/layout.tsx", "src/app/globals.css", "src/components/ui/Button.tsx",
    "src/lib/shell/seams.ts", "src/lib/theme/themes.ts", "public/icon.png",
])
def test_a_shared_path_selects_every_spec(path):
    assert run(path) == ("all", SPECS)


@pytest.mark.parametrize("path", ["src/lib/brand_new_thing.ts", "scripts/x.mjs", "src/components/NeverMapped.tsx"])
def test_an_unknown_path_selects_every_spec(path):
    assert run(path) == ("all", SPECS)


def test_one_shared_path_among_area_paths_still_selects_all():
    assert run("src/app/knowledge/page.tsx", "src/app/globals.css")[0] == "all"


def test_an_area_path_selects_its_specs_and_always_run():
    mode, specs = run("src/app/knowledge/page.tsx")
    assert mode == "partial"
    assert set(specs) == ({s for s in LIVE if s.startswith("knowledge-")} | set(MAP["always_run"]))
    assert len(specs) < len(LIVE)


def test_a_vitest_file_or_a_doc_selects_nothing():
    assert run("src/lib/chatStore.test.ts") == ("none", [])
    assert run("README.md") == ("none", [])
    # A test file under a shared path still selects nothing.
    assert run("src/lib/theme/themes.test.ts") == ("none", [])


def test_a_path_outside_the_control_plane_selects_nothing():
    assert sel.select(["docs/x.md"], MAP, E2E, CONFIG) == ("none", [])


def test_a_spec_file_selects_itself_and_always_run():
    mode, specs = run("e2e/crm-landing-once.spec.ts")
    assert mode == "partial"
    assert "crm-landing-once.spec.ts" in specs and set(specs) <= {"crm-landing-once.spec.ts", *MAP["always_run"]}


def test_a_quarantined_spec_alone_selects_nothing():
    assert QUARANTINED, "the ci project quarantines no spec"
    assert run("e2e/" + sorted(QUARANTINED)[0]) == ("none", [])


def test_a_helper_selects_the_specs_that_import_it():
    mode, specs = run("e2e/email-message-fixtures.ts")
    assert mode == "partial"
    assert {"email-inline-images.spec.ts", "email-message-actions.spec.ts"} <= set(specs)


def test_the_shared_harness_selects_its_importers_and_an_unused_helper_selects_all(tmp_path):
    mode, specs = run("e2e/visual/harness.ts")
    assert mode == "partial" and "knowledge-pages.spec.ts" in specs
    # A helper that no spec imports gets the full suite.
    e2e = tmp_path / "e2e"
    (e2e / "visual").mkdir(parents=True)
    (e2e / "a.spec.ts").write_text("export {};\n", encoding="utf-8")
    (e2e / "orphan.ts").write_text("export {};\n", encoding="utf-8")
    assert sel.select([ROOT + "e2e/orphan.ts"], MAP, e2e, "") == ("all", ["a.spec.ts"])


def test_shard_plan_never_leaves_a_shard_without_a_spec():
    assert sel.shard_plan(1) == (1, [1])
    assert sel.shard_plan(2) == (2, [1, 2])
    assert sel.shard_plan(40) == (3, [1, 2, 3])


def test_main_prints_the_outputs(tmp_path, capsys):
    f = tmp_path / "files.txt"
    f.write_bytes((ROOT + "src/app/crm/page.tsx\n").encode("utf-8"))
    assert sel.main(["--files-from", str(f)]) == 0
    out = capsys.readouterr().out
    assert "e2e_mode=partial" in out and "e2e/crm-landing-once.spec.ts" in out and "e2e_total=" in out
    f.write_bytes((ROOT + "package.json\n").encode("utf-8"))
    sel.main(["--files-from", str(f)])
    out = capsys.readouterr().out
    assert "e2e_files=\n" in out and "e2e_shards=[1,2,3]" in out and "e2e_total=3" in out
    f.write_bytes(b"docs/x.md\n")
    sel.main(["--files-from", str(f)])
    assert "e2e_shards=[]" in capsys.readouterr().out


def test_the_workflow_feeds_the_selection_to_the_browser_job():
    wf = yaml.safe_load((REPO / ".github/workflows/pr-check.yml").read_text(encoding="utf-8"))
    changes, e2e = wf["jobs"]["changes"], wf["jobs"]["e2e"]
    for out in ("e2e_files", "e2e_shards", "e2e_total"):
        assert f"steps.diff.outputs.{out}" in changes["outputs"][out]
    assert "scripts/e2e_select.py" in changes["steps"][-1]["run"]
    assert e2e["strategy"]["matrix"]["shard"].startswith("${{ fromJSON(needs.changes.outputs.e2e_shards")
    assert "needs.changes.outputs.e2e_shards != '[]'" in e2e["if"]
    suite = next(s for s in e2e["steps"] if s.get("id") == "suite")
    assert "$SPECS" in suite["run"] and "/\"$TOTAL\"" in suite["run"]
    assert "e2e_files" in suite["env"]["SPECS"]


# ── Fix round 1: the import graph ────────────────────────────────────────────

REV = sel.src_graph(CP)
SRC_FILES = sorted(
    f.relative_to(CP).as_posix()
    for f in (CP / "src").rglob("*")
    if f.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs") and f.is_file()
    and not any(f.name.endswith(x) for x in MAP["ignore_suffixes"])
)


def test_every_importer_rule_names_real_paths_and_specs():
    rels = _all_rel_paths()
    for rule in MAP["importer_rules"]:
        assert rule["name"]
        for x in rule["paths"]:
            assert any(r.startswith(x) for r in rels), f"{rule['name'][:20]}: {x} names no file"
        for g in rule.get("specs", []):
            assert any(fnmatch.fnmatch(s, g) for s in SPECS), g
        for a in rule.get("areas", []):
            assert a in MAP["areas"]


def test_the_graph_reads_the_alias_and_relative_imports():
    # useAgentChat imports chatStore through the `@/` alias or a relative path.
    assert "src/hooks/useAgentChat.ts" in REV["src/lib/chatStore.ts"]
    assert "src/lib/shell/AssistantDock.tsx" in REV["src/lib/chatStore.ts"]


def test_a_fixture_or_test_file_is_not_an_importer():
    for importers_ in REV.values():
        assert not any(".test." in i or ".fixture." in i for i in importers_)


def test_every_src_file_selects_the_specs_of_each_transitive_importer():
    wrong = []
    for f in SRC_FILES:
        mode, specs = run(f)
        if mode == "all":
            continue
        got = set(specs)
        for imp in sel.importer_closure(f, REV):
            if imp.startswith("e2e/"):
                name = imp[len("e2e/"):]
                if name.endswith(".spec.ts") and "/" not in name and name not in QUARANTINED and name not in got:
                    wrong.append((f, imp))
                continue
            if imp not in _MEMO:
                _MEMO[imp] = sel.importer_specs(imp, MAP, SPECS)
            want = _MEMO[imp]
            if want is None:
                # A shared importer that other files import adds nothing. An entry point selects all.
                assert REV.get(imp), f"{f}: entry point {imp} is unnamed, so the run must be all"
                continue
            if not (want - QUARANTINED) <= got:
                wrong.append((f, imp))
    assert wrong == [], wrong[:5]


def test_an_agent_question_card_reaches_the_home_specs():
    mode, specs = run("src/components/AgentQuestionCard.tsx")
    assert mode in ("partial", "all")
    assert {"myday-peek.spec.ts", "my-day-feed.spec.ts"} <= set(specs)


@pytest.mark.parametrize("path", ["src/components/TaskMeta.tsx", "src/components/TaskPanelHeader.tsx", "src/lib/taskPanel.ts"])
def test_a_task_component_reaches_the_home_specs(path):
    assert {"myday-peek.spec.ts", "my-day-feed.spec.ts"} <= set(run(path)[1])


@pytest.mark.parametrize("path", ["src/components/CardPanel.tsx", "src/components/ElicitationCard.tsx", "src/components/GenUiText.tsx"])
def test_a_chat_card_reaches_the_home_specs(path):
    assert {"myday-peek.spec.ts", "my-day-feed.spec.ts"} <= set(run(path)[1])


def test_a_chat_library_reaches_the_shell_specs():
    specs = set(run("src/lib/chatStore.ts")[1])
    assert {"rail-rows.spec.ts", "app-title-bar.spec.ts", "shell-bell.spec.ts"} <= specs


def test_the_accounts_server_library_reaches_the_connect_specs():
    assert "connect-authorize.spec.ts" in run("src/lib/accountsServer.ts")[1]


def test_a_shared_file_reached_only_as_an_importer_does_not_select_every_spec():
    # `lib/shell/MyDayPage.tsx` is shared. TodayCard is imported only by it, so it
    # selects the home specs and the calendar specs, and not every spec.
    assert REV["src/app/calendar/components/TodayCard.tsx"] == {"src/lib/shell/MyDayPage.tsx"}
    mode, specs = run("src/app/calendar/components/TodayCard.tsx")
    assert mode == "partial" and len(specs) < len(LIVE)
    assert "myday-peek.spec.ts" in specs


def test_main_fails_loud_on_a_path_that_git_quotes(tmp_path):
    f = tmp_path / "files.txt"
    f.write_bytes(('"' + ROOT + 'a-b.ts"').encode("utf-8"))
    assert sel.main(["--files-from", str(f)]) == 2
    f.write_bytes((ROOT + "a" + chr(13) + "b.ts").encode("utf-8"))
    assert sel.main(["--files-from", str(f)]) == 2


def test_an_unsafe_spec_file_name_fails_the_selector(tmp_path):
    (tmp_path / "ok.spec.ts").write_text("", encoding="utf-8")
    assert sel.list_specs(tmp_path) == ["ok.spec.ts"]
    (tmp_path / "a b.spec.ts").write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        sel.list_specs(tmp_path)
