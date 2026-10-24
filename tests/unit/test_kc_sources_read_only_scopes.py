"""Fence: a Knowledge source never asks for a write scope and sends no write verb.

Spec ``knowledge_curation.md`` §7.4: "It never asks for a write scope.
``test_kc_sources_read_only_scopes.py`` fails on any scope outside the
allowlist." WS-61 KC-1a. R7 names this file as the fence of that rule.

Three scans over ``gateway/kb_sources/``, by AST and by string:

1. **Every scope string.** A string constant that is a Google or Microsoft
   scope URL, a Drive short scope, or a word of a scope list is checked
   against :data:`ALLOWLIST`: ``drive.readonly``, ``openid``, ``email`` and
   ``profile``, in their short and their URL form. ``drive``, ``drive.file``,
   ``drive.metadata`` and any scope URL that ends in a write verb fail.
2. **Every write verb.** A ``.post(``, ``.put(``, ``.patch(`` or ``.delete(``
   call fails, except the ``.post(`` of the token and revoke calls in
   ``google_drive/auth.py``. A verb passed as data (``"PUT"``, ``"PATCH"``,
   ``"DELETE"``) fails anywhere, and ``"POST"`` fails outside ``http.py``, the
   one door, which owns the form POST.
3. **The consent URL of the route.** ``POST /knowledge/sources/google_drive/
   connect`` answers a URL whose ``scope`` holds exactly the scopes of
   ``auth.SCOPES``, each one on the allowlist, ``drive.readonly`` among them,
   and no ``include_granted_scopes``.

The self-tests plant each forbidden form in a copy of the tree, as
``test_crm_sources_no_global_creds.py`` does, which proves the scan finds it.
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "apps" / "services" / "gateway" / "gateway" / "kb_sources"

_GOOGLE_SCOPE_URL = "https://www.googleapis.com/auth/"

#: Every scope a source may name, short and URL form.
ALLOWLIST = frozenset({
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
})

#: The short Drive scope words. ``drive.readonly`` is the only one allowed.
_DRIVE_SHORT = re.compile(r"^drive(\.[a-z_.]+)?$")
#: A host name such as ``drive.google.com`` is not a short scope.
_HOST = re.compile(r"\.(com|net|org)$")
#: A scope URL of Google or Microsoft.
_SCOPE_URL = re.compile(
    r"^https://(www\.googleapis\.com/auth/|graph\.microsoft\.com/)[A-Za-z0-9_./-]+$")
#: A scope URL or word that ends in a write verb.
_WRITE_TAIL = re.compile(
    r"(\.|/)(write|readwrite|manage|full|file|upload|create|edit|modify|insert|delete)$",
    re.IGNORECASE)

#: The one file that may POST, and its targets.
POST_ALLOWED_FILE = "google_drive/auth.py"
TOKEN_PATH_SUFFIXES = ("oauth2.googleapis.com/token", "oauth2.googleapis.com/revoke")
#: The one file that may name ``"POST"`` as data: the form POST of the door.
POST_VERB_FILE = "http.py"
_WRITE_CALLS = frozenset({"put", "patch", "delete"})
_VERB_STRINGS = frozenset({"PUT", "PATCH", "DELETE"})


def _scope_words(value: str) -> list[str]:
    """The scope words a string may hold: itself, or each word of a list."""
    words = value.split()
    return words if len(words) > 1 else [value]


def _scope_finding(word: str) -> str | None:
    if word in ALLOWLIST:
        return None
    if _SCOPE_URL.match(word) or word.startswith(_GOOGLE_SCOPE_URL):
        return f"names the scope {word!r}, which is not on the allowlist"
    if _DRIVE_SHORT.match(word) and not _HOST.search(word):
        return f"names the Drive scope {word!r}, which is not drive.readonly"
    if word.startswith(("Files.", "Sites.")):
        return f"names the scope {word!r}, which is not on the allowlist"
    if _WRITE_TAIL.search(word) and ("googleapis" in word or word.startswith("drive")):
        return f"names {word!r}, which ends in a write verb"
    return None


def _module_strings(tree: ast.AST) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out[node.targets[0].id] = node.value.value
    return out


def _post_ok(call: ast.Call, names: dict[str, str]) -> bool:
    url: ast.expr | None = call.args[0] if call.args else None
    for kw in call.keywords:
        if kw.arg == "url":
            url = kw.value
    value = None
    if isinstance(url, ast.Name):
        value = names.get(url.id)
    elif isinstance(url, ast.Constant) and isinstance(url.value, str):
        value = url.value
    return bool(value) and str(value).endswith(TOKEN_PATH_SUFFIXES)


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the docstring nodes, so prose about a scope is not a finding."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def scan(root: Path) -> list[str]:
    """Every finding under ``root``, as ``<file>:<line>: <what>``."""
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _module_strings(tree)
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in skip:
                    continue
                for word in _scope_words(node.value):
                    what = _scope_finding(word)
                    if what:
                        findings.append(f"{rel}:{line}: {what}")
                if node.value in _VERB_STRINGS or (
                        node.value == "POST" and rel != POST_VERB_FILE):
                    findings.append(f"{rel}:{line}: names the write verb {node.value!r}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in _WRITE_CALLS:
                    findings.append(f"{rel}:{line}: calls .{attr}(")
                if attr == "post" and (rel != POST_ALLOWED_FILE or not _post_ok(node, names)):
                    findings.append(f"{rel}:{line}: calls .post( outside the token and revoke paths")
    return findings


# ── The fence ───────────────────────────────────────────────────────────────


def test_the_tree_holds_the_scope_so_the_scan_is_not_vacuous() -> None:
    from gateway.kb_sources.google_drive import auth

    text = (ROOT / "google_drive" / "auth.py").read_text(encoding="utf-8")
    assert "https://www.googleapis.com/auth/drive.readonly" in text
    assert auth.DRIVE_READONLY in auth.SCOPES


def test_no_source_names_a_write_scope_or_a_write_verb() -> None:
    assert scan(ROOT) == []


def test_the_scope_lists_hold_the_allowlist_only() -> None:
    from gateway.kb_sources.google_drive import auth

    assert set(auth.SCOPES) <= ALLOWLIST
    assert set(auth.ALLOWED_SCOPES) <= ALLOWLIST
    assert auth.DRIVE_READONLY == "https://www.googleapis.com/auth/drive.readonly"


PLANTS = [
    ("google_drive/auth.py", "S = 'https://www.googleapis.com/auth/drive'\n", "drive'"),
    ("google_drive/auth.py", "S = 'https://www.googleapis.com/auth/drive.file'\n", "drive.file"),
    ("google_drive/auth.py", "S = 'https://www.googleapis.com/auth/drive.metadata'\n",
     "drive.metadata"),
    ("google_drive/client.py", "S = 'drive.file'\n", "drive.file"),
    ("google_drive/client.py", "S = 'drive'\n", "'drive'"),
    ("google_drive/client.py", "S = 'drive.metadata'\n", "drive.metadata"),
    ("google_drive/auth.py", "S = 'openid email https://www.googleapis.com/auth/drive'\n",
     "auth/drive'"),
    ("base.py", "S = 'https://www.googleapis.com/auth/gmail.modify'\n", "gmail.modify"),
    ("base.py", "S = 'https://graph.microsoft.com/Files.ReadWrite.All'\n", "Files.ReadWrite"),
    ("base.py", "S = 'Files.ReadWrite.All'\n", "Files.ReadWrite"),
    ("http.py", "S = 'https://www.googleapis.com/auth/drive.appdata'\n", "drive.appdata"),
    ("google_drive/client.py", "def f(h):\n    return h.put('u')\n", ".put("),
    ("google_drive/client.py", "def f(h):\n    return h.patch('u')\n", ".patch("),
    ("google_drive/client.py", "def f(h):\n    return h.delete('u')\n", ".delete("),
    ("google_drive/client.py", "def f(h):\n    return h.post('https://x/files')\n", ".post("),
    ("google_drive/auth.py", "def f(h):\n    return h.post('https://www.googleapis.com/x')\n",
     ".post("),
    ("google_drive/client.py", "V = 'PATCH'\n", "'PATCH'"),
    ("http.py", "V = 'DELETE'\n", "'DELETE'"),
    ("google_drive/client.py", "V = 'POST'\n", "'POST'"),
]


@pytest.mark.parametrize(("rel", "snippet", "expect"), PLANTS)
def test_a_planted_violation_is_found(tmp_path: Path, rel: str, snippet: str,
                                      expect: str) -> None:
    copy = tmp_path / "kb_sources"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert scan(copy) == []
    target = copy / rel
    target.write_text(target.read_text(encoding="utf-8") + "\n\n" + snippet, encoding="utf-8")
    findings = scan(copy)
    assert findings, f"the scan missed {snippet!r}"
    assert any(expect in f and f.startswith(rel) for f in findings), findings


def test_a_docstring_that_names_a_scope_is_not_a_finding(tmp_path: Path) -> None:
    copy = tmp_path / "kb_sources"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = copy / "base.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n\ndef f() -> None:\n    """Never ask https://www.googleapis.com/auth/drive."""\n',
        encoding="utf-8",
    )
    assert scan(copy) == []


