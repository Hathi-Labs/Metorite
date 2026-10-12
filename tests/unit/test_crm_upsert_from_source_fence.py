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

**The start over has one caller** (WS-53 CRM-X0, ``crm_platform.md`` §19.11
item 6). ``core.reset_crm_from_admin`` deletes every CRM record of an org and
bypasses the Mirror write guard. Only ``routes/crm/settings.py`` may name it.
:data:`ALLOWED_CALLERS` holds that one exception, for that one name. Its scan
covers the whole gateway package, ``crm_sync/`` included, because a sync that
named it would empty a CRM by itself.

**The field-rule door is for the sync only** (WS-53 CRM-Q7a,
``crm_platform.md`` §18.7). ``core.apply_field_rule_from_source`` writes an
approved rule's value into a mirror table. Only the files of
``gateway/crm_sync/`` may name it, and the scan covers the whole gateway
package, every agent, every skill and every shared package.

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
#: a mirrored deal), and the fifth and sixth (CRM-Q6a, the generic record
#: store, which has no native writer). Same fence.
#: CRM-X0 adds the start over, which has ONE allowed caller.
RESET_NAME = "reset_crm_from_admin"
#: CRM-Q7a adds the field-rule door, which only ``gateway/crm_sync/`` calls.
RULE_NAME = "apply_field_rule_from_source"
NAMES = (
    NAME, "delete_from_source", "set_pipeline_stages_from_source",
    "set_deal_contacts_from_source",
    "upsert_module_records_from_source", "delete_module_records_from_source",
    RESET_NAME, RULE_NAME,
)

GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
SYNC = GATEWAY / "crm_sync"

#: A fenced name and the ONLY files that may name it. A name that is not here
#: has no caller outside the definer. The field-rule door may be named by the
#: files of the sync package and nowhere else.
ALLOWED_CALLERS: dict[str, frozenset[Path]] = {
    RESET_NAME: frozenset({GATEWAY / "routes" / "crm" / "settings.py"}),
    RULE_NAME: frozenset(SYNC.rglob("*.py")),
}

