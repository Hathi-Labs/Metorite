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
* ``test_the_tree_names_only_the_two_gmail_scopes`` reads each file that git
  knows, except ``tests/``, ``project-docs/`` and ``docs/``. The only hits it
  allows are the two lines of ``GMAIL_SCOPES``, and the Drive scope lists of
  the one Google client (WS-61 KC-1a, D112: ``DRIVE_READONLY`` and
  ``ALLOWED_SCOPES`` in ``kb_sources/google_drive/auth.py``). Those lists have
  their own fence, ``test_kc_sources_read_only_scopes.py``, which refuses any
  scope that is not read only.
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

#: The walk reads every file that git knows: each tracked file, and each
#: untracked file that `.gitignore` does not exclude. So a new file is read
#: before its first commit. No folder name is skipped. Fix round 1: a
#: folder-name skip list dropped `workbench/control_plane/src/app/build/`.
#:
#: Only these top-level folders are not read, each for a reason:
EXCLUDED_ROOTS = {
    # The fences name the scopes, as the expected value of a test.
    "tests": "the tests name the scopes to check them",
    # The plan and the specs quote the scopes to say what the app asks for.
    "project-docs": "the specs quote the scopes",
    # Engineering notes, not code that runs.
    "docs": "engineering notes quote the scopes",
}


def _git_files() -> list[str]:
    """Each file that git knows, as a path relative to the repo."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=REPO, capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        # Never a skip: a fence that cannot read the tree has proved nothing.
        raise AssertionError(f"the fence needs git to list the tree: {exc}") from exc
    return [p for p in out.decode("utf-8").split("\0") if p]

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


#: WS-61 KC-1a (D112): the Drive source uses the one Google client, so its
#: scope lists are the second place a Google scope may appear. Only the two
#: assignments below, by AST, so a Drive scope anywhere else still fails.
DRIVE_SCOPE_FILE = (REPO / "apps" / "services" / "gateway" / "gateway" / "kb_sources"
                    / "google_drive" / "auth.py")
DRIVE_SCOPE_NAMES = ("DRIVE_READONLY", "ALLOWED_SCOPES")
#: The Google scope URLs that those two assignments hold: drive.readonly and
#: the two URL forms of the identity scopes.
DRIVE_SCOPE_URLS = 3


def _drive_scope_lines() -> set[int]:
    """The lines of the two Drive scope assignments of the one Google client."""
    tree = ast.parse(DRIVE_SCOPE_FILE.read_text(encoding="utf-8"))
    lines: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id in DRIVE_SCOPE_NAMES for t in targets):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    assert lines, "the Drive scope lists moved out of kb_sources/google_drive/auth.py"
    return lines


def _walk() -> list[Path]:
    files: list[Path] = []
    for rel in _git_files():
        if rel.split("/", 1)[0] in EXCLUDED_ROOTS:
            continue
        path = REPO / rel
        # A path that git lists and the disk does not hold is a deletion
        # that is not committed yet.
        if path.is_file():
            files.append(path)
    # The companion: a walk that reads almost nothing proves nothing.
    assert len(files) > 1000, f"the walk read only {len(files)} files"
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
    drive_file, drive_lines = DRIVE_SCOPE_FILE.resolve(), _drive_scope_lines()
    hits = _scope_hits()
    stray = [
        f"{path.relative_to(REPO)}:{number}: {line}"
        for path, number, line in hits
        if not (path == scope_file and number in scope_range)
        and not (path == drive_file and number in drive_lines)
    ]
    assert stray == [], (
        "a Google scope string outside GMAIL_SCOPES and the Drive scope lists. "
        "The Gmail app asks for one set of scopes (D-EM-31, EM-G11b):\n"
        + "\n".join(stray)
    )
    # The companion: the walk must find the real lines, or it reads nothing
    # and passes for that reason.
    gmail_hits = [h for h in hits if h[0] == scope_file]
    drive_hits = [h for h in hits if h[0] == drive_file]
    assert len(gmail_hits) == len(D_EM_31_SCOPES), hits
    assert len(drive_hits) == DRIVE_SCOPE_URLS, drive_hits


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


def test_the_agent_catalog_declares_no_retired_gmail_id() -> None:
    import json

    catalog = json.loads((REPO / "agent_registry.json").read_text(encoding="utf-8"))
    declared = {
        (entry.get("name"), service)
        for entry in catalog
        for key in ("integrations", "optional_integrations")
        for service in entry.get(key) or []
        if service in RETIRED_IDS
    }
    assert declared == set()


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
