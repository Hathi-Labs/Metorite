"""CIV-33b: the merge logic of scripts/refresh_test_durations.py. No network."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "refresh_test_durations",
    Path(__file__).resolve().parents[2] / "scripts" / "refresh_test_durations.py",
)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_new_entries_win_and_a_fresh_file_drops_its_stale_ids() -> None:
    old = {"tests/a.py::t1": 1.0, "tests/a.py::gone": 9.0, "tests/b.py::t": 2.0}
    new = {"tests/a.py::t1": 3.0, "tests/c.py::t": 5.0}
    out = mod.merge(old, new)
    assert out == {"tests/a.py::t1": 3.0, "tests/b.py::t": 2.0, "tests/c.py::t": 5.0}


def test_entries_of_deleted_files_drop_out() -> None:
    old = {"tests/a.py::t": 1.0, "tests/dead.py::t": 4.0}
    out = mod.merge(old, {"tests/new.py::t": 1.0}, exists=lambda f: f != "tests/dead.py")
    assert list(out) == ["tests/a.py::t", "tests/new.py::t"]


def test_render_is_sorted_stable_and_round_trips() -> None:
    text = mod.render({"b::t": 2.0, "a::t": 1.5})
    assert text.index('"a::t"') < text.index('"b::t"')
    assert text.endswith("\n")
    assert json.loads(text) == {"a::t": 1.5, "b::t": 2.0}
    assert mod.render(json.loads(text)) == text


def test_pick_artifact_takes_the_newest_live_green_run() -> None:
    arts = [
        {"created_at": "2026-11-10", "workflow_run": {"id": 1}},
        {"created_at": "2026-11-12", "workflow_run": {"id": 2}},
        {"created_at": "2026-11-13", "workflow_run": {"id": 3}, "expired": True},
        {"created_at": "2026-11-11", "workflow_run": {"id": 4}},
    ]
    verdict = {1: "success", 2: "failure", 4: "success"}
    assert mod.pick_artifact(arts, verdict.get)["workflow_run"]["id"] == 4
    assert mod.pick_artifact(arts, lambda _r: "failure") is None
