"""D80 fence — the file importer opens no network connection.

Spec: ``project-docs/specs/project_import.md`` §2 and §8 · decision **D80**.

D52.2 deleted the ClickUp importers partly because each was "a live credential
dependency". D80 allows a file importer on the condition that it reads an
uploaded file and nothing else. A URL inside an export is data, and the
importer never fetches it (§6.8). This test fails if any module under
``routes/projects/importer/`` imports a network client.

Reversing this is an owner decision recorded in ``work_plan.md`` §3. The
correct response to this test failing is to read D80, not to edit this file.
"""

from __future__ import annotations

import ast
import pathlib

IMPORTER = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps"
    / "services"
    / "gateway"
    / "gateway"
    / "routes"
    / "projects"
    / "importer"
)

#: A top-level module name that means a network client.
NETWORK = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "socket",
        "ssl",
        "websockets",
        "ftplib",
        "smtplib",
        "paramiko",
        "boto3",
        "botocore",
        "imaplib",
        "poplib",
        "telnetlib",
        "xmlrpc",
        # A way round a literal import, or out of the process.
        "importlib",
        "subprocess",
        "multiprocessing",
        "ctypes",
    }
)
#: A dotted module that means a network client, inside a harmless package.
NETWORK_DOTTED = frozenset({"urllib.request", "http.client", "asyncio.streams"})
#: An attribute call that opens a connection whatever the import looked like,
#: for example ``import urllib`` then ``urllib.request.urlopen(...)``.
NETWORK_ATTRS = frozenset({"urlopen", "open_connection", "create_connection", "getaddrinfo"})

#: The only modules OUTSIDE this package that an importer module may import
#: from the repo. The scanner reads one file at a time and cannot follow an
#: import into a helper that reaches the network, so each entry here is a
#: reviewed decision. The writer (I-3) adds the Projects core helpers here,
#: and its reviewer checks that none of them opens a connection.
ALLOWED_REPO_IMPORTS: frozenset[str] = frozenset()
REPO_ROOTS = ("gateway", "acb_common")
OWN_PACKAGE = "gateway.routes.projects.importer"


def network_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "__import__":
            names = [
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
        elif isinstance(node, ast.Attribute) and node.attr in NETWORK_ATTRS:
            found.append(node.attr)
        for name in names:
            if name.split(".")[0] in NETWORK or name in NETWORK_DOTTED:
                found.append(name)
    return found


def repo_imports(source: str) -> list[str]:
    """Modules from this repo, outside the importer package, that a file imports."""
    found = []
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module]
        for module in modules:
            if module.split(".")[0] in REPO_ROOTS and not module.startswith(OWN_PACKAGE):
                found.append(module)
    return found


def test_the_scanner_sees_each_form_of_import() -> None:
    """Verified-red: the scanner catches every form, so an empty result
    below means something."""
    assert network_imports("import httpx") == ["httpx"]
    assert network_imports("from urllib import request") == ["urllib.request"]
    assert network_imports("from urllib.request import urlopen") == ["urllib.request"]
    assert network_imports("import http.client as c") == ["http.client"]
    assert network_imports("__import__('socket')") == ["socket"]
    assert network_imports("import importlib") == ["importlib"]
    assert network_imports("import subprocess") == ["subprocess"]
    assert network_imports("import urllib\nurllib.request.urlopen('x')") == ["urlopen"]
    assert network_imports("import asyncio\nasyncio.open_connection('h', 1)") == ["open_connection"]
    assert network_imports("import json, csv\nfrom urllib.parse import urlparse") == []


def test_the_repo_import_scanner_sees_a_reach_outside() -> None:
    assert repo_imports("from gateway.routes.projects import core") == ["gateway.routes.projects"]
    assert repo_imports("import acb_common.db") == ["acb_common.db"]
    assert repo_imports("from gateway.routes.projects.importer.bundle import Task") == []
    assert repo_imports("from pydantic import BaseModel") == []


def test_every_repo_import_outside_the_package_is_reviewed() -> None:
    """A helper outside this package could reach the network where the
    scanner above cannot see it. Each one must be on the reviewed list."""
    reached = {
        module
        for path in sorted(IMPORTER.rglob("*.py"))
        for module in repo_imports(path.read_text(encoding="utf-8"))
    }
    assert reached <= ALLOWED_REPO_IMPORTS, (
        f"D80: review these for network reach, then add them to ALLOWED_REPO_IMPORTS — "
        f"{sorted(reached - ALLOWED_REPO_IMPORTS)}"
    )


def test_the_importer_package_exists() -> None:
    assert (IMPORTER / "__init__.py").is_file()
    assert len(list(IMPORTER.rglob("*.py"))) >= 3


def test_no_importer_module_imports_a_network_client() -> None:
    offenders = {
        str(path.relative_to(IMPORTER)): found
        for path in sorted(IMPORTER.rglob("*.py"))
        if (found := network_imports(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}, f"D80: the importer must not reach the network — {offenders}"
