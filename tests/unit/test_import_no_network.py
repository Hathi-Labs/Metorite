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
    }
)
#: A dotted module that means a network client, inside a harmless package.
NETWORK_DOTTED = frozenset({"urllib.request", "http.client", "asyncio.streams"})


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
        for name in names:
            if name.split(".")[0] in NETWORK or name in NETWORK_DOTTED:
                found.append(name)
    return found


def test_the_scanner_sees_each_form_of_import() -> None:
    """Verified-red: the scanner catches every form, so an empty result
    below means something."""
    assert network_imports("import httpx") == ["httpx"]
    assert network_imports("from urllib import request") == ["urllib.request"]
    assert network_imports("from urllib.request import urlopen") == ["urllib.request"]
    assert network_imports("import http.client as c") == ["http.client"]
    assert network_imports("__import__('socket')") == ["socket"]
    assert network_imports("import json, csv\nfrom urllib.parse import urlparse") == []


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
