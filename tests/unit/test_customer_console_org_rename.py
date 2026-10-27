"""CP-8 · Rename an organization — the Console door ``POST /registry/orgs/rename``.

Spec: ``project-docs/specs/customer_console.md`` CP-8 "Rename an organization"
(owner request, 2026-10-27), done-whens 1-5.

The door writes ``organization.name``, the only name the Console stores. It is
the ``member_admin`` door family: the same capability, the same two arms and
the same derivation seam (``_admin_scheme_context``) as the member doors.

* The OPERATOR arm names the org by ``org_slug``. The operator BFF calls it
  after the tenant door renamed the tenant plane.
* The DEPLOYMENT arm derives the org from placement and membership (R11). The
  gateway's admin rename mirrors onto it after its own commit.

⚠️ **R8.** The subject is one ``UPDATE ... RETURNING`` and a four-table
placement and membership join. A fake would agree with both, so this suite
runs against a real Postgres and skips loudly without one. ``pr-check.yml``
names it, and ``test_this_suite_is_named_in_the_ci_skip_guard`` keeps that
true.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_customer_console_org_rename.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console import store
from customer_console.auth import MEMBER_ADMIN_CAPABILITY
from customer_console.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    apply_ladder,
    ensure_deployment,
    mint_deployment_key,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

TOKEN = "test-operator-token"
INTERNAL = "test-internal-token"
OP = {"Authorization": f"Bearer {TOKEN}"}

#: Where this suite's organizations are born.
BOX_LABEL = "org-rename-suite-box"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _box(db):
    """This suite's deployment row, per test, because another suite empties it."""
    with db.begin() as c:
        ensure_deployment(c, label=BOX_LABEL)


def _deployment_id(db) -> str:
    with db.begin() as c:
        return str(c.execute(
            text("SELECT id FROM deployment WHERE label = :l"), {"l": BOX_LABEL},
        ).scalar_one())


def _key(db, capabilities: list[str] | None = None) -> str:
    with db.begin() as c:
        return mint_deployment_key(
            c, deployment_id=_deployment_id(db), capabilities=capabilities,
        )


@pytest.fixture
def member_key(db):
    """A deployment key on THIS box that carries only ``member_admin``."""
    return _key(db, [MEMBER_ADMIN_CAPABILITY])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _new_org(client) -> dict:
    """Provision an org through the product. Its name starts as ``Dewin``."""
    slug = f"cp8r-{uuid.uuid4().hex[:8]}"
    owner = f"owner@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "Dewin", "owner_email": owner,
        "core_seats": 2, "deployment_label": BOX_LABEL,
    })
    assert r.status_code == 200, r.text
    return {"slug": slug, "owner": owner, "id": r.json()["organization_id"]}


def _row(db, org_id: str) -> dict:
    with db.begin() as c:
        row = c.execute(
            text("SELECT slug, name FROM organization WHERE id = :i"),
            {"i": org_id},
        ).mappings().one()
    return dict(row)


