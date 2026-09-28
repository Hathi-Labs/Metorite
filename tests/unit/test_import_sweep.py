"""WS-41 I-7 — the sweep of old import files (spec §7.2).

It reads the disk only, so a tmp_path is the real thing.
"""

from __future__ import annotations

import os
import pathlib
import time

from gateway.routes.projects import import_sweep

DAY = 86400


def _run(root: pathlib.Path, org: str, run: str, age_days: float) -> pathlib.Path:
    folder = root / org / run
    folder.mkdir(parents=True)
    f = folder / "00-export.csv"
    f.write_text("x", encoding="utf-8")
    past = time.time() - age_days * DAY
    for p in (f, folder):
        os.utime(p, (past, past))
    return folder


def test_an_old_run_folder_goes_and_a_fresh_one_stays(tmp_path: pathlib.Path) -> None:
    old = _run(tmp_path, "org-a", "run-old", 20)
    fresh = _run(tmp_path, "org-a", "run-new", 1)
    counts = import_sweep.sweep(tmp_path)
    assert counts == {"runs": 1, "organizations": 0}
    assert not old.exists() and fresh.exists()


def test_an_emptied_organization_folder_goes(tmp_path: pathlib.Path) -> None:
    _run(tmp_path, "org-gone", "run", 30)
    counts = import_sweep.sweep(tmp_path)
    assert counts == {"runs": 1, "organizations": 1}
    assert not (tmp_path / "org-gone").exists()


def test_the_boundary_is_fourteen_days(tmp_path: pathlib.Path) -> None:
    kept = _run(tmp_path, "org", "run", import_sweep.KEEP_DAYS - 0.5)
    assert import_sweep.sweep(tmp_path)["runs"] == 0 and kept.exists()


def test_a_missing_root_is_fine(tmp_path: pathlib.Path) -> None:
    assert import_sweep.sweep(tmp_path / "nothing") == {"runs": 0, "organizations": 0}


def test_the_gateway_starts_and_stops_it() -> None:
    main = pathlib.Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/main.py"
    src = main.read_text(encoding="utf-8")
    assert "import_sweep import start_sweep" in src and "import_sweep import stop_sweep" in src
