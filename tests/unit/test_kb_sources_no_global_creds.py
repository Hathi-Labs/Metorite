"""Fence: a Knowledge source reads no global credential and writes nothing upstream.

Spec ``knowledge_base.md`` §7.5 and ``knowledge_curation.md`` §7.4 and §19,
WS-61 KC-1a (was KB-7a). A COPY of ``test_crm_sources_no_global_creds.py``,
with ``ROOT`` at ``gateway/kb_sources/``. The rule (R7): no file under
``gateway/kb_sources/`` reads the settings, the environment or a file on disk
for a credential. The app credential comes in through the constructor (the
route reads ``oauth_app("gmail")``), and the member's token comes in as an
argument, from ``integration_connections`` through ``connections/store.py``.
A source reads only, so no file sends a write verb. The only POSTs are the
OAuth token calls and the revoke call in ``google_drive/auth.py``.

The scan reads the AST, not the text, so a comment or a docstring that names a
forbidden call does not trip it. It refuses the whole of ``acb_common``,
``pydantic_settings``, ``dotenv``, ``importlib`` and ``email_ingestion``, and
``__import__``. It follows each alias of ``os``, so ``import os as _os`` does
not hide ``_os.environ``. A ``.post(`` to a module constant (``TOKEN_URL``)
resolves to the value of that constant. The self-tests plant each forbidden
form in a copy of the tree, which proves that the scan finds it.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "services" / "gateway" / "gateway" / "kb_sources"

#: The files the slice creates. A missing file makes the scan vacuous.
EXPECTED_FILES = {
    "__init__.py",
    "base.py",
    "http.py",
    "registry.py",
    "google_drive/__init__.py",
    "google_drive/auth.py",
    "google_drive/client.py",
}

#: The one file that may call ``.post(``, and the paths it must post to.
POST_ALLOWED_FILE = "google_drive/auth.py"
#: The token call (code and refresh) and the revoke call of the one Google client.
TOKEN_PATH_SUFFIXES = ("oauth2.googleapis.com/token", "oauth2.googleapis.com/revoke")

#: A package that holds or loads settings, or that loads a module by name.
#: ``acb_common`` is refused WHOLE, because ``import acb_common`` reaches
#: ``acb_common.Settings()`` through an attribute that no name rule sees.
#: ``email_ingestion`` is refused because ``oauth_app`` reads the settings:
#: the ROUTE reads the client, and the adapter takes it in.
_BANNED_MODULES = ("acb_common", "pydantic_settings", "dotenv", "importlib", "email_ingestion")
_BANNED_NAMES = frozenset(
    {"get_settings", "BaseSettings", "import_module", "__import__", "oauth_app"})
_OS_ENV_NAMES = frozenset({"environ", "getenv", "putenv", "environb", "getenvb"})
_FILE_ATTRS = frozenset({"read_text", "write_text", "read_bytes", "write_bytes"})
_WRITE_VERBS = frozenset({"put", "patch", "delete"})
#: A generic send takes the verb as data, so no file may call one.
_GENERIC_SENDS = frozenset({"request", "send", "stream"})
_FORBIDDEN_STRINGS = ("token.json", "credentials.json", ".google_token_cache")


def _is_banned(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in _BANNED_MODULES)


def os_aliases(tree: ast.AST) -> frozenset[str]:
    """Every name that ``import os`` binds in the file, ``import os as _os`` included."""
    names = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    names.add(alias.asname or "os")
    return frozenset(names)


def _module_strings(tree: ast.AST) -> dict[str, str]:
    """Each ``NAME = "text"`` at the top of a module, so a ``.post(`` to a
    named constant (``TOKEN_URL``) resolves to its value."""
    out: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out[node.targets[0].id] = node.value.value
    return out


def _check_import(node: ast.Import | ast.ImportFrom) -> list[str]:
    found: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_banned(alias.name):
                found.append(f"imports {alias.name}")
        return found
    module = node.module or ""
    names = {alias.name for alias in node.names}
    if _is_banned(module):
        found.append(f"imports from {module}")
    if module == "os" and "*" in names:
        found.append("imports * from os")
    if module == "os" and names & _OS_ENV_NAMES:
        found.append(f"imports {sorted(names & _OS_ENV_NAMES)} from os")
    if names & _BANNED_NAMES:
        found.append(f"imports {sorted(names & _BANNED_NAMES)} from {module}")
    return found


def _post_target_ok(call: ast.Call, names: dict[str, str] | None = None) -> bool:
    """True when the URL of a ``.post(`` call ends in the token path or the revoke path."""
    url: ast.expr | None = call.args[0] if call.args else None
    for kw in call.keywords:
        if kw.arg == "url":
            url = kw.value
    if isinstance(url, ast.Name) and names and url.id in names:
        return names[url.id].endswith(TOKEN_PATH_SUFFIXES)
    if isinstance(url, ast.Constant) and isinstance(url.value, str):
        return url.value.endswith(TOKEN_PATH_SUFFIXES)
    if isinstance(url, ast.JoinedStr) and url.values:
        last = url.values[-1]
        return (
            isinstance(last, ast.Constant)
            and isinstance(last.value, str)
            and last.value.endswith(TOKEN_PATH_SUFFIXES)
        )
    return False


def _check_call(
    node: ast.Call, rel: str, os_names: frozenset[str], names: dict[str, str],
) -> list[str]:
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
        elif not _post_target_ok(node, names):
            found.append(f"calls .post( to a URL that does not end in {TOKEN_PATH_SUFFIXES}")
    return found


def _check_node(
    node: ast.AST, rel: str, os_names: frozenset[str], names: dict[str, str],
) -> list[str]:
    if isinstance(node, ast.Import | ast.ImportFrom):
        return _check_import(node)
    if isinstance(node, ast.Call):
        return _check_call(node, rel, os_names, names)
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
        names = _module_strings(tree)
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            findings.extend(
                f"{rel}:{line}: {what}" for what in _check_node(node, rel, os_names, names))
    return findings


# ── The fence ───────────────────────────────────────────────────────────────


def test_the_tree_exists_so_the_scan_is_not_vacuous() -> None:
    present = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")}
    assert present >= EXPECTED_FILES, sorted(EXPECTED_FILES - present)


def test_no_source_reads_a_global_credential_or_writes_upstream() -> None:
    assert scan(ROOT) == []


def test_the_token_post_is_present_so_the_post_rule_is_live() -> None:
    """The allowed POSTs exist. If they move, this rule must move with them."""
    tree = ast.parse((ROOT / POST_ALLOWED_FILE).read_text(encoding="utf-8"))
    names = _module_strings(tree)
    posts = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "post"
    ]
    assert len(posts) == 3, "the code exchange, the refresh and the revoke"
    assert all(_post_target_ok(p, names) for p in posts)


# ── Self-tests: a planted violation makes the scan fail ─────────────────────

PLANTS = [
    ("google_drive/client.py", "from acb_common import get_settings\n", "get_settings"),
    ("google_drive/client.py", "def f(m):\n    return m.get_settings()\n", "get_settings"),
    ("base.py", "x = get_settings()\n", "get_settings"),
    ("base.py", "import acb_common.settings\n", "acb_common.settings"),
    ("base.py", "from acb_common.settings import Settings\n", "acb_common.settings"),
    ("google_drive/auth.py", "import os\nv = os.environ['GMAIL_OAUTH_CLIENT_ID']\n",
     "os.environ"),
    ("google_drive/auth.py", "import os\nv = os.getenv('GMAIL_OAUTH_CLIENT_ID')\n",
     "os.getenv"),
    ("google_drive/auth.py", "from os import environ\n", "from os"),
    ("google_drive/client.py", "fh = open('x.json')\n", "open("),
    ("google_drive/client.py", "from pathlib import Path\np = Path('x')\n", "Path("),
    ("google_drive/client.py", "def f(p):\n    return p.read_text()\n", ".read_text"),
    ("google_drive/client.py", "def f(p):\n    p.write_text('t')\n", ".write_text"),
    ("registry.py", "from email_ingestion.providers import app_credentials\n",
     "email_ingestion"),
    ("registry.py", "import email_ingestion.providers.app_credentials\n", "email_ingestion"),
    ("google_drive/auth.py", "def f(m):\n    return m.oauth_app('gmail')\n", "oauth_app"),
    ("google_drive/client.py", "CACHE = 'token.json'\n", "token.json"),
    ("google_drive/client.py", "def f(h):\n    return h.put('u')\n", ".put("),
    ("google_drive/client.py", "def f(h):\n    return h.patch('u')\n", ".patch("),
    ("google_drive/client.py", "def f(h):\n    return h.delete('u')\n", ".delete("),
    ("google_drive/client.py", "def f(h):\n    return h.request('PUT', 'u')\n", ".request("),
    ("google_drive/client.py", "def f(h):\n    return h.send('x')\n", ".send("),
    (
        "google_drive/client.py",
        "def f(h):\n    return h.post('https://oauth2.googleapis.com/token')\n",
        ".post(",
    ),
    ("http.py", "def f(h):\n    return h.post('https://oauth2.googleapis.com/token')\n",
     ".post("),
    (
        "google_drive/auth.py",
        "def f(h):\n    return h.post('https://www.googleapis.com/drive/v3/files')\n",
        ".post(",
    ),
    ("google_drive/auth.py", "def f(h, u):\n    return h.post(u)\n", ".post("),
    ("google_drive/auth.py", "U = 'https://www.googleapis.com/upload'\n"
     "def f(h):\n    return h.post(U)\n", ".post("),
    # The forms that passed the first CRM scan, kept here too.
    ("base.py", "import acb_common\nS = acb_common.Settings()\n", "acb_common"),
    ("base.py", "import acb_common\nS = acb_common.get_settings()\n", "acb_common"),
    (
        "google_drive/client.py",
        "from acb_common import settings as cfg\nS = cfg.Settings()\n",
        "acb_common",
    ),
    ("http.py", "from acb_common.db import tenant_session\n", "acb_common"),
    ("google_drive/auth.py", "import os as _os\nv = _os.environ['X']\n", "os.environ"),
    ("google_drive/auth.py", "import os as _os\nv = _os.getenv('X')\n", "os.getenv"),
    (
        "google_drive/auth.py",
        "from pydantic_settings import BaseSettings\n\n\nclass Z(BaseSettings):\n    x: str = ''\n",
        "pydantic_settings",
    ),
    ("google_drive/auth.py", "import pydantic_settings\n", "pydantic_settings"),
    ("google_drive/auth.py", "from pydantic import BaseSettings\n", "BaseSettings"),
    ("google_drive/auth.py", "import os\nv = getattr(os, 'environ')\n", "getattr("),
    ("google_drive/auth.py", "import os\nx = os\nv = getattr(x, 'environ')\n", "'environ'"),
    ("google_drive/auth.py", "from os import *\n", "* from os"),
    ("google_drive/auth.py", "from os import getenv as g\n", "from os"),
    ("google_drive/client.py", "m = __import__('os')\n", "__import__"),
    ("google_drive/client.py", "import importlib\nm = importlib.import_module('os')\n",
     "importlib"),
    ("google_drive/client.py", "from importlib import import_module\n", "importlib"),
]


@pytest.mark.parametrize(("rel", "snippet", "expect"), PLANTS)
def test_a_planted_violation_is_found(
    tmp_path: Path,
    rel: str,
    snippet: str,
    expect: str,
) -> None:
    copy = tmp_path / "kb_sources"
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
    copy = tmp_path / "kb_sources"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "base.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n\ndef f() -> None:\n    """Never call get_settings or os.environ here."""\n',
        encoding="utf-8",
    )
    assert scan(copy) == []
