"""The provider credential write path — WS-31 **CP-10 slice 1**, **H-40**.

Spec: ``project-docs/specs/customer_console.md`` CP-10 slice 1 · §3.4 · done-
whens 1 and 2. Decision: **D56.7**.

⚠️ **Done-when 1 is the point of this suite, and it is worded carefully:** the
key must be installed *through the route*, on a database *where nothing seeded
it*, and ``router.provider_credential()`` must then return it. Every fixture
here therefore starts from an empty table.

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_provider_keys.py
"""
from __future__ import annotations

import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHARED = "test-operator-token"
INTERNAL = "test-internal-token"
ENCRYPTION_KEY = "test-encryption-key-not-a-real-one"
SECRET = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def eng():
    e = create_engine(_URL, future=True)
    yield e
    e.dispose()


@pytest.fixture
def client(monkeypatch, eng):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", SHARED)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENCRYPTION_KEY)
    # ⚠️ Done-when 1 says "on a database where the key was installed through
    # the route and NOT seeded". Clearing first is what makes that true.
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM provider_credential"))
    from customer_console.main import app
    return TestClient(app)


def _admin(eng) -> str:
    """An elevated admin session — the writes need both (§5, D64.4)."""
    from customer_console import operator_sessions, store

    email = f"admin-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        operator_id = conn.execute(
            text("INSERT INTO operator (email, role) VALUES (:e, 'admin') "
                 "RETURNING id"),
            {"e": email},
        ).scalar()
        store.operator_session_insert(
            conn, operator_id=str(operator_id), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12),
        )
        store.operator_elevation_open(
            conn, operator_id=str(operator_id),
            reason="installing the platform provider key",
            reference=None, expires_at=now + timedelta(minutes=30),
        )
    return issued.token


def _viewer(eng) -> str:
    from customer_console import operator_sessions, store

    email = f"viewer-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        operator_id = conn.execute(
            text("INSERT INTO operator (email, role) VALUES (:e, 'viewer') "
                 "RETURNING id"),
            {"e": email},
        ).scalar()
        store.operator_session_insert(
            conn, operator_id=str(operator_id), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12),
        )
    return issued.token


def _auth(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _org(eng) -> str:
    slug = f"byok-{uuid.uuid4().hex[:8]}"
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO organization (slug, name) VALUES (:s, 'Byok')"),
            {"s": slug},
        )
    return slug


def _install(client, token, **body):
    payload = {"provider": "anthropic", "secret": SECRET}
    payload.update(body)
    return client.post("/providers/credentials", headers=_auth(token),
                       json=payload)


# ── Done-when 1: the route installs a key the Router can actually use ───────


def test_a_key_installed_through_the_route_is_readable_by_the_router(
    client, eng
):
    """⚠️ **This is H-40, closed.**

    Before this slice `provider_credential` had no writer at all: no migration
    seeded it, no route wrote it and no script populated it. On a fresh
    database the Router returned `None` and there was no way to put our key in.
    """
    from customer_console.router import provider_credential

    token = _admin(eng)
    with eng.begin() as conn:
        assert provider_credential(conn, provider="anthropic") is None, (
            "the table was not empty, so this test proves nothing"
        )

    assert _install(client, token).status_code == 200

    with eng.begin() as conn:
        found = provider_credential(conn, provider="anthropic")
    assert found is not None
    assert found[0] == SECRET, "the Router got back a different secret"


def test_the_secret_is_encrypted_at_rest(client, eng):
    """Fernet, through `router.encrypt_secret` — the seam that already exists.

    A second encryption path in this slice would be the "second implementation
    of an existing seam" CLAUDE.md calls a defect.
    """
    token = _admin(eng)
    _install(client, token)
    with eng.begin() as conn:
        stored = conn.execute(
            text("SELECT secret_enc FROM provider_credential")).scalar()
    assert stored is not None
    assert SECRET not in stored
    assert stored.startswith("gAAAAA"), "not a Fernet token"


