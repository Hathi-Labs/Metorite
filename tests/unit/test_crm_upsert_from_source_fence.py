"""The mirror write path has one door (WS-53 CRM-Z3, spec crm_platform.md §3.5).

``core.upsert_from_source`` writes a row a provider owns and never marks it
dirty. If a route, an agent or a skill called it, a native edit would pass as
a mirror write and the sync would never push it. So only the future
``gateway/crm_sync/`` package may name it.

**The fence reads names, not call graphs.** Any ``Name``, ``Attribute``,
import alias or string constant equal to ``upsert_from_source`` counts. That
catches a plain import, an aliased import (``as u``), a relative import
(``from .core import ...``), an import inside a function body, a module
attribute call (``core.upsert_from_source``), a star import followed by a
call, and ``getattr(core, "upsert_from_source")``.

**What it cannot see.** A name built at run time (``"upsert_" + "from_source"``)
and a file outside the three roots. Both need someone willing to edit around
the fence, and a fence cannot stop that (the same limit as the pipeline gate
fences in ``test_crm_pipeline.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = "upsert_from_source"
#: The second door of the mirror write path (CRM-Z4), and the third (CRM-Q2,
#: the stages of a mirrored pipeline), and the fourth (CRM-Q5, the contacts of
#: a mirrored deal). Same fence.
NAMES = (
    NAME, "delete_from_source", "set_pipeline_stages_from_source",
    "set_deal_contacts_from_source",
)

#: The trees that must never name the function.
SCAN_ROOTS = (
    ROOT / "apps" / "services" / "gateway" / "gateway" / "routes",
    ROOT / "apps" / "agents",
    ROOT / "apps" / "skills",
    ROOT / "skills",
)

#: The one file that defines it. ``gateway/crm_sync/`` is outside every root.
DEFINER = (
    ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm" / "core.py"
)


def _names_it(tree: ast.AST) -> list[int]:
    """Line numbers where *tree* names the function in any spelling."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            (isinstance(node, ast.Name) and node.id in NAMES)
            or (isinstance(node, ast.Attribute) and node.attr in NAMES)
            or (isinstance(node, ast.Constant) and node.value in NAMES)
            or (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in NAMES
            )
        ):
            lines.append(node.lineno)
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in NAMES:
            lines.append(getattr(node, "lineno", 0))
    return lines


def violations(roots: tuple[Path, ...], definer: Path | None = None) -> list[str]:
    """Every ``path:line`` under *roots* that names the function."""
    found: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if definer is not None and path == definer:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line in _names_it(tree):
                found.append(f"{path}:{line}")
    return found


def test_no_route_agent_or_skill_names_a_mirror_write_function() -> None:
    assert violations(SCAN_ROOTS, DEFINER) == []


def test_the_definer_and_the_scan_roots_exist() -> None:
    """A fence over a path that moved scans nothing and passes."""
    assert DEFINER.is_file()
    definer_text = DEFINER.read_text(encoding="utf-8")
    assert "async def upsert_from_source" in definer_text
    assert "async def delete_from_source" in definer_text
    assert "async def set_pipeline_stages_from_source" in definer_text
    assert any(r.is_dir() for r in SCAN_ROOTS)
    assert (ROOT / "apps" / "services" / "gateway" / "gateway" / "routes").is_dir()


@pytest.mark.parametrize(
    "source",
    [
        "from gateway.routes.crm.core import upsert_from_source\n",
        "from gateway.routes.crm.core import upsert_from_source as put\n",
        "from .core import upsert_from_source\n",
        "from ..crm.core import upsert_from_source as u\n",
        "from gateway.routes.crm import core\n\nasync def go(db):\n"
        "    await core.upsert_from_source(db, 'crm_leads', {})\n",
        "async def go(db):\n"
        "    from gateway.routes.crm.core import upsert_from_source as u\n"
        "    await u(db, 'crm_leads', {})\n",
        "import gateway.routes.crm.core as c\n\nF = c.upsert_from_source\n",
        "from gateway.routes.crm.core import *\n\nupsert_from_source(None, 'x', {})\n",
        "import importlib\n\nF = getattr(importlib.import_module('x'), 'upsert_from_source')\n",
        "from gateway.routes.crm.core import delete_from_source\n",
        "from gateway.routes.crm.core import set_pipeline_stages_from_source\n",
        "from gateway.routes.crm import core\n\nasync def go(db):\n"
        "    await core.set_pipeline_stages_from_source(db, 'p', [])\n",
        "from gateway.routes.crm import core\n\nasync def go(db):\n"
        "    await core.delete_from_source(db, 'crm_leads', 'zoho', [])\n",
        "import importlib\n\nF = getattr(importlib.import_module('x'), 'delete_from_source')\n",
    ],
)
def test_the_fence_catches_every_spelling(tmp_path: Path, source: str) -> None:
    """Red-first proof: a planted import in a synthetic package is found."""
    package = tmp_path / "routes" / "planted"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "bad.py").write_text(source, encoding="utf-8")
    assert violations((tmp_path / "routes",)), source


def test_a_clean_package_passes(tmp_path: Path) -> None:
    package = tmp_path / "routes" / "clean"
    package.mkdir(parents=True)
    (package / "ok.py").write_text(
        "from gateway.routes.crm.core import upsert_by_zoho_id\n", encoding="utf-8",
    )
    assert violations((tmp_path / "routes",)) == []


def test_the_definer_is_the_only_exemption(tmp_path: Path) -> None:
    package = tmp_path / "routes"
    package.mkdir()
    definer = package / "core.py"
    definer.write_text("async def upsert_from_source(db, t, v):\n    pass\n",
                       encoding="utf-8")
    assert violations((package,), definer) == []
    assert violations((package,), None) != []