def _audit_rows(db, org_id: str) -> list[dict]:
    with db.begin() as c:
        rows = c.execute(
            text(
                "SELECT actor, action, detail FROM control_audit "
                "WHERE organization_id = :o AND action = 'org.rename' "
                "ORDER BY created_at"
            ),
            {"o": org_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def _rename(client, key: str | None, **body) -> object:
    """Drive the door. ``key=None`` is the operator arm."""
    headers = OP if key is None else {"Authorization": f"Bearer {key}"}
    return client.post("/registry/orgs/rename", headers=headers, json=body)


# ── Done-when 1 and 3 — the operator arm writes the name, never the slug ─────

class TestTheOperatorArm:
    def test_the_operator_sets_the_name_and_the_slug_stays(self, client, db):
        org = _new_org(client)
        r = _rename(client, None, org_slug=org["slug"], name="Shaurrya Tele")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "slug": org["slug"], "name": "Shaurrya Tele",
            "previous_name": "Dewin",
        }
        assert _row(db, org["id"]) == {"slug": org["slug"], "name": "Shaurrya Tele"}

    def test_one_audit_row_records_both_names(self, client, db):
        org = _new_org(client)
        assert _rename(client, None, org_slug=org["slug"],
                       name="Shaurrya Tele").status_code == 200
        rows = _audit_rows(db, org["id"])
        assert len(rows) == 1
        # The shared token is break-glass, and the audit row says so.
        assert rows[0]["actor"] == "breakglass"
        assert rows[0]["detail"] == {"name": "Shaurrya Tele",
                                     "previous_name": "Dewin"}

    def test_a_signed_in_editor_is_named_in_the_audit(self, client, db):
        """The Console half names the staff member (``staff.actor``)."""
        from datetime import UTC, datetime, timedelta

        from customer_console import operator_sessions

        org = _new_org(client)
        email = f"editor-{uuid.uuid4().hex[:8]}@fracktal.in"
        now = datetime.now(UTC)
        issued = operator_sessions.issue(now=now)
        with db.begin() as c:
            op_id = c.execute(
                text("INSERT INTO operator (email, role) VALUES (:e, 'editor') "
                     "RETURNING id"),
                {"e": email},
            ).scalar_one()
            store.operator_session_insert(
                c, operator_id=str(op_id), prefix=issued.prefix,
                key_hash=issued.key_hash, expires_at=now + timedelta(hours=1),
            )
        r = client.post(
            "/registry/orgs/rename",
            headers={"Authorization": f"Bearer {issued.token}"},
            json={"org_slug": org["slug"], "name": "Shaurrya Tele"},
        )
        assert r.status_code == 200, r.text
        assert [row["actor"] for row in _audit_rows(db, org["id"])] == [email]

    def test_a_retry_with_the_same_name_is_safe(self, client, db):
        """The BFF tells the caller a retry is safe. This proves it."""
        org = _new_org(client)
        for _ in range(2):
            r = _rename(client, None, org_slug=org["slug"], name="Shaurrya Tele")
            assert r.status_code == 200, r.text
        assert _row(db, org["id"])["name"] == "Shaurrya Tele"

    def test_an_unknown_slug_is_404_and_writes_nothing(self, client):
        r = _rename(client, None, org_slug=f"nope-{uuid.uuid4().hex[:6]}",
                    name="X")
        assert r.status_code == 404, r.text

    def test_the_operator_must_name_the_org(self, client):
        r = _rename(client, None, name="X")
        assert r.status_code == 400, r.text
        assert "org_slug is required" in r.json()["detail"]

    def test_the_operator_may_not_name_an_actor(self, client):
        org = _new_org(client)
        r = _rename(client, None, org_slug=org["slug"], name="X",
                    actor_email=org["owner"])
        assert r.status_code == 400, r.text


# ── The deployment arm — the gateway admin mirror's door ─────────────────────

class TestTheDeploymentArm:
    def test_the_owner_renames_their_own_org(self, client, db, member_key):
        org = _new_org(client)
        r = _rename(client, member_key, actor_email=org["owner"],
                    name="Shaurrya Tele")
        assert r.status_code == 200, r.text
        assert _row(db, org["id"]) == {"slug": org["slug"], "name": "Shaurrya Tele"}
        rows = _audit_rows(db, org["id"])
        # The deployment arm names the acting admin, never "operator".
        assert [r["actor"] for r in rows] == [org["owner"]]

    def test_a_key_without_member_admin_is_refused_and_told_why(
        self, client, db
    ):
        org = _new_org(client)
        narrow = _key(db)
        r = _rename(client, narrow, actor_email=org["owner"], name="X")
        assert r.status_code == 403, r.text
        assert MEMBER_ADMIN_CAPABILITY in r.json()["detail"]
        assert _row(db, org["id"])["name"] == "Dewin"

    def test_a_deployment_key_naming_an_org_is_400(self, client, db, member_key):
        """R11: the org is the ANSWER on this arm, so naming one is refused."""
        org = _new_org(client)
        r = _rename(client, member_key, actor_email=org["owner"],
                    org_slug=org["slug"], name="X")
        assert r.status_code == 400, r.text
        assert _row(db, org["id"])["name"] == "Dewin"

    def test_a_stranger_is_403(self, client, db, member_key):
        org = _new_org(client)
        r = _rename(client, member_key,
                    actor_email=f"nobody-{uuid.uuid4().hex[:6]}@x.example",
                    name="X")
        assert r.status_code == 403, r.text
        assert _row(db, org["id"])["name"] == "Dewin"

    def test_an_admin_of_two_orgs_on_one_box_is_409(
        self, client, db, member_key
    ):
        """The spec's drift case: the mirror logs this refusal, and nothing
        is written on either org."""
        first = _new_org(client)
        second = _new_org(client)
        with db.begin() as c:
            identity = store.ensure_identity(c, email=first["owner"])
            c.execute(
                text(
                    "INSERT INTO org_membership (organization_id, "
                    "user_identity_id, role, status, joined_at) "
                    "VALUES (:o, :i, 'member', 'active', now())"
                ),
                {"o": second["id"], "i": identity},
            )
        r = _rename(client, member_key, actor_email=first["owner"], name="X")
        assert r.status_code == 409, r.text
        assert _row(db, first["id"])["name"] == "Dewin"
        assert _row(db, second["id"])["name"] == "Dewin"


def _member_actor(db, org_id: str, *, status: str = "active") -> str:
    """A registry ``member`` of *org_id*. Returns the address.

    This is the tenant's second admin: nothing maps a tenant ``org_role``
    onto the registry role (D12), so the Console holds that admin as a plain
    ``member``.
    """
    email = f"second-admin-{uuid.uuid4().hex[:6]}@x.example"
    with db.begin() as c:
        identity = store.ensure_identity(c, email=email)
        c.execute(
            text(
                "INSERT INTO org_membership (organization_id, "
                "user_identity_id, role, status, joined_at) "
                "VALUES (:o, :i, 'member', :s, now())"
            ),
            {"o": org_id, "i": identity, "s": status},
        )
    return email


class TestTheDeploymentArmRoleSet:
    """R7: the door demands ``_MEMBER_ADMIN_ROLES``, and these fail without it.

    🔴 **Why ``member`` must pass.** The tenant plane's
    ``admin:settings:manage`` is the real gate, and the gateway mirrors here
    only after it passed and the tenant rename committed. A tenant admin can
    be a Console ``member``. An ``owner|admin`` set here would refuse that
    admin's mirror after the tenant name changed, and the two planes would
    show different names with only a log line to say so.
    """

    def test_a_registry_member_renames_on_the_deployment_arm(
        self, client, db, member_key
    ):
        org = _new_org(client)
        who = _member_actor(db, org["id"])
        r = _rename(client, member_key, actor_email=who, name="Shaurrya Tele")
        assert r.status_code == 200, r.text
        assert _row(db, org["id"])["name"] == "Shaurrya Tele"
        assert [row["actor"] for row in _audit_rows(db, org["id"])] == [who]

    def test_a_role_outside_the_set_is_403_and_writes_nothing(
        self, client, db, member_key, monkeypatch
    ):
        """The registry CHECK allows only owner, admin and member, and all
        three are in the set. So the test narrows the CONSTANT. That also
        proves the door reads ``_MEMBER_ADMIN_ROLES`` and no copy of it."""
        from customer_console import main

        org = _new_org(client)
        who = _member_actor(db, org["id"])
        monkeypatch.setattr(main, "_MEMBER_ADMIN_ROLES",
                            frozenset({"owner", "admin"}))
        r = _rename(client, member_key, actor_email=who, name="X")
        assert r.status_code == 403, r.text
        assert _row(db, org["id"])["name"] == "Dewin"
        assert _audit_rows(db, org["id"]) == []

    @pytest.mark.parametrize("status", ["invited", "suspended", "removed"])
    def test_a_member_who_is_not_active_is_403(
        self, client, db, member_key, status
    ):
        """The other half of the gate: the role is in the set, the status is
        not ``active``, so the actor acts for nobody."""
        org = _new_org(client)
        who = _member_actor(db, org["id"], status=status)
        r = _rename(client, member_key, actor_email=who, name="X")
        assert r.status_code == 403, r.text
        assert _row(db, org["id"])["name"] == "Dewin"


# ── Done-when 4 — the Console's own name check ───────────────────────────────

class TestTheNameCheck:
    @pytest.mark.parametrize("name", ["", "   ", "a" * 81])
    def test_an_empty_or_long_name_is_422_and_writes_nothing(
        self, client, db, name
    ):
        org = _new_org(client)
        r = _rename(client, None, org_slug=org["slug"], name=name)
        assert r.status_code == 422, r.text
        assert _row(db, org["id"])["name"] == "Dewin"
        assert _audit_rows(db, org["id"]) == []

    def test_a_name_at_the_limit_is_written(self, client, db):
        org = _new_org(client)
        r = _rename(client, None, org_slug=org["slug"], name="a" * 80)
        assert r.status_code == 200, r.text
        assert _row(db, org["id"])["name"] == "a" * 80

    def test_an_extra_body_field_is_422_never_ignored(self, client, db):
        """A ``slug`` here would be a caller who believes it can move the slug."""
        org = _new_org(client)
        r = _rename(client, None, org_slug=org["slug"], name="X",
                    slug="new-slug")
        assert r.status_code == 422, r.text
        assert _row(db, org["id"]) == {"slug": org["slug"], "name": "Dewin"}


# ── The store write on its own ───────────────────────────────────────────────

class TestTheStoreWrite:
    def test_rename_org_answers_the_previous_name(self, client, db):
        org = _new_org(client)
        with db.begin() as c:
            out = store.rename_org(c, org_id=org["id"], name="Shaurrya Tele")
        assert out == {"slug": org["slug"], "name": "Shaurrya Tele",
                       "previous_name": "Dewin"}

    def test_rename_org_answers_none_for_no_row(self, db):
        with db.begin() as c:
            out = store.rename_org(c, org_id=str(uuid.uuid4()), name="X")
        assert out is None


# ── Done-when 2 — the REAL gateway client and the REAL admin mirror ──────────

def _wire(monkeypatch, key: str) -> None:
    """Point ``console_resolve`` at THIS app, through an ASGI transport.

    The member-write suite's precedent. ``is_wired()`` reads the cached
    ``get_settings()``, so the caller busts the cache before and after.
    """
    import httpx
    from acb_auth import console_resolve as cr

    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "http://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", key)

    def asgi_client():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://console.test",
            timeout=5.0,
        )

    monkeypatch.setattr(cr, "_new_http_client", asgi_client)


