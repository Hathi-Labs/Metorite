"""WS-17 EM-G11b: the Gmail app asks Google for one set of scopes.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.5 (PR-G2).

Google verification asks that the app requests only the scopes it needs. The
live Gmail connect asks for exactly the two scopes of D-EM-31
(``GMAIL_SCOPES``). Two dead paths named other scopes, and EM-G11b removed
both:

* the ``google`` entry of the retired OAuth route (``gmail.readonly``), and
* the service-account Gmail path (``gmail.readonly`` and ``gmail.metadata``),
  with its settings, its two Integrations guides, its key-store map and its
  run-time resolver.

The fences:

* ``test_gmail_scopes_are_exactly_the_two`` pins the list.
* ``test_the_tree_names_only_the_two_gmail_scopes`` walks the shipped code.
  The only hits it allows are the two lines of ``GMAIL_SCOPES``.
* ``test_the_retired_oauth_route_has_no_google_entry`` pins the route table.
* ``test_no_gmail_service_account_setting_or_integration`` and its siblings
  pin each removed part, and that an old env file still loads.

Mutations M1 to M3 of the spec each turn one of these red.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from acb_common.settings import Settings
from email_ingestion.providers import gmail as gmail_provider
from email_ingestion.providers.gmail import GMAIL_SCOPES

REPO = Path(__file__).resolve().parents[2]

#: D-EM-31 (WS-17 EM-G7). The two scopes of the Gmail app, and no third.
D_EM_31_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

#: The two marks of a Google scope string. Each Gmail scope is a
#: ``googleapis.com/auth/`` URL, except the full-access one, which is
#: ``https://mail.google.com/``.
SCOPE_MARKS = ("googleapis.com/auth/", "https://mail.google.com/")

#: The roots that ship code (§12.3.13.5, the fence list).
WALK_ROOTS = (
    "apps",
    "packages",
    "scripts",
    "workbench/control_plane/src",
    "workbench/operator_console/src",
)

#: Directories that hold no code of ours: installs, caches and build output.
SKIP_DIRS = frozenset({
    "node_modules", ".next", "__pycache__", ".venv", "venv", ".turbo",
    "dist", "build", "coverage", ".pytest_cache", ".ruff_cache", ".git",
})

#: The settings of the two removed paths. None may come back.
REMOVED_SETTINGS = (
    "gmail_sa_json_path",
    "gmail_workspace_domain",
    "gmail_default_user",
    "google_client_id",
    "google_client_secret",
    "google_access_token",
    "google_refresh_token",
    "google_token_expiry",
)

#: The env names a box can still hold from before EM-G11b.
OLD_ENV_NAMES = {name.upper(): "old-value" for name in REMOVED_SETTINGS}

RETIRED_IDS = ("gmail", "gmail-send")


def _scope_lines() -> tuple[Path, range]:
    """The file and the line range of the ``GMAIL_SCOPES`` assignment."""
    path = Path(gmail_provider.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "GMAIL_SCOPES" for t in node.targets
        ):
            return path, range(node.lineno, (node.end_lineno or node.lineno) + 1)
    raise AssertionError("GMAIL_SCOPES has no assignment in providers/gmail.py")


def _walk() -> list[Path]:
    files: list[Path] = []
    for root in WALK_ROOTS:
        base = REPO / root
        assert base.is_dir(), f"the walk root {root} is missing"
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(REPO).parts):
                continue
            files.append(path)
    return files


def _scope_hits() -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for path in _walk():
        # `errors="replace"` keeps a binary file from stopping the walk. A
        # scope string is ASCII, so a replaced byte cannot hide one.
        text = path.read_text(encoding="utf-8", errors="replace")
        if not any(mark in text for mark in SCOPE_MARKS):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if any(mark in line for mark in SCOPE_MARKS):
                hits.append((path.resolve(), number, line.strip()))
    return hits


# ── The one set ─────────────────────────────────────────────────────────────


def test_gmail_scopes_are_exactly_the_two() -> None:
    assert GMAIL_SCOPES == D_EM_31_SCOPES


def test_the_tree_names_only_the_two_gmail_scopes() -> None:
    scope_file, scope_range = _scope_lines()
    hits = _scope_hits()
    stray = [
        f"{path.relative_to(REPO)}:{number}: {line}"
        for path, number, line in hits
        if not (path == scope_file and number in scope_range)
    ]
    assert stray == [], (
        "a Google scope string outside GMAIL_SCOPES. The Gmail app asks for "
        "one set of scopes (D-EM-31, EM-G11b):\n" + "\n".join(stray)
    )
    # The companion: the walk must find the two real lines, or it reads
    # nothing and passes for that reason.
    assert len(hits) == len(D_EM_31_SCOPES), hits


# ── The retired OAuth route ─────────────────────────────────────────────────


def test_the_retired_oauth_route_has_no_google_entry() -> None:
    from gateway.routes import oauth

    assert "google" not in oauth._PROVIDERS
    assert set(oauth._PROVIDERS) == {"zoho-crm"}
    for service, provider in oauth._PROVIDERS.items():
        scopes = str(provider.get("scopes", "")).lower()
        assert "gmail" not in scopes, service
        assert "google" not in scopes, service


async def test_the_refresh_of_google_is_nothing() -> None:
    from gateway.routes import oauth

    assert await oauth.refresh_access_token("google") is None


# ── The service-account path ────────────────────────────────────────────────


def test_no_gmail_service_account_setting_or_integration() -> None:
    from acb_llm.key_store import INTEGRATION_ENV_MAP
    from acb_skills import integrations as skills
    from gateway.routes import integrations
    from ingestion.sources.gmail import client

    fields = set(Settings.model_fields)
    assert [f for f in REMOVED_SETTINGS if f in fields] == []

    for service in RETIRED_IDS:
        assert service not in integrations._SETUP_GUIDES, service
        assert service not in integrations._GUIDE_CATEGORIES, service
        assert service not in INTEGRATION_ENV_MAP, service
        assert service not in skills._REGISTRY, service
        assert service not in skills.FIELD_TO_ENV, service
        assert service not in skills.list_registered(), service
        # The ids stay reserved, so a custom integration cannot take one.
        assert service in integrations.RESERVED_SERVICE_IDS, service

    for name in ("_SCOPES", "_service", "fetch_message", "list_history"):
        assert not hasattr(client, name), name
    # The parse helpers stay (test_gmail_normaliser.py).
    for name in ("GmailMessageRaw", "_decode_b64url", "_extract_body", "_header"):
        assert hasattr(client, name), name


def test_the_integrations_status_lists_no_service_account_gmail() -> None:
    from gateway.routes import integrations

    for guide in integrations._SETUP_GUIDES.values():
        keys = {var["key"] for var in guide["env_vars"]}
        assert not keys & {"GMAIL_SA_JSON_PATH", "GMAIL_DEFAULT_USER"}, guide["label"]


def test_a_declared_gmail_service_resolves_to_nothing(monkeypatch) -> None:
    from acb_skills.integrations import build_integrations

    monkeypatch.setenv("GMAIL_SA_JSON_PATH", "/etc/acb/gmail-sa.json")
    monkeypatch.setenv("GMAIL_DEFAULT_USER", "ceo@example.com")
    resolved, unavailable = build_integrations(["gmail"], ["gmail-send"], Settings())
    assert resolved == {}
    assert set(unavailable) == {"gmail", "gmail-send"}
    assert all("not in the IntegrationRegistry" in r for r in unavailable.values())


def test_google_sheets_reads_its_own_key_only(monkeypatch) -> None:
    """The fallback to the Gmail service-account key is gone."""
    from acb_skills.integrations import IntegrationMisconfiguredError, _google_sheets
    from gateway.routes import integrations

    monkeypatch.delenv("GOOGLE_SHEETS_SA_JSON_PATH", raising=False)
    monkeypatch.setenv("GMAIL_SA_JSON_PATH", "/etc/acb/gmail-sa.json")
    settings = Settings(_env_file=None)
    with pytest.raises(IntegrationMisconfiguredError):
        _google_sheets(settings)
    assert integrations._is_configured("google-sheets", settings) is False

    monkeypatch.setenv("GOOGLE_SHEETS_SA_JSON_PATH", "/etc/acb/sheets-sa.json")
    settings = Settings(_env_file=None)
    assert _google_sheets(settings)["sa_json_path"] == "/etc/acb/sheets-sa.json"
    assert integrations._is_configured("google-sheets", settings) is True


# ── An old env file still loads ─────────────────────────────────────────────


def test_settings_load_with_the_old_names_in_the_env_file(tmp_path, monkeypatch) -> None:
    """A box that still holds the removed names starts (`extra="ignore"`)."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{name}={value}\n" for name, value in OLD_ENV_NAMES.items()),
        encoding="utf-8",
    )
    for name in OLD_ENV_NAMES:
        monkeypatch.setenv(name, "old-value")
    settings = Settings(_env_file=str(env_file))
    for field in REMOVED_SETTINGS:
        assert not hasattr(settings, field), field
