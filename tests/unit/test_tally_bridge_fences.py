"""TLY-1: the fences of apps/connectors/AGENTS.md for the Tally bridge.

1. The program imports nothing from apps/services or packages/ (it ships alone).
2. The three modules stay apart: the reader imports neither the tools nor the
   transport, and the tools do not import the transport (brief section 8).
3. Brief test 17: no company name, domain or ledger name of ours in the
   program or its defaults.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "connectors" / "tally_bridge"
PKG = ROOT / "tally_bridge"

# Every top-level package of the monorepo that a shipped program must not import.
_MONOREPO = {p.name for p in (REPO / "packages").iterdir() if p.is_dir()}
for kind in ("services", "agents", "skills"):
    for p in (REPO / "apps" / kind).iterdir():
        if p.is_dir():
            _MONOREPO.add(p.name.replace("-", "_"))
            for child in p.iterdir():
                if (child / "__init__.py").is_file():
                    _MONOREPO.add(child.name)

# Names and domains of ours (brief test 17). Lowercase.
_OURS = re.compile(r"hathi|metorite\.com", re.I)


# Folders that a local run makes and git ignores. They are not the program.
_LOCAL_ONLY = {"__pycache__", ".venv", "build", "dist", ".pytest_cache", ".ruff_cache"}


def _py_files(under: Path) -> list[Path]:
    return sorted(under.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_program_has_the_three_modules():
    for name in ("reader", "tools", "transport"):
        assert (PKG / name / "__init__.py").is_file(), name


def test_the_program_imports_nothing_of_the_monorepo():
    assert "gateway" in _MONOREPO and "acb_common" in _MONOREPO
    bad = []
    for f in _py_files(PKG):
        for mod in _imports(f):
            if mod.split(".")[0] in _MONOREPO:
                bad.append(f"{f.relative_to(REPO)} imports {mod}")
    assert not bad, bad


def _module_imports(module: str) -> set[str]:
    out: set[str] = set()
    for f in _py_files(PKG / module):
        out |= {m for m in _imports(f) if m.startswith("tally_bridge.")}
    return out


def test_the_reader_imports_neither_the_tools_nor_the_transport():
    bad = {
        m
        for m in _module_imports("reader")
        if m.startswith(("tally_bridge.tools", "tally_bridge.transport"))
    }
    assert not bad, bad


def test_the_reader_and_the_tools_do_not_import_the_setup_page():
    bad = {
        m
        for module in ("reader", "tools")
        for m in _module_imports(module)
        if m.startswith("tally_bridge.setup")
    }
    assert not bad, bad


def test_the_local_copy_imports_only_the_reader():
    """TLY-4. The copy reads Tally through the reader. It never imports the
    tools, the transport or the setup page, so the tools can import it."""
    bad = {
        m
        for m in _module_imports("localcopy")
        if m.startswith(("tally_bridge.tools", "tally_bridge.transport", "tally_bridge.setup"))
    }
    assert not bad, bad


def test_the_windows_module_imports_no_reader_tools_transport_or_setup():
    """TLY-5. The Windows side sits behind the store and key seams. It never
    reaches into the reader, the tools, the transport or the setup page."""
    bad = {
        m
        for m in _module_imports("windows")
        if m.startswith(("tally_bridge.reader", "tally_bridge.tools",
                         "tally_bridge.transport", "tally_bridge.setup"))
    }
    assert not bad, bad


def test_no_module_but_the_entry_point_imports_the_windows_side():
    bad = {
        f"{module}: {m}"
        for module in ("reader", "tools", "transport", "setup", "localcopy")
        for m in _module_imports(module)
        if m.startswith("tally_bridge.windows")
    }
    assert not bad, bad


def test_the_reader_does_not_import_the_local_copy():
    bad = {m for m in _module_imports("reader") if m.startswith("tally_bridge.localcopy")}
    assert not bad, bad


def test_the_tools_do_not_import_the_transport():
    bad = {m for m in _module_imports("tools") if m.startswith("tally_bridge.transport")}
    assert not bad, bad


def test_no_name_of_ours_in_the_program():
    """Brief test 17, over every file of the program folder."""
    hits = []
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file() or _LOCAL_ONLY.intersection(f.relative_to(ROOT).parts):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if _OURS.search(line):
                hits.append(f"{f.relative_to(REPO)}:{n}")
    assert not hits, hits


def test_the_program_is_not_a_workspace_member():
    import tomllib

    root = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    members = root["tool"]["uv"]["workspace"]["members"]
    assert not any(m.startswith("apps/connectors") for m in members)
