"""pytest plugin: collect only the selected test files (CIV-33a, task 3071).

pytest 9.1 lists the whole ``tests/unit`` tree again for each file path on the
command line. A selection of 341 files made 594,027 ``Package.collect`` calls.
This plugin lets CI pass ``tests/unit`` once and drop the unselected files.

Load it with ``-p pytest_only_selected`` and set ``SHARD_SELECTED_FILES`` to a
file that lists one repo-relative path per line (or space separated). With no
list, the plugin does nothing. It ignores only ``.py`` files under
``tests/unit``. It never ignores a directory, a ``conftest.py`` or an
``__init__.py``.

At configure time the plugin reads the list once, then takes its two shard-only
variables out of the environment (``SHARD_SELECTED_FILES`` and its own folder
in ``PYTHONPATH``). A subprocess that a test starts then sees the same
environment as on a full run (the SHARD_-only rule of PR 1749, and the Opus
review of PR 2192).
Fence: tests/unit/test_pytest_only_selected.py.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV = "SHARD_SELECTED_FILES"
_SCOPE = "tests/unit/"
_KEEP = {"conftest.py", "__init__.py"}
_selected: set[str] | None = None


def _load() -> set[str] | None:
    global _selected
    if _selected is None:
        name = os.environ.get(ENV, "").strip()
        if not name:
            return None
        text = Path(name).read_text(encoding="utf-8")
        _selected = {p.replace("\\", "/") for p in text.split()}
    return _selected


def pytest_configure(config):
    _load()
    os.environ.pop(ENV, None)
    here = Path(__file__).resolve().parent
    parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    keep = [p for p in parts if Path(p).resolve() != here]
    if keep:
        os.environ["PYTHONPATH"] = os.pathsep.join(keep)
    else:
        os.environ.pop("PYTHONPATH", None)


def pytest_ignore_collect(collection_path: Path, config):
    selected = _load()
    if selected is None or collection_path.suffix != ".py" or collection_path.name in _KEEP:
        return None
    try:
        rel = collection_path.resolve().relative_to(config.rootpath.resolve()).as_posix()
    except ValueError:
        return None
    if not rel.startswith(_SCOPE):
        return None
    return None if rel in selected else True
