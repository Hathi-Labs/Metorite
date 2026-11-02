"""Fence F2 for the assistant guide: a guide points only at things that exist (WS-OB, GD-3).

Spec: ``project-docs/specs/assistant_guide.md`` §5.2 and §5.7. Four checks
bind each guide:

* an ``open`` step names a live in-app path (``orchestrator.in_app_path``, the
  same check as ``show_in_app``, and the live pane set it holds);
* a ``point`` step names an anchor of the shell manifests;
* a ``do_tool`` is a chat tool that ``chat_parity.COVERED`` names;
* the ``feature`` of a guide equals the feature of its app's pane.

The loader checks the shape (unknown keys, step kinds, counts). Each mutation
test below feeds a bad guide to the checker or the loader and asserts the failure.

Run::

    uv run pytest tests/unit/test_guides.py tests/unit/test_guide_anchors.py -q
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from acb_common.app_manifests import guide_manifest
from acb_common.app_manifests.guide_manifest import Guide, GuideStep, load_guides
from acb_common.app_manifests.shell_manifest import ShellManifestError, load_anchors
from acb_skills.chat_parity import COVERED
from orchestrator.in_app_path import is_in_app_path, pane_of

ROOT = Path(__file__).resolve().parents[2]
GUIDES_TS = ROOT / "workbench/control_plane/src/lib/shell/guides.generated.ts"
#: Words a member must never read in a guide (user-facing copy rule).
_INTERNAL_NAMES = re.compile(r"\bjev\b", re.I)


def _covered_tools() -> set[str]:
    return {v if isinstance(v, str) else v[0] for v in COVERED.values()}


def _app_pane_feature(app: str) -> tuple[bool, str | None]:
    """Whether the app has a live pane, and the feature that gates it."""
    pane = pane_of("/" if app == "home" else f"/{app}")
    return (pane is not None, pane.feature if pane else None)


def problems(
    guides: tuple[Guide, ...],
    anchors: set[str] | None = None,
    tools: set[str] | None = None,
) -> list[str]:
    """Each way that these guides break the four F2 rules."""
    anchors = {a.full_id for a in load_anchors()} if anchors is None else anchors
    tools = _covered_tools() if tools is None else tools
    out: list[str] = []
    for g in guides:
        live, feature = _app_pane_feature(g.app)
        if not live:
            out.append(f"{g.id}: app {g.app!r} has no live pane")
        elif g.feature != feature:
            out.append(f"{g.id}: feature {g.feature!r} does not match the pane's {feature!r}")
        if g.do_tool is not None and g.do_tool not in tools:
            out.append(f"{g.id}: do_tool {g.do_tool!r} is not COVERED in chat_parity")
        texts = [g.title, *g.asks, *(s.say for s in g.steps)]
        if any(_INTERNAL_NAMES.search(t) for t in texts):
            out.append(f"{g.id}: the copy names an internal agent")
        for n, s in enumerate(g.steps, 1):
            where = f"{g.id} step {n}"
            if s.kind == "open":
                if not is_in_app_path(s.path) or pane_of(s.path) is None:
                    out.append(f"{where}: path {s.path!r} is not a live in-app path")
            elif s.kind == "point" and s.anchor not in anchors:
                out.append(f"{where}: anchor {s.anchor!r} is not in the shell manifests")
    return out


def _good() -> Guide:
    return Guide(
        id="crm.add-deal",
        app="crm",
        feature="crm",
        admin_only=False,
        title="Add a deal",
        asks=("add a deal", "new deal", "create a deal"),
        steps=(
            GuideStep("open", "Open CRM.", path="/crm"),
            GuideStep("point", "Press New deal.", anchor="home.ask"),
        ),
        do_tool=None,
    )


# ── The guides in the repo ──────────────────────────────────────────────────


def test_every_guide_passes_f2():
    assert problems(load_guides()) == []


def test_the_tour_is_one_guide_of_five_stops():
    tour = {g.id: g for g in load_guides()}["home.tour"]
    assert [s.anchor for s in tour.steps] == [
        "home.app-list",
        "home.ask",
        "chat.panel",
        "home.my-day",
        "chat.message-field",
    ]
    assert all(s.kind == "point" for s in tour.steps)
    assert tour.steps[3].say == "Your day starts here."
    assert tour.do_tool is None and tour.admin_only is False and 3 <= len(tour.asks) <= 6


def test_guides_ts_is_current():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "gen_shell_manifests", ROOT / "scripts/gen_shell_manifests.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_shell_manifests"] = module
    spec.loader.exec_module(module)
    want = module.render_guides_ts(load_guides())
    have = GUIDES_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert have == want, "guides.generated.ts is stale: run `uv run python scripts/gen_shell_manifests.py`"


# ── The four mutations: each bad guide must fail ────────────────────────────


def test_the_good_guide_passes():
    assert problems((_good(),)) == []


@pytest.mark.parametrize(
    "path",
    ["//evil.example", "https://evil.example", "/api/projects", "/connect", "/projects/../crm", "crm"],
)
def test_a_bad_path_fails(path):
    g = replace(_good(), steps=(GuideStep("open", "Go.", path=path),))
    assert any("not a live in-app path" in p for p in problems((g,))), path


def test_a_preview_pane_path_fails():
    g = replace(_good(), steps=(GuideStep("open", "Go.", path="/dashboard"),))
    assert any("not a live in-app path" in p for p in problems((g,)))


def test_an_unknown_anchor_fails():
    g = replace(_good(), steps=(GuideStep("point", "Here.", anchor="crm.ghost"),))
    assert any("not in the shell manifests" in p for p in problems((g,)))


def test_an_uncovered_tool_fails():
    g = replace(_good(), do_tool="no_such_chat_tool")
    assert any("not COVERED" in p for p in problems((g,)))


def test_a_covered_tool_passes():
    tool = sorted(_covered_tools())[0]
    assert problems((replace(_good(), do_tool=tool),)) == []


def test_a_feature_that_does_not_match_the_app_fails():
    assert any("does not match" in p for p in problems((replace(_good(), feature="email"),)))
    assert any("does not match" in p for p in problems((replace(_good(), feature=None),)))


def test_an_app_without_a_pane_fails():
    g = replace(_good(), id="ghost.x", app="ghost", feature=None)
    assert any("no live pane" in p for p in problems((g,)))


def test_member_copy_never_names_an_internal_agent():
    g = replace(_good(), title="Ask Jev to add a deal")
    assert any("internal agent" in p for p in problems((g,)))


# ── The loader refuses a bad shape ──────────────────────────────────────────


def _write(folder: Path, name: str, doc) -> None:
    (folder / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def _raw() -> dict:
    return {
        "id": "crm.add-deal",
        "app": "crm",
        "feature": "crm",
        "admin_only": False,
        "title": "Add a deal",
        "asks": ["add a deal", "new deal", "create a deal"],
        "steps": [{"kind": "open", "say": "Open CRM.", "path": "/crm"}],
        "do_tool": None,
    }


def test_loader_reads_a_good_file(tmp_path):
    _write(tmp_path, "crm", [_raw()])
    (g,) = load_guides(tmp_path)
    assert g.id == "crm.add-deal" and g.steps[0].path == "/crm"


def _bad(tmp_path, mutate, match):
    raw = _raw()
    mutate(raw)
    _write(tmp_path, "crm", [raw])
    with pytest.raises(ShellManifestError, match=match):
        load_guides(tmp_path)


def test_loader_rejects_bad_files(tmp_path):
    _bad(tmp_path, lambda r: r.update(selector="#x"), "must hold exactly")
    _bad(tmp_path, lambda r: r.pop("do_tool"), "must hold exactly")
    _bad(tmp_path, lambda r: r.update(asks=["one", "two"]), "asks must hold")
    _bad(tmp_path, lambda r: r.update(asks=["a"] * 7), "asks must hold")
    _bad(tmp_path, lambda r: r.update(id="deal.add"), "must start with the app")
    _bad(tmp_path, lambda r: r.update(steps=[]), "non-empty list")
    _bad(tmp_path, lambda r: r.update(admin_only="no"), "true or false")
    _bad(tmp_path, lambda r: r.update(steps=[{"kind": "click", "say": "x"}]), "step kind")
    _bad(tmp_path, lambda r: r.update(steps=[{"kind": "open", "say": "x"}]), "must hold exactly")
    _bad(
        tmp_path,
        lambda r: r.update(steps=[{"kind": "point", "say": "x", "anchor": "a.b", "selector": "#z"}]),
        "must hold exactly",
    )
    _bad(
        tmp_path,
        lambda r: r.update(steps=[{"kind": "open", "say": "x" * 161, "path": "/crm"}]),
        "longer than",
    )
    _bad(
        tmp_path,
        lambda r: r.update(steps=[{"kind": "fill", "say": "x", "form": "f", "values": {}}]),
        "values",
    )


def test_loader_rejects_a_guide_in_the_wrong_file(tmp_path):
    _write(tmp_path, "email", [_raw()])
    with pytest.raises(ShellManifestError, match="must have app 'email'"):
        load_guides(tmp_path)


def test_loader_rejects_a_repeated_id(tmp_path):
    _write(tmp_path, "crm", [_raw(), _raw()])
    with pytest.raises(ShellManifestError, match="duplicate guide id"):
        load_guides(tmp_path)


def test_the_module_lists_the_four_step_kinds():
    assert set(guide_manifest.STEP_KINDS) == {"open", "point", "fill", "say"}
