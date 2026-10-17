"""Shared helpers of the WS-54 GitHub R8 suites (``test_github_*_r8.py``).

Spec: ``project-docs/specs/github_integration.md``. Each helper seeds as the
RLS-bypassing admin of the ``promoted`` catalog and stamps
``organization_id`` itself. The suites then act as the NON-privileged role
``acb_app_h3rls``, through ``tenant_engine_scope``.

Not a test module: the name starts with ``_``, so pytest collects nothing here.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import _APP_ROLE

ROOT = Path(__file__).resolve().parents[2]
FN = "public.github_installation_org(bigint)"
SUPABASE_ROLES = ("anon", "authenticated", "service_role")


def migration() -> Path:
    """The GH-2 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((ROOT / "infra" / "postgres").glob("*.sql"))
        if "FUNCTION public.github_installation_org" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one GH-2 migration, found {hits}"
    return hits[0]


def grant_lookup(p: Any) -> None:
    """The lookup goes to ``acb_app`` only. The rehearsal role has a
    suite-private name, so it gets the same grant here, as the superuser."""
    with p.admin_engine.begin() as c:
        c.execute(text(f"GRANT EXECUTE ON FUNCTION {FN} TO {_APP_ROLE}"))


def app_dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def seed_member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "o": org})


def seed_project(
    p: Any, org: str, email: str, *, personal_owner: str | None = None,
    lanes: tuple[tuple[str, str], ...] = (("To do", "todo"), ("In review", "in_progress"),
                                          ("Done", "done"), ("Cancelled", "cancelled"),
                                          ("Triage", "triage")),
) -> tuple[str, dict[str, str]]:
    """A project that owns its statuses, and its lanes by category."""
    project = str(uuid.uuid4())
    by_category: dict[str, str] = {}
    seed_member(p, org, email)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
            "personal_owner) VALUES (:id, :o, 'gh', :e, true, :po)"),
            {"id": project, "o": org, "e": email, "po": personal_owner})
        for position, (name, category) in enumerate(lanes):
            sid = str(uuid.uuid4())
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": sid, "o": org, "p": project, "n": name, "c": category,
                 "pos": (position + 1) * 10})
            by_category.setdefault(category, sid)
    return project, by_category


_TASK_NUMBERS: dict[str, int] = {}


def seed_task(
    p: Any, org: str, email: str, *, project: str | None = None,
    lanes: dict[str, str] | None = None, category: str = "todo",
    task_id: str | None = None, archived: bool = False, parent: str | None = None,
    personal_owner: str | None = None, recurring: bool = False,
) -> str:
    """One task in *org*. A new project is made unless *project* is given.
    *recurring* gives it a daily ``pm_recurrences`` rule."""
    if project is None or lanes is None:
        project, lanes = seed_project(p, org, email, personal_owner=personal_owner)
    task = task_id or str(uuid.uuid4())
    number = _TASK_NUMBERS[project] = _TASK_NUMBERS.get(project, 0) + 1
    with p.admin_engine.begin() as c:
        rule = None
        if recurring:
            rule = str(c.execute(text(
                "INSERT INTO pm_recurrences (organization_id, project_id, freq, created_by) "
                "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'daily', :e) RETURNING id"),
                {"o": org, "p": project, "e": email}).scalar_one())
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by, archived_at, parent_task_id, "
            "recurrence_id) VALUES (:id, :o, :p, :p, :n, :s, 'Fix the extruder', :e, "
            "CASE WHEN :arch THEN now() ELSE NULL END, CAST(:parent AS uuid), "
            "CAST(:rule AS uuid))"),
            {"id": task, "o": org, "p": project, "n": number, "s": lanes[category],
             "e": email, "arch": archived, "parent": parent, "rule": rule})
    return task


def seed_github_row(
    p: Any, org: str, installation_id: int | None, *, status: str = "connected",
    auth: str = "app", extra: dict[str, Any] | None = None,
) -> str:
    """One ``github`` connection row of *org*, as the admin."""
    meta: dict[str, Any] = {"auth": auth}
    if installation_id is not None:
        meta["installation_id"] = str(installation_id)
        meta["account_login"] = f"acct-{installation_id}"
        meta["account_type"] = "Organization"
    meta.update(extra or {})
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status, provider_meta, connected_by) VALUES (CAST(:o AS uuid), "
            "'github', 'integrations', 'org', :s, CAST(:m AS jsonb), 'admin@gh.test') "
            "ON CONFLICT (organization_id, provider, owning_app, scope_kind, "
            "(COALESCE(member_email, ''))) DO UPDATE SET status = EXCLUDED.status, "
            "provider_meta = EXCLUDED.provider_meta RETURNING id"),
            {"o": org, "s": status, "m": json.dumps(meta)}).scalar_one())


def clear_github(p: Any) -> None:
    """Delete every GitHub row, reference and activity the suites wrote."""
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE provider = 'github'"))
        c.execute(text("DELETE FROM pm_task_refs"))