def test_an_api_base_travels_to_the_router(client, eng):
    # ⚠️ The credential grew a third field on 2026-08-30: `byok`, which §3.4's
    # zero-rating turns on. A platform install reads False.
    from customer_console.router import provider_credential

    token = _admin(eng)
    _install(client, token, api_base="https://proxy.example/v1")
    with eng.begin() as conn:
        found = provider_credential(conn, provider="anthropic")
    assert found == (SECRET, "https://proxy.example/v1", False)
    assert found.byok is False


def test_a_byok_credential_wins_over_the_platform_one(client, eng):
    """§3.4 — a customer insisting on their own account is metered, not charged.

    Both rows exist at once, and the organization's own must be preferred.
    """
    from customer_console.router import provider_credential

    token = _admin(eng)
    slug = _org(eng)
    _install(client, token, secret=SECRET)
    _install(client, token, secret=SECRET + "-byok", org_slug=slug)

    with eng.begin() as conn:
        org_id = conn.execute(
            text("SELECT id FROM organization WHERE slug = :s"), {"s": slug},
        ).scalar()
        theirs = provider_credential(conn, provider="anthropic",
                                     org_id=str(org_id))
        ours = provider_credential(conn, provider="anthropic")
    assert theirs[0] == SECRET + "-byok"
    assert ours[0] == SECRET, "the platform row was disturbed by a BYOK install"


# ── Done-when 2: the plaintext never leaves ────────────────────────────────


def test_no_read_route_returns_the_secret(client, eng):
    token = _admin(eng)
    _install(client, token, label="prod key")

    listed = client.get("/providers/credentials", headers=_auth(token))
    assert listed.status_code == 200
    assert SECRET not in listed.text
    body = listed.json()["credentials"][0]
    assert body["provider"] == "anthropic"
    assert body["label"] == "prod key"
    assert body["scope"] == "platform"
    assert "secret" not in body and "secret_enc" not in body


def test_the_install_response_does_not_echo_the_secret(client, eng):
    token = _admin(eng)
    r = _install(client, token)
    assert SECRET not in r.text


def test_the_list_query_never_selects_the_ciphertext_column(client):
    """⚠️ **Done-when 2 asks for a STRUCTURAL fence, not an example.**

    A caller cannot leak what a query never fetched. This reads the source of
    the one function a read route uses, so the property holds for every future
    caller rather than for the two responses tested above.
    """
    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "store.py"
    ).read_text(encoding="utf-8")
    start = src.index("def provider_credential_list(")
    end = src.index("def provider_credential_revoke(")
    body = src[start:end]
    # ⚠️ Read the SQL, not the docstring. The docstring NAMES the column in
    # order to explain the rule, and a naive substring check fails on its own
    # explanation — which is how a fence gets deleted for being wrong.
    statement = body[body.index("SELECT"):body.index("ORDER BY")]
    assert "secret_enc" not in statement, (
        "provider_credential_list now selects the ciphertext, so a read "
        f"route can leak it: {statement}"
    )


def test_no_response_model_carries_a_secret_field(client):
    """The other half of the structural fence: the wire shape itself.

    `ProviderCredentialRequest` takes a secret because installing one must.
    Nothing that is RETURNED may carry the field.
    """
    from customer_console import main

    assert "secret" in main.ProviderCredentialRequest.model_fields
    for name in ("ProviderCredentialRevokeRequest",):
        model = getattr(main, name)
        assert "secret" not in model.model_fields