class TestTheGatewayClientSpeaksThisDoorsWire:
    def test_rename_org_on_console_lands_the_name(
        self, client, db, member_key, monkeypatch
    ):
        import asyncio

        from acb_auth import console_resolve as cr
        from acb_common import get_settings

        org = _new_org(client)
        _wire(monkeypatch, member_key)
        get_settings.cache_clear()
        try:
            status, body = asyncio.run(
                cr.rename_org_on_console(
                    actor_email=org["owner"], name="Shaurrya Tele",
                )
            )
        finally:
            get_settings.cache_clear()
        assert status == 200, body
        assert _row(db, org["id"]) == {"slug": org["slug"], "name": "Shaurrya Tele"}

    def test_the_admin_mirror_lands_the_name_end_to_end(
        self, client, db, member_key, monkeypatch
    ):
        """The admin path's post-commit mirror, through the real client."""
        import asyncio

        from acb_common import get_settings
        from gateway.routes.admin import organization

        org = _new_org(client)
        _wire(monkeypatch, member_key)
        get_settings.cache_clear()
        try:
            asyncio.run(
                organization._mirror_rename_to_console(
                    actor_email=org["owner"], slug=org["slug"],
                    name="Shaurrya Tele",
                )
            )
        finally:
            get_settings.cache_clear()
        assert _row(db, org["id"]) == {"slug": org["slug"], "name": "Shaurrya Tele"}


# ── The R8 gate cannot silently disarm ───────────────────────────────────────

def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """``pr-check.yml``'s hand-list names the R8 suites that must RUN."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(
        encoding="utf-8"
    )
    assert "tests/unit/test_customer_console_org_rename.py" in workflow, (
        "this suite is not in pr-check.yml's R8 skip-guard list. Without the "
        "entry it can skip in CI while the job reports green"
    )


def test_this_suite_is_named_in_the_spec_verification_block() -> None:
    """The spec's verify block is what a human runs."""
    spec = (
        _ROOT / "project-docs" / "specs" / "customer_console.md"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_customer_console_org_rename.py" in spec