# ── The consent URL the route builds ────────────────────────────────────────


def test_the_consent_url_of_the_route_holds_exactly_the_allowlisted_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("gateway.routes.knowledge")
    from acb_auth import UserContext, UserRole, build_access, get_current_user
    from acb_common import get_settings
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.kb_sources.google_drive import auth
    from gateway.routes import knowledge as kb_package
    from gateway.routes.knowledge import sources

    org = "11111111-1111-4111-8111-111111111111"
    settings = get_settings()
    for name, value in (("knowledge_enabled", True), ("knowledge_orgs", "*"),
                        ("knowledge_sources", "*"), ("knowledge_source_google", "*")):
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setattr(sources, "google_client", lambda: auth.OAuthClientConfig(
        client_id="cid.apps.googleusercontent.com", client_secret="s3cret",
        redirect_uri="https://app.test/api/knowledge/sources/google_drive/callback"))
    monkeypatch.setattr(sources, "sign_state", lambda **kw: "signed-state")
    user = UserContext(email="m@kc.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:knowledge"]), organization_id=org)
    app = FastAPI()
    app.include_router(kb_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    answer = TestClient(app).post("/knowledge/sources/google_drive/connect")
    assert answer.status_code == 200, answer.text
    url = answer.json()["url"]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.hostname}{parts.path}" == auth.CONSENT_URL
    query = parse_qs(parts.query)
    granted = set(query["scope"][0].split())
    assert granted == set(auth.SCOPES)
    assert granted <= ALLOWLIST
    assert "https://www.googleapis.com/auth/drive.readonly" in granted
    assert "include_granted_scopes" not in query, "the Drive token must not join the Gmail scopes"
    assert query["access_type"] == ["offline"]
    assert "s3cret" not in url, "the secret never sits in the consent URL"
