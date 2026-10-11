"""Fence: a CRM source reads no global credential and writes nothing upstream.

Spec ``crm_platform.md`` §5.1 and §15, WS-53 CRM-Z1. The rule (R7): no file
under ``gateway/crm_sources/`` reads the settings, the environment or a file on
disk for a credential. The credential comes in through the constructor, and
CRM-Z2 reads it from ``crm_connections``. Mirror v1 reads only (D95.3), so no
file sends a write verb. The one POST is the OAuth token call in
``zoho/auth.py``.

The scan reads the AST, not the text, so a comment or a docstring that names a
forbidden call does not trip it. It refuses the whole of ``acb_common``,
``pydantic_settings``, ``dotenv`` and ``importlib``, and ``__import__``. It
follows each alias of ``os``, so ``import os as _os`` does not hide
``_os.environ``. The self-tests plant each forbidden form in a
copy of the tree, which proves that the scan finds it.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "services" / "gateway" / "gateway" / "crm_sources"

#: The files the slice creates. A missing file makes the scan vacuous.
EXPECTED_FILES = {
    "__init__.py",
    "base.py",
    "registry.py",
    "zoho/__init__.py",
    "zoho/auth.py",
    "zoho/client.py",
}

#: The one file that may call ``.post(``, and the path it must post to.
POST_ALLOWED_FILE = "zoho/auth.py"
TOKEN_PATH_SUFFIX = "/oauth/v2/token"

#: A package that holds or loads settings, or that loads a module by name.
#: ``acb_common`` is refused WHOLE, because ``import acb_common`` reaches
#: ``acb_common.Settings()`` through an attribute that no name rule sees.
_BANNED_MODULES = ("acb_common", "pydantic_settings", "dotenv", "importlib")
_BANNED_NAMES = frozenset({"get_settings", "BaseSettings", "import_module", "__import__"})
_OS_ENV_NAMES = frozenset({"environ", "getenv", "putenv", "environb", "getenvb"})
_FILE_ATTRS = frozenset({"read_text", "write_text", "read_bytes", "write_bytes"})
_WRITE_VERBS = frozenset({"put", "patch", "delete"})
#: A generic send takes the verb as data, so no file may call one.
_GENERIC_SENDS = frozenset({"request", "send", "stream"})
_FORBIDDEN_STRINGS = (".zoho_token_cache", "ingestion.sources.zoho")


def _is_banned(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in _BANNED_MODULES)


def _is_ingestion_zoho(module: str) -> bool:
    return module == "ingestion.sources.zoho" or module.startswith("ingestion.sources.zoho.")


def os_aliases(tree: ast.AST) -> frozenset[str]:
    """Every name that ``import os`` binds in the file, ``import os as _os`` included."""
    names = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    names.add(alias.asname or "os")
    return frozenset(names)


def _check_import(node: ast.Import | ast.ImportFrom) -> list[str]:
    found: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_banned(alias.name) or _is_ingestion_zoho(alias.name):
                found.append(f"imports {alias.name}")
        return found
    module = node.module or ""
    names = {alias.name for alias in node.names}
    if _is_banned(module):
        found.append(f"imports from {module}")
    if _is_ingestion_zoho(module) or (module == "ingestion.sources" and "zoho" in names):
        found.append(f"imports from {module}")
    if module == "os" and "*" in names:
        found.append("imports * from os")
    if module == "os" and names & _OS_ENV_NAMES:
        found.append(f"imports {sorted(names & _OS_ENV_NAMES)} from os")
    if names & _BANNED_NAMES:
        found.append(f"imports {sorted(names & _BANNED_NAMES)} from {module}")
    return found


def _post_target_ok(call: ast.Call) -> bool:
    """True when the URL of a ``.post(`` call ends in the token path."""
    url: ast.expr | None = call.args[0] if call.args else None
    for kw in call.keywords:
        if kw.arg == "url":
            url = kw.value
    if isinstance(url, ast.Constant) and isinstance(url.value, str):
        return url.value.endswith(TOKEN_PATH_SUFFIX)
    if isinstance(url, ast.JoinedStr) and url.values:
        last = url.values[-1]
        return (
            isinstance(last, ast.Constant)
            and isinstance(last.value, str)
            and last.value.endswith(TOKEN_PATH_SUFFIX)
        )
    return False


def _check_call(node: ast.Call, rel: str, os_names: frozenset[str]) -> list[str]:
    func = node.func
    found: list[str] = []
    if isinstance(func, ast.Name):
        if func.id in ("open", "Path", "__import__"):
            found.append(f"calls {func.id}(")
        if (
            func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in os_names
        ):
            found.append("calls getattr( on os")
        return found
    if not isinstance(func, ast.Attribute):
        return found
    attr = func.attr
    if attr in ("open", "Path"):
        found.append(f"calls .{attr}(")
    if attr in _WRITE_VERBS:
        found.append(f"calls .{attr}(")
    if attr in _GENERIC_SENDS:
        found.append(f"calls .{attr}(, which takes the verb as data")
    if attr == "post":
        if rel != POST_ALLOWED_FILE:
            found.append(f"calls .post( outside {POST_ALLOWED_FILE}")
        elif not _post_target_ok(node):
            found.append(f"calls .post( to a URL that does not end in {TOKEN_PATH_SUFFIX}")
    return found


def _check_node(node: ast.AST, rel: str, os_names: frozenset[str]) -> list[str]:
    if isinstance(node, ast.Import | ast.ImportFrom):
        return _check_import(node)
    if isinstance(node, ast.Call):
        return _check_call(node, rel, os_names)
    if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
        return [f"names {node.id}"]
    if isinstance(node, ast.Attribute):
        if node.attr in _BANNED_NAMES:
            return [f"names .{node.attr}"]
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in os_names
            and node.attr in _OS_ENV_NAMES
        ):
            return [f"reads os.{node.attr} (as {node.value.id})"]
        if node.attr in _FILE_ATTRS:
            return [f"names .{node.attr}"]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        found = [f"holds the string {s!r}" for s in _FORBIDDEN_STRINGS if s in node.value]
        # ``getattr(x, "environ")`` after ``x = os`` has no os name in it.
        if node.value in _OS_ENV_NAMES:
            found.append(f"holds the string {node.value!r}")
        return found
    return []


def scan(root: Path) -> list[str]:
    """Every finding under ``root``, as ``<file>:<line>: <what>``."""
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        os_names = os_aliases(tree)
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            findings.extend(f"{rel}:{line}: {what}" for what in _check_node(node, rel, os_names))
    return findings


# ── The fence ───────────────────────────────────────────────────────────────


def test_the_tree_exists_so_the_scan_is_not_vacuous() -> None:
    present = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")}
    assert present >= EXPECTED_FILES, sorted(EXPECTED_FILES - present)


def test_no_source_reads_a_global_credential_or_writes_upstream() -> None:
    assert scan(ROOT) == []


def test_the_token_post_is_present_so_the_post_rule_is_live() -> None:
    """The one allowed POST exists. If it moves, this rule must move with it."""
    tree = ast.parse((ROOT / POST_ALLOWED_FILE).read_text(encoding="utf-8"))
    posts = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "post"
    ]
    assert len(posts) == 1
    assert _post_target_ok(posts[0])


# ── Self-tests: a planted violation makes the scan fail ─────────────────────

PLANTS = [
    ("zoho/client.py", "from acb_common import get_settings\n", "get_settings"),
    ("zoho/client.py", "def f(m):\n    return m.get_settings()\n", "get_settings"),
    ("base.py", "x = get_settings()\n", "get_settings"),
    ("base.py", "import acb_common.settings\n", "acb_common.settings"),
    ("base.py", "from acb_common.settings import Settings\n", "acb_common.settings"),
    ("zoho/auth.py", "import os\nv = os.environ['ZOHO_CLIENT_ID']\n", "os.environ"),
    ("zoho/auth.py", "import os\nv = os.getenv('ZOHO_CLIENT_ID')\n", "os.getenv"),
    ("zoho/auth.py", "from os import environ\n", "from os"),
    ("zoho/client.py", "fh = open('x.json')\n", "open("),
    ("zoho/client.py", "from pathlib import Path\np = Path('x')\n", "Path("),
    ("zoho/client.py", "def f(p):\n    return p.read_text()\n", ".read_text"),
    ("zoho/client.py", "def f(p):\n    p.write_text('t')\n", ".write_text"),
    ("registry.py", "from ingestion.sources.zoho import client\n", "ingestion.sources.zoho"),
    ("registry.py", "import ingestion.sources.zoho.client\n", "ingestion.sources.zoho"),
    ("registry.py", "from ingestion.sources import zoho\n", "ingestion.sources"),
    ("zoho/client.py", "CACHE = '.zoho_token_cache.json'\n", ".zoho_token_cache"),
    ("zoho/client.py", "def f(h):\n    return h.put('u')\n", ".put("),
    ("zoho/client.py", "def f(h):\n    return h.patch('u')\n", ".patch("),
    ("zoho/client.py", "def f(h):\n    return h.delete('u')\n", ".delete("),
    ("zoho/client.py", "def f(h):\n    return h.request('PUT', 'u')\n", ".request("),
    ("zoho/client.py", "def f(h):\n    return h.post('https://a/oauth/v2/token')\n", ".post("),
    ("zoho/auth.py", "def f(h):\n    return h.post('https://a/crm/v2/Deals')\n", ".post("),
    ("zoho/auth.py", "def f(h, u):\n    return h.post(u)\n", ".post("),
    # Fix round 1: each of these passed the first scan.
    ("base.py", "import acb_common\nS = acb_common.Settings()\n", "acb_common"),
    ("base.py", "import acb_common\nS = acb_common.get_settings()\n", "acb_common"),
    (
        "zoho/client.py",
        "from acb_common import settings as cfg\nS = cfg.Settings()\n",
        "acb_common",
    ),
    ("zoho/auth.py", "import os as _os\nv = _os.environ['X']\n", "os.environ"),
    ("zoho/auth.py", "import os as _os\nv = _os.getenv('X')\n", "os.getenv"),
    (
        "zoho/auth.py",
        "from pydantic_settings import BaseSettings\n\n\nclass Z(BaseSettings):\n    x: str = ''\n",
        "pydantic_settings",
    ),
    ("zoho/auth.py", "import pydantic_settings\n", "pydantic_settings"),
    ("zoho/auth.py", "from pydantic import BaseSettings\n", "BaseSettings"),
    ("zoho/auth.py", "import os\nv = getattr(os, 'environ')\n", "getattr("),
    ("zoho/auth.py", "import os\nx = os\nv = getattr(x, 'environ')\n", "'environ'"),
    ("zoho/auth.py", "from os import *\n", "* from os"),
    ("zoho/auth.py", "from os import getenv as g\n", "from os"),
    ("zoho/client.py", "m = __import__('os')\n", "__import__"),
    ("zoho/client.py", "import importlib\nm = importlib.import_module('os')\n", "importlib"),
    ("zoho/client.py", "from importlib import import_module\n", "importlib"),
]


@pytest.mark.parametrize(("rel", "snippet", "expect"), PLANTS)
def test_a_planted_violation_is_found(
    tmp_path: Path,
    rel: str,
    snippet: str,
    expect: str,
) -> None:
    copy = tmp_path / "crm_sources"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert scan(copy) == []
    target = copy / rel
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\n" + snippet,
        encoding="utf-8",
    )
    findings = scan(copy)
    assert findings, f"the scan missed {snippet!r}"
    assert any(expect in f and f.startswith(rel) for f in findings), findings


def test_a_docstring_that_names_a_rule_is_not_a_finding(tmp_path: Path) -> None:
    """The scan reads code, so prose about the rule stays legal."""
    copy = tmp_path / "crm_sources"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "base.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n\ndef f() -> None:\n    """Never call get_settings or os.environ here."""\n',
        encoding="utf-8",
    )
    assert scan(copy) == []