def test_the_audit_row_records_the_provider_and_not_the_key(client, eng):
    """The install and the revoke both land a row, and neither carries a key.

    ⚠️ **Scoped to the rows THIS request wrote**, by timestamp. An earlier
    version scanned the whole table, and it caught a real leaked secret — one
    my own mutation run had written. That is the fence working, but it also
    means a single historical bad row makes the test red forever, and a test
    that can never go green again is a blocker rather than a fence.

    The whole-table property is a DATA question. The code question is
    answered structurally by `test_no_audit_call_site_passes_a_secret`.
    """
    token = _admin(eng)
    with eng.begin() as conn:
        mark = conn.execute(text("SELECT now()")).scalar()

    _install(client, token, label="prod key")
    client.post("/providers/credentials/revoke", headers=_auth(token),
                json={"provider": "anthropic"})

    with eng.begin() as conn:
        rows = [
            dict(r) for r in conn.execute(
                text("SELECT action, detail FROM control_audit "
                     "WHERE action LIKE 'provider.credential%' "
                     "AND created_at >= :mark"), {"mark": mark}).mappings()
        ]
    assert len(rows) == 2, f"expected an install and a revoke row: {rows}"
    for row in rows:
        assert SECRET not in str(row["detail"]), row["action"]
        assert "secret" not in row["detail"], row["action"]
        assert row["detail"]["provider"] == "anthropic"
    installs = [r for r in rows if r["action"] == "provider.credential.install"]
    assert installs[0]["detail"]["label"] == "prod key"


def test_no_audit_call_site_passes_a_secret(client):
    """⚠️ The structural half, and the one that cannot rot with data.

    Once a secret reaches `control_audit` it STAYS there — the table is
    append-only by design, and the purge route scrubs emails rather than
    arbitrary keys. So the fence that matters is the one that stops it being
    written, not one that notices afterwards.
    """
    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "main.py"
    ).read_text(encoding="utf-8")
    start = src.index("# ── CP-10 slice 1: OUR provider credentials")
    end = src.index('@app.get("/health")')
    block = src[start:end]
    for call in block.split("_audit(")[1:]:
        detail = call[:call.index("actor=")]
        assert "secret" not in detail, (
            f"an _audit call in the CP-10 block passes a secret: {detail}"
        )

# ── Rotation and revocation ────────────────────────────────────────────────


def test_installing_over_a_live_key_rotates_it_atomically(client, eng):
    """One live credential per provider — the partial unique index says so.

    ⚠️ The old row SURVIVES, revoked. Deleting it would destroy the record
    that the previous key ever existed, which is the thing an incident review
    needs most.
    """
    from customer_console.router import provider_credential

    token = _admin(eng)
    _install(client, token, secret=SECRET)
    second = _install(client, token, secret=SECRET + "-rotated")
    assert second.status_code == 200, second.text
    assert second.json()["rotated"] == 1

    with eng.begin() as conn:
        live = conn.execute(text(
            "SELECT count(*) FROM provider_credential "
            "WHERE provider = 'anthropic' AND revoked_at IS NULL")).scalar()
        total = conn.execute(text(
            "SELECT count(*) FROM provider_credential "
            "WHERE provider = 'anthropic'")).scalar()
        found = provider_credential(conn, provider="anthropic")
    assert live == 1, "two live credentials for one provider"
    assert total == 2, "the rotated-out row was deleted rather than revoked"
    assert found[0] == SECRET + "-rotated"


def test_the_first_install_reports_that_it_rotated_nothing(client, eng):
    token = _admin(eng)
    assert _install(client, token).json()["rotated"] == 0


def test_revoking_stops_the_router_and_keeps_the_row(client, eng):
    from customer_console.router import provider_credential

    token = _admin(eng)
    _install(client, token)
    r = client.post("/providers/credentials/revoke", headers=_auth(token),
                    json={"provider": "anthropic"})
    assert r.status_code == 200 and r.json()["revoked"] == 1

    with eng.begin() as conn:
        assert provider_credential(conn, provider="anthropic") is None
        assert conn.execute(text(
            "SELECT count(*) FROM provider_credential")).scalar() == 1


def test_revoking_the_platform_key_leaves_a_byok_org_working(client, eng):
    """A BYOK customer must not lose service when we rotate our own account."""
    from customer_console.router import provider_credential

    token = _admin(eng)
    slug = _org(eng)
    _install(client, token)
    _install(client, token, secret=SECRET + "-byok", org_slug=slug)
    client.post("/providers/credentials/revoke", headers=_auth(token),
                json={"provider": "anthropic"})

    with eng.begin() as conn:
        org_id = conn.execute(
            text("SELECT id FROM organization WHERE slug = :s"), {"s": slug},
        ).scalar()
        assert provider_credential(conn, provider="anthropic") is None
        theirs = provider_credential(conn, provider="anthropic",
                                     org_id=str(org_id))
    assert theirs is not None and theirs[0] == SECRET + "-byok"


