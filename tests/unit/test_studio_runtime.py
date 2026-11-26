"""STU-B7a: the pure import scan of a Studio runtime (no execution, no DB)."""

from __future__ import annotations

from acb_common.studio_runtime import SANDBOX_MODULES, scan_imports


def test_stdlib_and_a_lock_package_are_allowed() -> None:
    src = "import os, json\nimport pandas as pd\nfrom PIL import Image\nfrom os.path import join\n"
    assert scan_imports(src, "sandbox", {}) == []


def test_an_import_outside_the_lock_is_refused_with_a_path_and_a_sentence() -> None:
    errs = scan_imports("import json\nimport rich.console\n", "sandbox", {})
    assert [e.path for e in errs] == ["$.runtime"]
    assert "`rich`" in errs[0].message and "packages" in errs[0].message
    assert len(errs[0].message.split()) <= 25


def test_packages_kind_allows_the_named_package() -> None:
    src = "import rich\nimport pandas\n"
    assert scan_imports(src, "packages", {"rich": "13.7.1"}) == []
    errs = scan_imports("import attrs\n", "packages", {"rich": "13.7.1"})
    assert len(errs) == 1 and "`attrs`" in errs[0].message


def test_relative_and_future_imports_pass() -> None:
    assert scan_imports("from __future__ import annotations\nfrom . import x\nfrom .y import z\n",
                        "sandbox", {}) == []


def test_a_syntax_error_is_one_error_with_the_line() -> None:
    errs = scan_imports("import os\nx = (\n", "sandbox", {})
    assert len(errs) == 1 and "line" in errs[0].message


def test_none_with_source_is_one_error_and_without_source_is_clean() -> None:
    assert scan_imports("", "none", {}) == []
    assert len(scan_imports("print(1)", "none", {})) == 1
    assert scan_imports("import anything", "browser", {}) == []


def test_the_map_is_not_empty() -> None:
    assert SANDBOX_MODULES["pillow"] == "PIL"
