"""No API module reads a credential from the environment (API-2, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.8 point 4 and §5. IN-3
(task 3176) ends the process-wide credential of ``acb_skills.integrations``.
The API path does not wait for it, so it must never use it: no API module
imports ``acb_skills.integrations``, and none reads ``os.environ`` or
``os.getenv``. An operator key in the environment is never a fallback for an
API connection, at any rung (IN-D7).

The API modules are each file of ``gateway/connections/api/``, each route
file whose name starts with ``integrations_api``, and the call routes of API-3
(``routes/api_connections.py``). A new API module under one of those names
joins the scan with no change here. API-3 also bans ``import os`` and a bare
``environ`` or ``getenv`` name.

Mutation: add ``import os`` and ``os.environ.get("X")`` to
``connections/api/connect.py``, and ``test_no_api_module_reads_the_environment``
fails. The planted cases of ``test_the_fence_sees_each_banned_form`` prove the
walk itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GATEWAY = _REPO / "apps" / "services" / "gateway" / "gateway"


def _api_modules() -> list[Path]:
    found = sorted((_GATEWAY / "connections" / "api").glob("*.py"))
    found += sorted((_GATEWAY / "routes").glob("integrations_api*.py"))
    found += sorted((_GATEWAY / "routes").glob("api_connections*.py"))
    return found


def _offences(source: str) -> list[str]:
    """Each banned form in *source*: the integrations resolver, or the env."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    hits.append("import os")
                if alias.name == "acb_skills.integrations" or alias.name.startswith(
                        "acb_skills.integrations."):
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if module == "acb_skills.integrations" or module.startswith("acb_skills.integrations."):
                hits.append(f"from {module}")
            if module == "acb_skills" and "integrations" in names:
                hits.append("from acb_skills import integrations")
            if module == "os" and names & {"environ", "getenv", "environb", "getenvb"}:
                hits.append("from os import environ")
        elif isinstance(node, ast.Attribute) and node.attr in (
                "environ", "getenv", "environb", "getenvb"):
            hits.append(f".{node.attr}")
        elif isinstance(node, ast.Name) and node.id in (
                "environ", "getenv", "environb", "getenvb"):
            hits.append(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (
                node.func.id == "__import__"):
            hits.append("__import__")
    return hits


def test_the_scan_sees_the_api_modules() -> None:
    names = {p.name for p in _api_modules()}
    assert {"template.py", "zoho_books.py", "connect.py", "call.py", "__init__.py",
            "integrations_api_connect.py", "api_connections.py"} <= names, names


@pytest.mark.parametrize("path", _api_modules(), ids=lambda p: p.name)
def test_no_api_module_reads_the_environment(path: Path) -> None:
    hits = _offences(path.read_text(encoding="utf-8"))
    assert hits == [], f"{path.relative_to(_REPO)}: {hits}"


@pytest.mark.parametrize("source", [
    "import acb_skills.integrations\n",
    "import acb_skills.integrations.resolve as r\n",
    "from acb_skills import integrations\n",
    "from acb_skills.integrations import credential\n",
    "import os\nKEY = os.environ.get('ZOHO')\n",
    "import os\nKEY = os.getenv('ZOHO')\n",
    "from os import environ\n",
    "from os import getenv as g\n",
    "m = __import__('os')\n",
    "import os\n",
    "import os.path\n",
    "x = environ['KEY']\n",
    "def f():\n    import os\n    return os.getenv('K')\n",
])
def test_the_fence_sees_each_banned_form(source: str) -> None:
    assert _offences(source), source


def test_the_fence_passes_a_clean_module() -> None:
    assert _offences("from acb_skills.untrusted import frame\nimport json\n") == []
