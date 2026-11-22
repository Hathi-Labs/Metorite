"""WS-PKG PKG-5b part 2 — the switch of the package host, with no database.

Spec: ``project-docs/specs/package_host.md`` §10 ("Every slice ships dark
behind ``PACKAGE_HOST_ORGS``"). Code: ``gateway/package_host/flags.py`` and
``gateway/routes/shell/manifest.py``.

a. The default is OFF. ``*`` opens every org, and a listed org opens in any
   case. No org is closed.
b. ``GET /shell/manifest`` answers 404 while the list leaves the org out, and
   it opens no session. The flag check is the first line of the handler.
c. ``flags.py`` is the one reader of ``package_host_orgs``.
d. ``PACKAGE_HOST_ORGS`` is a platform setting that no tenant route may write
   (``acb_common/env_guard.py``).

**R7.** Mutation M1: move the flag check below the ``async with`` in the
handler. ``test_the_flag_check_is_the_first_line`` and
``test_an_org_off_the_list_gets_404_and_no_session`` go red.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
import textwrap
from pathlib import Path

import pytest
from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.env_guard import PLATFORM_ENV_PREFIXES, is_platform_env
from fastapi import HTTPException
from gateway.package_host import flags
from gateway.routes.shell import manifest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[2]
ORG = "11111111-2222-3333-4444-555555555555"


def _user(org: str | None = ORG) -> UserContext:
    return UserContext(email="m@pkg.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:*"]), organization_id=org)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/shell/manifest",
                    "headers": []})


# ── a. The switch ──────────────────────────────────────────────────────────


def test_the_default_is_off(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    assert flags.package_host_allowed(ORG) is False


@pytest.mark.parametrize(("raw", "org", "want"), [
    ("*", ORG, True),
    (ORG.upper(), ORG, True),
    (f" other , {ORG} ", ORG.upper(), True),
    ("other", ORG, False),
    ("*", None, False),
    ("*", "", False),
])
def test_the_list_opens_an_org(monkeypatch, raw: str, org: str | None, want: bool) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", raw)
    assert flags.package_host_allowed(org) is want


# ── b. The route is dark ───────────────────────────────────────────────────


def test_an_org_off_the_list_gets_404_and_no_session(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "other-org")

    def no_session(*_a, **_k):
        raise AssertionError("the route opened a session while it was dark")

    monkeypatch.setattr(manifest, "_tenant_session", no_session)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(manifest.shell_manifest(_request(), _user()))
    assert (exc.value.status_code, exc.value.detail) == (404, "Not found")


def test_a_user_with_no_org_gets_404(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(manifest.shell_manifest(_request(), _user(None)))
    assert exc.value.status_code == 404


def test_the_flag_check_is_the_first_line() -> None:
    src = textwrap.dedent(inspect.getsource(manifest.shell_manifest))
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    body = [s for s in fn.body if not (isinstance(s, ast.Expr)
                                       and isinstance(s.value, ast.Constant))]
    first = body[0]
    assert isinstance(first, ast.If)
    assert "package_host_allowed(user.organization_id)" in ast.unparse(first.test)


# ── c. One reader ──────────────────────────────────────────────────────────


def test_flags_is_the_one_reader_of_the_setting() -> None:
    readers = set()
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts:
                continue
            if re.search(r"\bpackage_host_orgs\b", path.read_text(encoding="utf-8")):
                readers.add(path.relative_to(ROOT).as_posix())
    assert readers == {
        "apps/services/gateway/gateway/package_host/flags.py",
        "packages/acb_common/acb_common/settings.py",
    }


# ── d. No tenant writes it ─────────────────────────────────────────────────


def test_the_setting_is_a_platform_setting() -> None:
    assert "PACKAGE_HOST_" in PLATFORM_ENV_PREFIXES
    assert is_platform_env("PACKAGE_HOST_ORGS") is True
    assert is_platform_env(" package_host_orgs ") is True