def scalar(p: Any, sql: str, **params: Any) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def rows(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).all())


# ── Redis: a real private server when one is named, else a fake ─────────────
#
# ``GITHUB_R8_REDIS_URL`` names a PRIVATE Redis (never a shared one: the suite
# runs FLUSHDB). Without it, a fake redis-py client sits under the REAL
# ``TenantRedis`` wrapper, as the My Day R8 suites do, so the key discipline is
# the real one either way. CI has no Redis, so CI takes the fake.

REDIS_URL_ENV = "GITHUB_R8_REDIS_URL"


class FakeRedis:
    """The redis-py commands that the GitHub routes use, on a dict."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl_of: dict[str, int] = {}

    async def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None,
                  **_: Any) -> bool | None:
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        if ex is not None:
            self.ttl_of[key] = int(ex)
        return True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def getdel(self, key: str) -> str | None:
        self.ttl_of.pop(key, None)
        return self.data.pop(key, None)

    async def delete(self, *keys: str) -> int:
        gone = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                gone += 1
            self.ttl_of.pop(key, None)
        return gone

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.data)

    async def ttl(self, key: str) -> int:
        return self.ttl_of.get(key, -2 if key not in self.data else -1)


class RedisView:
    """What a test reads back: every key and value, real or fake."""

    def __init__(self, fake: FakeRedis | None, url: str | None) -> None:
        self.fake = fake
        self.url = url

    def items(self) -> dict[str, str]:
        if self.fake is not None:
            return dict(self.fake.data)
        import redis

        client = redis.Redis.from_url(self.url or "", decode_responses=True)
        try:
            return {k: str(client.get(k)) for k in client.keys("*")
                    if client.type(k) == "string"}
        finally:
            client.close()

    def ttl(self, key: str) -> int:
        if self.fake is not None:
            return self.fake.ttl_of.get(key, -2)
        import redis

        client = redis.Redis.from_url(self.url or "", decode_responses=True)
        try:
            return int(client.ttl(key))
        finally:
            client.close()

    @property
    def real(self) -> bool:
        return self.fake is None


def use_redis(monkeypatch: Any) -> RedisView:
    """Point the tenant Redis wrapper at the private server, or at a fake."""
    import os

    from acb_common import get_settings, tenant_redis
    from acb_common.tenant_redis import TenantRedis

    url = os.environ.get(REDIS_URL_ENV, "").strip()
    if url:
        import redis

        client = redis.Redis.from_url(url)
        try:
            client.flushdb()
        finally:
            client.close()
        monkeypatch.setattr(get_settings(), "redis_url", url, raising=False)
        tenant_redis.reset_pool_for_tests()
        monkeypatch.setattr(tenant_redis, "_POOL", None)
        return RedisView(None, url)
    fake = FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return RedisView(fake, None)


# ── A fake GitHub on httpx.MockTransport ────────────────────────────────────


def make_private_key(tmp_path: Path) -> Path:
    """An RSA key that the suite makes. No real key is ever read."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "gh-r8.pem"
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return path


def configure_app(monkeypatch: Any, key_path: Path, *, enabled: bool = True) -> Any:
    """The App settings of a test box. The flag is ON unless *enabled* is False."""
    from acb_common import get_settings
    from gateway.connections import github_app

    settings = get_settings()
    for name, value in {
        "github_app_enabled": enabled,
        "github_app_id": "4242",
        "github_app_slug": "metorite-test",
        "github_app_client_id": "Iv23.r8-client",
        "github_app_client_secret": "r8-client-secret-never-sent-out",
        "github_app_webhook_secret": "r8-webhook-secret",
        "github_app_private_key_path": str(key_path),
        "app_public_url": "https://app.metorite.com",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    github_app.reset_for_tests()
    return settings


def patch_github(monkeypatch: Any, handler: Any, real_client: Any) -> None:
    """Every ``httpx.AsyncClient`` that the GitHub modules make talks to *handler*."""
    import httpx

    def _client(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)


def user(email: str, org: str, *, manage: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:integrations"] + (["integrations:manage"] if manage else [])
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms), organization_id=org,
    )


async def call(
    p: Any, who: Any, routers: list[Any], method: str, path: str, *,
    real_client: Any, body: Any = None, params: dict | None = None,
    headers: dict | None = None, content: bytes | None = None,
) -> Any:
    """One request through real routers, as *who*. The override binds the
    tenant from the user, as the real auth dependency does. The tenant never
    comes from the request."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI

    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _caller() -> Any:
        bind_tenant(who.organization_id)
        return who

    app = FastAPI()
    for router in routers:
        app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(app_dsn(p)), real_client(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
        ) as client:
            return await client.request(
                method, path, json=body, params=params, headers=headers,
                content=content, follow_redirects=False,
            )
    finally:
        release_tenant(scope)