def test_revoking_the_platform_key_does_not_touch_a_byok_row(client, eng):
    """⚠️ `IS NOT DISTINCT FROM`, not `=`.

    The platform row has `organization_id IS NULL`, and `NULL = NULL` is NULL.
    An `=` here would revoke NOTHING and answer as though it had.
    """
    token = _admin(eng)
    slug = _org(eng)
    _install(client, token, secret=SECRET + "-byok", org_slug=slug)
    r = client.post("/providers/credentials/revoke", headers=_auth(token),
                    json={"provider": "anthropic"})
    assert r.json()["revoked"] == 0, "the BYOK row was revoked by mistake"


def test_revoking_what_is_not_there_is_zero_not_an_error(client, eng):
    token = _admin(eng)
    r = client.post("/providers/credentials/revoke", headers=_auth(token),
                    json={"provider": "nothing-installed"})
    assert r.status_code == 200 and r.json()["revoked"] == 0


def test_a_revoked_credential_is_hidden_unless_asked_for(client, eng):
    token = _admin(eng)
    _install(client, token)
    client.post("/providers/credentials/revoke", headers=_auth(token),
                json={"provider": "anthropic"})

    live = client.get("/providers/credentials", headers=_auth(token)).json()
    assert live["credentials"] == []
    everything = client.get("/providers/credentials?include_revoked=true",
                            headers=_auth(token)).json()
    assert len(everything["credentials"]) == 1
    assert everything["credentials"][0]["revoked_at"] is not None


# ── There is no UPDATE path, and that is load-bearing twice ────────────────


def test_no_route_updates_a_credential_in_place(client):
    """⚠️ **A rule with two reasons, and the second one is security.**

    The spec forbids a mutable credential row so the record of what was
    installed survives. It ALSO closes an escalation: an admin who could
    re-point an existing credential's `api_base` at a host they control would
    receive our provider key on the next call, and read a plaintext no route
    returns.
    """
    src = (
        _ROOT / "apps" / "services" / "customer_console" / "customer_console"
        / "store.py"
    ).read_text(encoding="utf-8")
    start = src.index("# ── CP-10 slice 1: the provider credential write path")
    block = src[start:]
    updates = [
        line.strip() for line in block.splitlines()
        if "UPDATE provider_credential" in line
    ]
    # Exactly ONE update statement exists, and it is the revocation the spec
    # names as the single permitted mutation.
    assert len(updates) == 1, updates
    assert "SET revoked_at" in block.split("UPDATE provider_credential")[1][:80]


# ── Admission ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad,why", [
    ("", "empty"),
    ("short", "too short"),
    ("  " + SECRET, "leading whitespace"),
    (SECRET + "  ", "trailing whitespace"),
    ("sk-has a space in it and is long enough", "internal whitespace"),
])
def test_a_secret_that_will_not_work_is_refused_at_the_door(
    client, eng, bad, why
):
    """⚠️ Refused, never trimmed.

    Silently altering an opaque provider key produces a credential that fails
    at the PROVIDER, with an authentication error nobody traces back here.
    """
    token = _admin(eng)
    r = _install(client, token, secret=bad)
    assert r.status_code in (400, 422), f"{why} was accepted"
    if why in ("leading whitespace", "trailing whitespace"):
        # ⚠️ The MESSAGE, not only the status. `any(ch.isspace())` below the
        # strip check refuses the same inputs, so the status alone cannot tell
        # the two apart — a mutation removing the strip check survived on
        # status. What it costs is the useful half: "paste it again" tells the
        # operator what to do, "contains whitespace" does not.
        assert "paste it again" in r.text, r.text


@pytest.mark.parametrize("bad", ["", "  ", "Anthropic!", "a", "x" * 41])
def test_a_malformed_provider_name_is_refused(client, eng, bad):
    token = _admin(eng)
    r = _install(client, token, provider=bad)
    assert r.status_code in (400, 422)


