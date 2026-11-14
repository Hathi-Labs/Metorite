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


def run(*paths: str) -> tuple[str, list[str]]:
    return sel.select([ROOT + p for p in paths], MAP, E2E, CONFIG)


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


def test_every_spec_is_in_an_area_or_always_run():
    assert SPECS, "no spec found"
    assert set(SPECS) - _covered_specs() == set()


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
    f.write_text(ROOT + "src/app/crm/page.tsx\n", encoding="utf-8")
    assert sel.main(["--files-from", str(f)]) == 0
    out = capsys.readouterr().out
    assert "e2e_mode=partial" in out and "e2e/crm-landing-once.spec.ts" in out and "e2e_total=" in out
    f.write_text(ROOT + "package.json\n", encoding="utf-8")
    sel.main(["--files-from", str(f)])
    out = capsys.readouterr().out
    assert "e2e_files=\n" in out and "e2e_shards=[1,2,3]" in out and "e2e_total=3" in out
    f.write_text("docs/x.md\n", encoding="utf-8")
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