#: Where the start over may never be named: the whole gateway package (the
#: sync too), every agent, every skill and every shared package.
RESET_SCAN_ROOTS = (
    GATEWAY,
    ROOT / "apps" / "agents",
    ROOT / "apps" / "skills",
    ROOT / "skills",
    ROOT / "packages",
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


def _names_it(tree: ast.AST, names: tuple[str, ...] = NAMES) -> list[tuple[int, str]]:
    """``(line, name)`` for each place *tree* names a function, in any spelling."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.Constant) and node.value in names:
            found.append((node.lineno, str(node.value)))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in names:
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in names:
            found.append((getattr(node, "lineno", 0), node.name.split(".")[-1]))
    return found


def violations(
    roots: tuple[Path, ...],
    definer: Path | None = None,
    *,
    names: tuple[str, ...] = NAMES,
    allowed: dict[str, frozenset[Path]] | None = None,
) -> list[str]:
    """Every ``path:line`` under *roots* that names a fenced function.

    A file in ``allowed[name]`` may name that one name. ``None`` takes
    :data:`ALLOWED_CALLERS`.
    """
    allowed = ALLOWED_CALLERS if allowed is None else allowed
    found: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if definer is not None and path == definer:
                continue
            # utf-8-sig: a file of a shared package starts with a byte order mark.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for line, name in _names_it(tree, names):
                if path in allowed.get(name, frozenset()):
                    continue
                found.append(f"{path}:{line}")
    return found


def test_no_route_agent_or_skill_names_a_mirror_write_function() -> None:
    assert violations(SCAN_ROOTS, DEFINER) == []


def test_only_settings_names_the_start_over() -> None:
    """CRM-X0 done when 7: no file but ``routes/crm/settings.py`` names it."""
    assert violations(RESET_SCAN_ROOTS, DEFINER, names=(RESET_NAME,)) == []


def test_only_the_sync_names_the_field_rule_door() -> None:
    """CRM-Q7a: no file outside ``gateway/crm_sync/`` names the door."""
    assert violations(RESET_SCAN_ROOTS, DEFINER, names=(RULE_NAME,)) == []


def test_the_sync_does_name_the_field_rule_door() -> None:
    """The exception is used, so it is not dead text in the allowlist."""
    named = [
        path for path in ALLOWED_CALLERS[RULE_NAME]
        if _names_it(ast.parse(path.read_text(encoding="utf-8")), (RULE_NAME,))
    ]
    assert named, "no file of gateway/crm_sync/ names the field-rule door"


@pytest.mark.parametrize(
    ("folder", "filename", "source"),
    [
        # A route of the CRM, the Field map routes included.
        ("routes/crm", "field_rules.py",
         "from gateway.routes.crm.core import apply_field_rule_from_source\n"),
        ("routes/crm", "records.py",
         "from gateway.routes.crm import core\n\nasync def go(db):\n"
         "    await core.apply_field_rule_from_source(db, 't', 'c', 'zoho', None)\n"),
        ("agents", "tool.py",
         "import importlib\n\nF = getattr(importlib.import_module('x'), "
         "'apply_field_rule_from_source')\n"),
        # A folder that is only CALLED crm_sync is not the sync package.
        ("elsewhere/crm_sync", "engine.py",
         "from gateway.routes.crm.core import apply_field_rule_from_source as a\n"),
    ],
)
def test_the_field_rule_fence_refuses_any_other_caller(
    tmp_path: Path, folder: str, filename: str, source: str,
) -> None:
    """Red-first proof for the CRM-Q7a door."""
    package = tmp_path / folder
    package.mkdir(parents=True)
    (package / filename).write_text(source, encoding="utf-8")
    assert violations((tmp_path,), names=(RULE_NAME,)), (folder, filename)


def test_settings_does_name_the_start_over() -> None:
    """The exception is used, so it is not dead text in the allowlist."""
    (settings,) = ALLOWED_CALLERS[RESET_NAME]
    tree = ast.parse(settings.read_text(encoding="utf-8"))
    assert _names_it(tree, (RESET_NAME,))
    assert violations((settings.parent,), DEFINER, names=(RESET_NAME,)) == []


def test_the_definer_and_the_scan_roots_exist() -> None:
    """A fence over a path that moved scans nothing and passes."""
    assert DEFINER.is_file()
    definer_text = DEFINER.read_text(encoding="utf-8")
    assert "async def upsert_from_source" in definer_text
    assert "async def delete_from_source" in definer_text
    assert "async def set_pipeline_stages_from_source" in definer_text
    assert "async def upsert_module_records_from_source" in definer_text
    assert "async def delete_module_records_from_source" in definer_text
    assert "async def reset_crm_from_admin" in definer_text
    assert "async def apply_field_rule_from_source" in definer_text
    assert SYNC.is_dir() and ALLOWED_CALLERS[RULE_NAME]
    assert GATEWAY.is_dir() and (GATEWAY / "crm_sync").is_dir()
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
        "from gateway.routes.crm.core import upsert_module_records_from_source\n",
        "from gateway.routes.crm import core\n\nasync def go(db):\n"
        "    await core.delete_module_records_from_source(db, 'zoho', 'Products', [])\n",
    ],
)
def test_the_fence_catches_every_spelling(tmp_path: Path, source: str) -> None:
    """Red-first proof: a planted import in a synthetic package is found."""
    package = tmp_path / "routes" / "planted"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "bad.py").write_text(source, encoding="utf-8")
    assert violations((tmp_path / "routes",)), source


@pytest.mark.parametrize(
    ("folder", "filename", "source"),
    [
        # The sync package is outside the routes, and still refused.
        ("crm_sync", "engine.py",
         "from gateway.routes.crm.core import reset_crm_from_admin\n"),
        ("crm_sync", "scheduler.py",
         "from gateway.routes.crm import core\n\nasync def go(db):\n"
         "    await core.reset_crm_from_admin(db, 'o')\n"),
        # Another route file of the CRM.
        ("routes/crm", "records.py",
         "from .core import reset_crm_from_admin as wipe\n"),
        # A file CALLED settings.py in another place is not the allowed one.
        ("routes/projects", "settings.py",
         "from gateway.routes.crm.core import reset_crm_from_admin\n"),
        ("agents", "tool.py",
         "import importlib\n\nF = getattr(importlib.import_module('x'), "
         "'reset_crm_from_admin')\n"),
    ],
)
def test_the_start_over_fence_refuses_any_other_caller(
    tmp_path: Path, folder: str, filename: str, source: str,
) -> None:
    """Red-first proof for CRM-X0 done when 7."""
    package = tmp_path / folder
    package.mkdir(parents=True)
    (package / filename).write_text(source, encoding="utf-8")
    assert violations((tmp_path,), names=(RESET_NAME,)), (folder, filename)


def test_the_allowed_caller_is_exact_and_for_one_name(tmp_path: Path) -> None:
    """The exception names one path and one function, never a folder."""
    settings = tmp_path / "settings.py"
    settings.write_text(
        "from gateway.routes.crm.core import reset_crm_from_admin\n"
        "from gateway.routes.crm.core import upsert_from_source\n",
        encoding="utf-8",
    )
    allowed = {RESET_NAME: frozenset({settings})}
    found = violations((tmp_path,), allowed=allowed)
    assert found == [f"{settings}:2"]


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
