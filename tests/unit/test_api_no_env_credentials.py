"""API connections: no API module reads a credential from the environment (AST).

Spec: ``project-docs/specs/api_connections.md`` §2.8, point 4, and §5. IN-3
(task 3176) ends the process-global credentials of
``acb_skills.integrations``. The API path does not wait for it, because no
API module calls that package or reads ``os.environ``. A credential reaches a
call only as an argument, from the sealed row in the tenant session.

The fence parses each API module and refuses:

* an import of ``acb_skills.integrations``, in any form;
* an import of ``os`` or of a name from it, and any ``environ`` or
  ``getenv`` name or attribute;
* an ``import`` inside a function that does either of the above.

Mutation: add ``import os`` or ``from acb_skills.integrations import
credential`` to ``routes/api_connections.py``, and
``test_no_api_module_reads_the_environment_or_the_integrations_package``
fails. ``test_the_checker_finds_each_form`` holds the checker itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"

#: Each module of the API path. A new API module goes in this list.
API_MODULES: tuple[Path, ...] = (
    *sorted((GATEWAY / "connections" / "api").glob("*.py")),
    GATEWAY / "routes" / "api_connections.py",
    GATEWAY / "routes" / "integrations_api_connect.py",
)

_ENV_NAMES = frozenset({"environ", "getenv", "environb", "getenvb", "putenv", "unsetenv"})


def problems(source: str) -> list[str]:
    """Each place in *source* that breaks the rule of §2.8, point 4."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    found.append(f"line {node.lineno}: import os")
                if alias.name.startswith("acb_skills.integrations"):
                    found.append(f"line {node.lineno}: import acb_skills.integrations")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if module == "os" or module.startswith("os."):
                found.append(f"line {node.lineno}: from os import")
            if module.startswith("acb_skills.integrations") or (
                    module == "acb_skills" and "integrations" in names):
                found.append(f"line {node.lineno}: acb_skills.integrations")
            if names & _ENV_NAMES:
                found.append(f"line {node.lineno}: an environment name")
        elif isinstance(node, ast.Attribute) and node.attr in _ENV_NAMES:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _ENV_NAMES:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
            found.append(f"line {node.lineno}: __import__")
    return found


def test_the_module_list_is_whole() -> None:
    names = {p.name for p in API_MODULES}
    assert {"template.py", "connect.py", "call.py", "zoho_books.py", "__init__.py",
            "api_connections.py", "integrations_api_connect.py"} <= names
    for path in API_MODULES:
        assert path.is_file(), path


@pytest.mark.parametrize("path", API_MODULES, ids=lambda p: p.name)
def test_no_api_module_reads_the_environment_or_the_integrations_package(path: Path) -> None:
    assert problems(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("source", [
    "import os",
    "import os.path",
    "from os import environ",
    "from os import getenv as g",
    "import acb_skills.integrations",
    "from acb_skills.integrations import credential",
    "from acb_skills import integrations",
    "x = something.environ['KEY']",
    "def f():\n    import os\n    return os.getenv('K')",
    "mod = __import__('os')",
])
def test_the_checker_finds_each_form(source: str) -> None:
    assert problems(source), source


def test_the_checker_passes_a_clean_module() -> None:
    assert problems("from acb_skills.hidden_text import strip_hidden\nx = 1\n") == []