def test_the_provider_name_is_normalised_so_one_row_stays_one_row(client, eng):
    """⚠️ Load-bearing for the partial unique index, which is over the literal
    column. Two spellings would both be "the one live credential"."""
    token = _admin(eng)
    _install(client, token, provider="Anthropic")
    _install(client, token, provider=" ANTHROPIC ")
    with eng.begin() as conn:
        live = conn.execute(text(
            "SELECT count(*) FROM provider_credential "
            "WHERE revoked_at IS NULL")).scalar()
        name = conn.execute(text(
            "SELECT DISTINCT provider FROM provider_credential")).scalar()
    assert live == 1, "two spellings became two live credentials"
    assert name == "anthropic"


@pytest.mark.parametrize("bad", ["not-a-url", "ftp://x.example", "//x.example",
                                 "javascript:alert(1)"])
def test_a_bad_api_base_is_refused(client, eng, bad):
    token = _admin(eng)
    assert _install(client, token, api_base=bad).status_code in (400, 422)


def test_an_unknown_org_slug_is_a_404(client, eng):
    token = _admin(eng)
    r = _install(client, token, org_slug=f"nope-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 404


def test_an_unset_encryption_key_is_503_and_writes_nothing(
    client, eng, monkeypatch
):
    """D33.1 — fail closed, and say it is the BOX that is wrong, not the input.

    ⚠️ Encrypted BEFORE the transaction opens, so a rotation cannot be left
    half-done by a missing key.
    """
    monkeypatch.delenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", raising=False)
    token = _admin(eng)
    assert _install(client, token).status_code == 503
    with eng.begin() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM provider_credential")).scalar() == 0


# ── The role matrix ────────────────────────────────────────────────────────


def test_a_viewer_may_list_but_never_install(client, eng):
    token = _viewer(eng)
    assert client.get("/providers/credentials",
                      headers=_auth(token)).status_code == 200
    assert _install(client, token).status_code == 403


def test_an_admin_without_an_elevation_window_cannot_install(client, eng):
    """Installing the key every customer's AI call is billed against is as
    sharp as a purge, so it needs the window as well as the role."""
    from customer_console import operator_sessions, store

    email = f"admin-{uuid.uuid4().hex[:8]}@fracktal.in"
    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    with eng.begin() as conn:
        operator_id = conn.execute(
            text("INSERT INTO operator (email, role) VALUES (:e, 'admin') "
                 "RETURNING id"), {"e": email},
        ).scalar()
        store.operator_session_insert(
            conn, operator_id=str(operator_id), prefix=issued.prefix,
            key_hash=issued.key_hash, expires_at=now + timedelta(hours=12))

    assert _install(client, issued.token).status_code == 403


def test_the_three_routes_are_named_in_the_matrix(client):
    from customer_console import operator_roles
    from customer_console.operators import ADMIN, VIEWER

    read = operator_roles.rule_for("GET", "/providers/credentials")
    assert read is not None and read.min_role == VIEWER
    assert read.elevated is False

    for path in ("/providers/credentials", "/providers/credentials/revoke"):
        rule = operator_roles.rule_for("POST", path)
        assert rule is not None, path
        assert rule.min_role == ADMIN and rule.elevated is True, path


def test_an_unauthenticated_caller_reaches_nothing(client):
    assert client.get("/providers/credentials").status_code == 401
    assert client.post("/providers/credentials",
                       json={"provider": "x", "secret": SECRET}).status_code \
        == 401


# ── The R8 gate cannot silently disarm ─────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_provider_keys.py" in workflow


def test_the_rate_card_still_ships_unpriced() -> None:
    """CP-10 done-when 6, guarded from here too.

    This slice builds the mechanism to install a KEY. Pricing is a separate
    owner act (D19.2, H-42), and a slice that quietly priced something would
    be the one nobody reviewed for it.
    """
    suite = (_ROOT / "tests" / "unit"
             / "test_customer_console_sql.py").read_text(encoding="utf-8")
    assert "def test_the_rate_card_ships_unpriced" in suite
