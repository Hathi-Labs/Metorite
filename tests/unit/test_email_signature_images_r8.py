"""WS-17 SG-1 — hosted signature images under FORCE RLS (R8).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.4, §23.4, §23.7,
D-EM-67 and D-EM-68.

Runs against a REAL Postgres, through the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``, as the non-privileged app role (no
superuser, no BYPASSRLS). It pins:

* Organization B cannot read an image of organization A: the table policy
  hides the row, and the public route answers 404.
* A token of A under the id of B gives 404, and so does a bad token and a
  wrong extension.
* The public route serves a retired image, with the stored type and the
  spec headers.
* The table refuses a row stamped with another org (WITH CHECK).
* The store keeps one copy of each image, and refuses member 21.
* The settings save cleans a new HTML signature and hosts its ``data:`` image
  through the real child, and a save of the stored signature changes no byte.

Each database class skips cleanly without ``TENANT_LADDER_DATABASE_URL``
(``_DB_GATE``). The CI list check and the migration check need no database.
The migration is found by CONTENT, never by number (R1).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from base64 import b64encode
from pathlib import Path

import pymupdf
import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway import signature_image as si
from gateway.routes.email import AssistantSettingsModel
from gateway.routes.email import signature_images as routes
from gateway.routes.email.automation import assistant as assistant_mod
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_BASE = "https://api.example.test"


def _png(width: int = 40, height: int = 20, fill: tuple[int, ...] = (51, 102, 153)) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), True)
    pix.set_rect(pix.irect, (*fill, 255))
    return pix.tobytes("png")


def _colour() -> tuple[int, ...]:
    """A random colour, so each seeded image has its own bytes and hash."""
    return tuple(secrets.token_bytes(3))


def _seed_image(admin_engine, *, org: str, member: str = "a@sig.test",
                retired: bool = False, data: bytes | None = None) -> tuple[str, str, bytes]:
    data = data or _png(fill=_colour())
    token = secrets.token_urlsafe(32)
    with admin_engine.begin() as c:
        image_id = str(c.execute(text(
            "INSERT INTO email_signature_images (organization_id, created_by, kind, "
            "content_type, width, height, bytes, sha256, public_token, retired_at) "
            "VALUES (CAST(:o AS uuid), :me, 'logo', 'image/png', 40, 20, :b, :sha, "
            ":t, CASE WHEN :r THEN now() END) RETURNING id"),
            {"o": org, "me": member, "b": data,
             "sha": hashlib.sha256(data).hexdigest(), "t": token, "r": retired},
        ).scalar_one())
    return image_id, token, data


def _app_dsn(promoted) -> str:  # noqa: F811
    return promoted.app_url.render_as_string(hide_password=False)


def test_the_migration_is_tenant_scoped_with_force_rls() -> None:
    [migration] = [
        p for p in (_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql")
        if "CREATE TABLE IF NOT EXISTS email_signature_images" in p.read_text(encoding="utf-8")
    ]
    body = migration.read_text(encoding="utf-8")
    assert "ALTER TABLE email_signature_images FORCE  ROW LEVEL SECURITY" in body
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in body
    assert "UNIQUE (organization_id, sha256)" in body


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention. The
    release gate in deploy.yml holds a copy of the list."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(
            r"^\s+tests/unit/test_email_signature_images_r8\.py \\?$", workflow, re.M), name


@_DB_GATE
class TestTheTableUnderForceRls:

    def test_org_b_cannot_read_an_image_of_org_a(self, promoted, app_engine):  # noqa: F811
        _assert_non_priv(app_engine)
        _, token, _ = _seed_image(promoted.admin_engine, org=promoted.org_a)
        query = text("SELECT count(*) FROM email_signature_images WHERE public_token = :t")
        for org, want in ((promoted.org_b, 0), (promoted.org_a, 1)):
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                got = c.execute(query, {"t": token}).scalar_one()
            assert got == want, (org, got)

    def test_a_row_stamped_with_another_org_is_refused(self, promoted, app_engine):  # noqa: F811
        data = _png(fill=(9, 9, 9))
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_b})
            with pytest.raises(DBAPIError):
                c.execute(text(
                    "INSERT INTO email_signature_images (organization_id, created_by, "
                    "kind, content_type, width, height, bytes, sha256, public_token) "
                    "VALUES (CAST(:o AS uuid), 'b@sig.test', 'logo', 'image/png', 1, 1, "
                    ":b, :sha, :t)"),
                    {"o": promoted.org_a, "b": data,
                     "sha": hashlib.sha256(data).hexdigest(),
                     "t": secrets.token_urlsafe(32)})


@_DB_GATE
class TestThePublicRoute:

    async def test_the_owner_org_gets_the_bytes_and_the_headers(self, promoted, app_engine):  # noqa: F811
        _, token, data = _seed_image(promoted.admin_engine, org=promoted.org_a)
        async with tenant_engine_scope(_app_dsn(promoted)):
            res = await routes.signature_image_file(promoted.org_a, token, "png")
        assert res.status_code == 200 and res.body == data
        assert res.media_type == "image/png"
        for name, value in routes.PUBLIC_HEADERS.items():
            assert res.headers[name] == value

    async def test_a_token_of_a_under_the_id_of_b_is_404(self, promoted, app_engine):  # noqa: F811
        _, token, _ = _seed_image(promoted.admin_engine, org=promoted.org_a)
        async with tenant_engine_scope(_app_dsn(promoted)):
            with pytest.raises(HTTPException) as caught:
                await routes.signature_image_file(promoted.org_b, token, "png")
        assert caught.value.status_code == 404 and caught.value.detail == "Not Found"

    @pytest.mark.parametrize("change", ["token", "ext", "org"])
    async def test_every_miss_is_the_same_404(self, promoted, app_engine, change):  # noqa: F811
        _, token, _ = _seed_image(promoted.admin_engine, org=promoted.org_a)
        org, ext = promoted.org_a, "png"
        if change == "token":
            token = secrets.token_urlsafe(32)
        elif change == "ext":
            ext = "jpg"
        else:
            org = str(uuid.uuid4())
        async with tenant_engine_scope(_app_dsn(promoted)):
            with pytest.raises(HTTPException) as caught:
                await routes.signature_image_file(org, token, ext)
        assert (caught.value.status_code, caught.value.detail) == (404, "Not Found")

    async def test_a_retired_image_is_still_served(self, promoted, app_engine):  # noqa: F811
        _, token, data = _seed_image(promoted.admin_engine, org=promoted.org_a, retired=True)
        async with tenant_engine_scope(_app_dsn(promoted)):
            res = await routes.signature_image_file(promoted.org_a, token, "png")
        assert res.status_code == 200 and res.body == data


def _member(email: str, org: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=EffectiveAccess(role_granted=frozenset({"feature:email"})))


def _encoded(data: bytes) -> si.Encoded:
    return si.Encoded(data=data, content_type="image/png", width=40, height=20,
                      dark_share=0.0, clear_share=0.0)


@_DB_GATE
class TestTheStore:

    async def test_one_copy_of_each_image_and_the_url_opens(self, promoted, app_engine):  # noqa: F811
        member = f"m-{uuid.uuid4().hex[:6]}@sig.test"
        data = _png(fill=_colour())
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                first = await routes.store_image(
                    _encoded(data), kind="logo", member=member, base=_BASE)
                second = await routes.store_image(
                    _encoded(data), kind="logo", member=member, base=_BASE)
                _, _, path_token_ext = first["url"].rpartition("/")
                res = await routes.signature_image_file(
                    promoted.org_b, path_token_ext.removesuffix(".png"), "png")
        finally:
            release_tenant(token)
        assert first["id"] == second["id"]
        assert first["url"].startswith(f"{_BASE}/email/signature-image/{promoted.org_b}/")
        assert res.body == data
        with promoted.admin_engine.connect() as c:
            rows = c.execute(text(
                "SELECT organization_id::text, created_by FROM email_signature_images "
                "WHERE sha256 = :s"), {"s": hashlib.sha256(data).hexdigest()}).all()
        assert [tuple(r) for r in rows] == [(promoted.org_b, member)]

    async def test_member_21_is_refused(self, promoted, app_engine):  # noqa: F811
        member = f"m-{uuid.uuid4().hex[:6]}@sig.test"
        for _ in range(routes.MEMBER_CAP):
            _seed_image(promoted.admin_engine, org=promoted.org_b, member=member)
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                with pytest.raises(HTTPException) as caught:
                    await routes.store_image(
                        _encoded(_png(fill=_colour())),
                        kind="logo", member=member, base=_BASE)
        finally:
            release_tenant(token)
        assert caught.value.status_code == 409
        assert caught.value.detail.startswith("You have uploaded 20 signature images")


@_DB_GATE
class TestTheSettingsSave:

    async def test_a_new_signature_is_cleaned_and_its_data_image_hosted(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        monkeypatch.setenv("GATEWAY_PUBLIC_URL", _BASE)
        owner = f"owner-{uuid.uuid4().hex[:8]}@sig.test"
        acc = _seed_account(promoted.admin_engine, org=promoted.org_b, owner=owner)
        data_uri = "data:image/png;base64," + b64encode(_png(64, 32)).decode("ascii")
        html = (f'<p>Jane<script>alert(1)</script></p><img src="{data_uri}" width="64">'
                '<a href="javascript:alert(1)">x</a><img src="http://t.example/p.png">')
        user = _member(owner, promoted.org_b)
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                put = await assistant_mod.put_assistant_settings(
                    AssistantSettingsModel(account_id=acc, signature=html), user=user)
                again = await assistant_mod.put_assistant_settings(
                    AssistantSettingsModel(account_id=acc, signature=put["signature"],
                                           about="new about"), user=user)
        finally:
            release_tenant(token)
        stored = put["signature"]
        assert "script" not in stored and "javascript" not in stored
        assert "data:" not in stored and "http://" not in stored
        assert stored.startswith("<p>Jane</p><img src=\"https://api.example.test/email/"
                                 f"signature-image/{promoted.org_b}/")
        assert again["signature"] == stored
        with promoted.admin_engine.connect() as c:
            saved = c.execute(text(
                "SELECT signature FROM email_assistant_settings "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": acc}).scalar_one()
            hosted = c.execute(text(
                "SELECT count(*) FROM email_signature_images "
                "WHERE organization_id = CAST(:o AS uuid) AND created_by = :me"),
                {"o": promoted.org_b, "me": owner}).scalar_one()
        assert saved == stored
        assert hosted == 1

    async def test_a_mixed_case_member_hosts_an_image_in_lower_case(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 1, P3: the session address can hold capitals, and the
        CHECK of created_by refuses them. The save writes it in lower case."""
        monkeypatch.setenv("GATEWAY_PUBLIC_URL", _BASE)
        owner = f"Owner-{uuid.uuid4().hex[:8]}@Sig.Test"
        acc = _seed_account(promoted.admin_engine, org=promoted.org_b, owner=owner)
        data_uri = "data:image/png;base64," + b64encode(_png(48, 24, _colour())).decode("ascii")
        user = _member(owner, promoted.org_b)
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                put = await assistant_mod.put_assistant_settings(
                    AssistantSettingsModel(account_id=acc, signature=f'<img src="{data_uri}">'),
                    user=user)
        finally:
            release_tenant(token)
        assert put["signature"].startswith(f'<img src="{_BASE}/email/signature-image/')
        with promoted.admin_engine.connect() as c:
            who = c.execute(text(
                "SELECT created_by FROM email_signature_images "
                "WHERE organization_id = CAST(:o AS uuid) AND lower(created_by) = lower(:me)"),
                {"o": promoted.org_b, "me": owner}).scalars().all()
        assert who == [owner.lower()]

    async def test_a_save_of_the_stored_signature_changes_no_byte(
        self, promoted, app_engine,  # noqa: F811
    ):
        """§23.0 rule 7. A legacy signature is not cleaned by a change of
        another setting: only a new signature is a signature save."""
        owner = f"owner-{uuid.uuid4().hex[:8]}@sig.test"
        acc = _seed_account(promoted.admin_engine, org=promoted.org_b, owner=owner)
        legacy = '<p onclick="x()">Old <img src="data:image/png;base64,AAAA"></p>'
        with promoted.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_assistant_settings (account_id, signature, "
                "organization_id) VALUES (CAST(:a AS uuid), :s, CAST(:o AS uuid))"),
                {"a": acc, "s": legacy, "o": promoted.org_b})
        user = _member(owner, promoted.org_b)
        token = bind_tenant(promoted.org_b)
        try:
            async with tenant_engine_scope(_app_dsn(promoted)):
                put = await assistant_mod.put_assistant_settings(
                    AssistantSettingsModel(account_id=acc, signature=legacy,
                                           about="only the about changed"), user=user)
        finally:
            release_tenant(token)
        assert put["signature"] == legacy
