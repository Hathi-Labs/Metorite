"""pytest plugin: collect only the selected test files (CIV-33a, task 3071).

pytest 9.1 lists the whole ``tests/unit`` tree again for each file path on the
command line. A selection of 341 files made 594,027 ``Package.collect`` calls.
This plugin lets CI pass ``tests/unit`` once and drop the unselected files.

Load it with ``-p pytest_only_selected`` and set ``CI_SELECTED_FILES`` to a file
that lists one repo-relative path per line (or space separated). With no list,
the plugin does nothing. It ignores only ``.py`` files under ``tests/unit``.
It never ignores a directory, a ``conftest.py`` or an ``__init__.py``.
Fence: tests/unit/test_pytest_only_selected.py.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV = "CI_SELECTED_FILES"
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
