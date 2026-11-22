"""Encrypted provider API key store backed by Postgres.

Single master key (ACB_MASTER_KEY env var or settings.acb_master_key) encrypts
all provider keys at rest.  Keys are decrypted on demand — never logged or
stored in plain text outside this module's in-memory cache.

Supports TWO credential types via the credential_type column:
  - 'llm'          — LLM provider API keys (OpenAI, Anthropic, Gemini, etc.)
  - 'integration'  — Business integration credentials (Zoho, Gmail, etc.)

Usage:
    from acb_llm.key_store import get_key_store

    store = get_key_store()
    await store.put("openai", "sk-...")                     # LLM key (default)
    await store.put("zoho-crm:client_id", "...", cred_type="integration")
    key = await store.get("openai")                         # returns plain text key or ""
    all_llm_keys = await store.get_by_type("llm")           # {provider: plain_text_key}
    await store.delete("openai")
    all_keys = await store.get_all()                        # {provider: plain_text_key, ...}
"""
from __future__ import annotations

import base64
import os
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.env_guard import (
    EnvWriteRefused,
    check_env_write,
    is_platform_env,
    value_problem,
)
from cryptography.fernet import Fernet, InvalidToken  # noqa: F401 (re-exported)

_log = get_logger("key_store")

# ---------------------------------------------------------------------------
# Master key derivation — Fernet requires a 32-byte url-safe-base64 key.
# We accept a raw passphrase and derive a proper Fernet key from it.
# ---------------------------------------------------------------------------

def _derive_fernet_key(master_key: str) -> bytes:
    """Derive a 32-byte Fernet key from an arbitrary-length master passphrase."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt = b"acb-provider-keys-v1"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8")))


#: Service name → {provider suffix → env var name}. The built-in
#: integrations whose stored keys ``configure_integrations`` copies into
#: ``os.environ`` at startup. It mirrors ``_SETUP_GUIDES`` in
#: ``apps/services/gateway/gateway/routes/integrations.py``, and the gateway
#: reserves every service id here: a custom integration may not take one
#: (security fix round 2, 2026-10-05).
INTEGRATION_ENV_MAP: dict[str, dict[str, str]] = {
    "zoho-crm": {
        "client_id": "ZOHO_CLIENT_ID",
        "client_secret": "ZOHO_CLIENT_SECRET",
        "refresh_token": "ZOHO_REFRESH_TOKEN",
        "api_domain": "ZOHO_API_DOMAIN",
        "accounts_url": "ZOHO_ACCOUNTS_URL",
        "region": "ZOHO_REGION",
    },
    "apollo": {
        "api_key": "APOLLO_API_KEY",
    },
    "google-maps": {
        "api_key": "GOOGLE_MAPS_API_KEY",
    },
    "instantly": {
        "api_key": "INSTANTLY_API_KEY",
    },
    # ⚠️ No `gmail` and no `gmail-send` entry (WS-17 EM-G11b, 2026-10-14).
    # The service-account Gmail path is gone. A stored row under either id
    # is not loaded at startup. `integrations.RETIRED_SERVICE_IDS` keeps both
    # ids reserved.
    "smtp": {
        "host": "SMTP_HOST",
        "port": "SMTP_PORT",
        "username": "SMTP_USERNAME",
        "password": "SMTP_PASSWORD",
    },
    "github": {
        # WS-54 GH-7: the customer-made OAuth App client id is retired with
        # the device flow, so no row of it maps to an env var any more.
        "token": "GITHUB_TOKEN",
    },
    "serpapi": {
        "api_key": "SERPAPI_API_KEY",
    },
    "apify": {
        "api_token": "APIFY_API_TOKEN",
    },
    "anymailfinder": {
        "api_key": "ANYMAILFINDER_API_KEY",
    },
    "google-sheets": {
        "sa_json_path": "GOOGLE_SHEETS_SA_JSON_PATH",
    },
    # ⚠️ No `gmail-oauth` and no `microsoft-oauth` entry (WS-17 EM-G7 review
    # round 1, O-GM-5). Each name they mapped is a platform name, so layer B
    # skipped every row and never copied the env value into the store. The
    # mail apps are the deployment's (D-EM-1): an operator sets them in the
    # env file of the box. `integrations.MAIL_APP_SERVICE_IDS` keeps both ids
    # reserved. Fence: `tests/unit/test_integrations_mail_app_keys.py`.
}

#: The ``credential_type`` of a built-in integration key. Startup loads
#: only this type.
BUILTIN_INTEGRATION_TYPE = "integration"

#: The ``credential_type`` of a key that a custom integration declares. It
#: lives in the store only, and nothing copies it into ``os.environ``.
CUSTOM_INTEGRATION_TYPE = "custom"


class ProviderKeyStore:
    """Encrypted provider key persistence backed by the provider_keys table."""

    def __init__(self) -> None:
        self._fernet: Fernet | None = None
        # MT-0d: keyed by (organization_id, provider) — NOT provider alone.
        # A provider-keyed cache is a cross-tenant leak that no amount of
        # correctness in the SQL below would catch: the second tenant asking for
        # "openai" would be served the first tenant's decrypted key straight
        # from memory, without a query ever running.
        self._cache: dict[tuple[str, str], str] = {}

    @property
    def _f(self) -> Fernet:
        if self._fernet is None:
            settings = get_settings()
            master = settings.acb_master_key or os.environ.get("ACB_MASTER_KEY", "")
            if not master:
                raise RuntimeError(
                    "ACB_MASTER_KEY is not set.  Generate one with: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
            key = _derive_fernet_key(master)
            self._fernet = Fernet(key)
        return self._fernet

    async def _execute(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        """Run a raw SQL statement and return rows as dicts.

        Uses psycopg sync connection wrapped in asyncio.to_thread() for
        Windows compatibility (avoids ProactorEventLoop issue).
        """
        import asyncio
        import re as _re

        import psycopg
        from psycopg.rows import dict_row

        from acb_common.dsn import conninfo as _dsn_conninfo

        settings = get_settings()
        db_url = str(settings.database_url)

        # SQLAlchemy URL → libpq conninfo via the shared parser: query params
        # (sslmode=… on managed Postgres) must survive into the conninfo.
        conninfo = _dsn_conninfo(db_url)

        # Convert :param style to %(param)s for psycopg
        pg_sql = _re.sub(r":(\w+)", r"%(\1)s", sql)

        def _sync() -> list[dict[str, Any]]:
            with psycopg.connect(
                conninfo, row_factory=dict_row, connect_timeout=5,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(pg_sql, params)
                    if cur.description is None:
                        return []  # INSERT/UPDATE/DELETE — no rows to fetch
                    return list(cur.fetchall())

        return await asyncio.to_thread(_sync)

    async def _resolve_org(self, organization_id: str | None) -> str:
        """The organization a credential read/write belongs to. Fails CLOSED.

        MT-0d (``saas_multitenancy.md`` §6.3). ``provider_keys`` was
        ``provider TEXT PRIMARY KEY`` — one key per provider for the whole box.
        That was correct under D11 (one deployment per tenant) and is a
        cross-tenant leak under D15.

        Passing ``organization_id`` explicitly is always right. ``None`` means
        "the sole organization", which **only resolves while there is exactly
        one** — every one of the ~20 existing call sites relies on it today and
        keeps working unchanged, and the moment a second organization is created
        it returns ``""`` and every untenanted read yields nothing.

        That is the point, and it is why this is not a "default org" lookup: a
        default-org fallback would keep answering *after* tenant #2 arrived, and
        would serve the operator's keys to a customer. Failing closed turns that
        into a visibly missing key at exactly the moment MT-1 must supply a real
        tenant, instead of a silent leak nobody notices.
        """
        if organization_id:
            return str(organization_id)
        rows = await self._execute(
            "SELECT id FROM organization WHERE (SELECT count(*) FROM organization) = 1"
        )
        return str(rows[0]["id"]) if rows else ""

    async def get(self, provider: str, organization_id: str | None = None) -> str:
        """Return the plain-text API key for a provider, or '' if not set.

        MT-0d: scoped to *organization_id*, or to the sole organization when it
        is omitted (see :meth:`_resolve_org` — that resolution fails closed once
        a second tenant exists).
        """
        org = await self._resolve_org(organization_id)
        if not org:
            _log.warning("key_store.no_tenant_resolved", provider=provider)
            return ""

        cache_key = (org, provider)
        if cache_key in self._cache:
            return self._cache[cache_key]

        rows = await self._execute(
            "SELECT encrypted FROM provider_keys "
            " WHERE organization_id = :org_id AND provider = :provider",
            org_id=org,
            provider=provider,
        )
        if not rows:
            return ""

        try:
            plain = self._f.decrypt(base64.urlsafe_b64decode(rows[0]["encrypted"])).decode("utf-8")
            self._cache[cache_key] = plain
            return plain
        except Exception:
            _log.warning("key_store.decrypt_failed", provider=provider)
            return ""

    def encrypt(self, plain_text: str) -> str:
        """Encrypt a plain-text string and return base64-encoded ciphertext.

        Uses the same Fernet key as the provider key store.  Callers can
        store the returned ciphertext and later decrypt it with decrypt().
        """
        encrypted = self._f.encrypt(plain_text.encode("utf-8"))
        return base64.urlsafe_b64encode(encrypted).decode("ascii")

    def decrypt(self, cipher_text: str) -> str:
        """Decrypt a ciphertext produced by encrypt()."""
        encrypted = base64.urlsafe_b64decode(cipher_text)
        return self._f.decrypt(encrypted).decode("utf-8")

    async def put(
        self,
        provider: str,
        api_key: str,
        credential_type: str = "llm",
        service: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        """Store an encrypted API key (upsert), scoped to an organization.

        Args:
            provider: Unique key identifier (e.g. 'openai', 'zoho-crm:client_id').
            api_key: The plain-text credential to encrypt and store.
            credential_type: 'llm' (default) or 'integration'.
            service: For integration keys, the service name (e.g. 'zoho-crm').
                     Defaults to provider when not given.
            organization_id: MT-0d — the owning org. Omitted resolves to the sole
                     organization and **raises** once a second one exists, which
                     is deliberate: writing a credential with no owner is how a
                     key ends up readable by the wrong tenant, and a write is a
                     far better place to fail loudly than a read.
        """
        if not api_key.strip():
            raise ValueError("api_key cannot be empty")

        org = await self._resolve_org(organization_id)
        if not org:
            raise RuntimeError(
                "key_store.put requires an organization_id once more than one "
                "organization exists (MT-0d / saas_multitenancy.md §6.3)"
            )

        encrypted = base64.urlsafe_b64encode(
            self._f.encrypt(api_key.encode("utf-8"))
        ).decode("ascii")

        svc = service or provider

        await self._execute(
            """
            INSERT INTO provider_keys
                (organization_id, provider, encrypted, credential_type, service, updated_at)
            VALUES (:org_id, :provider, :encrypted, :credential_type, :service, now())
            ON CONFLICT (organization_id, provider) DO UPDATE
            SET encrypted = :encrypted,
                credential_type = :credential_type,
                service = :service,
                updated_at = now()
            """,
            org_id=org,
            provider=provider,
            encrypted=encrypted,
            credential_type=credential_type,
            service=svc,
        )
        self._cache[(org, provider)] = api_key
        _log.info("key_store.put", provider=provider,
                   credential_type=credential_type)

    async def delete(self, provider: str, organization_id: str | None = None) -> None:
        """Remove a provider's API key (MT-0d: within one organization only)."""
        org = await self._resolve_org(organization_id)
        if not org:
            _log.warning("key_store.delete_no_tenant", provider=provider)
            return
        await self._execute(
            "DELETE FROM provider_keys "
            " WHERE organization_id = :org_id AND provider = :provider",
            org_id=org,
            provider=provider,
        )
        self._cache.pop((org, provider), None)
        _log.info("key_store.delete", provider=provider)

    async def _decrypt_rows(self, org: str, rows: list[dict[str, Any]]) -> dict[str, str]:
        """Decrypt ``(provider, encrypted)`` rows, using and filling the cache.

        MT-0d: the cache key is ``(org, provider)``. It was ``provider`` alone,
        which would have served the first tenant's decrypted key to the second
        without a query ever running — a leak no amount of correct SQL catches.
        """
        result: dict[str, str] = {}
        for row in rows:
            provider = row["provider"]
            cache_key = (org, provider)
            if cache_key in self._cache:
                result[provider] = self._cache[cache_key]
                continue
            try:
                plain = self._f.decrypt(
                    base64.urlsafe_b64decode(row["encrypted"])
                ).decode("utf-8")
                self._cache[cache_key] = plain
                result[provider] = plain
            except Exception:
                _log.warning("key_store.decrypt_failed", provider=provider)
        return result

    async def get_all(self, organization_id: str | None = None) -> dict[str, str]:
        """Return this organization's stored provider keys (decrypted)."""
        org = await self._resolve_org(organization_id)
        if not org:
            return {}
        rows = await self._execute(
            "SELECT provider, encrypted FROM provider_keys "
            " WHERE organization_id = :org_id",
            org_id=org,
        )
        return await self._decrypt_rows(org, rows)

    async def get_by_type(
        self, credential_type: str, organization_id: str | None = None,
    ) -> dict[str, str]:
        """Return this org's keys of a given credential_type (decrypted).

        Args:
            credential_type: 'llm' or 'integration'.
            organization_id: MT-0d — omitted resolves to the sole organization.

        Returns:
            {provider: plain_text_key, ...}
        """
        org = await self._resolve_org(organization_id)
        if not org:
            return {}
        rows = await self._execute(
            "SELECT provider, encrypted FROM provider_keys "
            " WHERE organization_id = :org_id AND credential_type = :credential_type",
            org_id=org,
            credential_type=credential_type,
        )
        return await self._decrypt_rows(org, rows)

    async def get_by_service(
        self, service: str, organization_id: str | None = None,
    ) -> dict[str, str]:
        """Return this org's keys for a given service (decrypted).

        Args:
            service: Service name, e.g. 'zoho-crm', 'gmail'.
            organization_id: MT-0d — omitted resolves to the sole organization.

        Returns:
            {provider: plain_text_key, ...}
        """
        org = await self._resolve_org(organization_id)
        if not org:
            return {}
        rows = await self._execute(
            "SELECT provider, encrypted FROM provider_keys "
            " WHERE organization_id = :org_id AND service = :service",
            org_id=org,
            service=service,
        )
        return await self._decrypt_rows(org, rows)

    async def configure_integrations(self) -> None:
        """Load all integration credentials into os.environ.

        Call once at startup so ingestion clients and skills can read
        credentials via os.environ / get_settings() without code changes.

        Integration → env var mapping mirrors _SETUP_GUIDES in
        apps/services/gateway/gateway/routes/integrations.py.

        🔒 Layers A and B (``acb_common.env_guard.check_env_write``, security
        fix round 1, 2026-10-05). A stored row whose value fails layer A (a
        control character, more than 4096 bytes, or a shell character) or
        whose name fails layer B (a platform or operator-only name, such as a
        mail-app key or ``ZOHO_API_DOMAIN``) is skipped and logged with the
        key name only. It never reaches ``os.environ``. A row that an older
        route wrote must not override the env value of the box, and a NUL
        raises in ``os.environ``, which aborts the whole loop. A platform name
        is never copied from the env into the store either.

        🔒 Round 2: it reads only ``credential_type = 'integration'`` rows, so
        a row of a custom service never reaches ``os.environ``. Before this, a
        custom service named ``github`` could store ``github:token`` and land
        in ``GITHUB_TOKEN`` at the next start, past the BYOK gate.
        """
        # 🔒 Only BUILT-IN integration rows (round 2, 2026-10-05). A custom
        # integration's key is stored as credential_type 'custom' under
        # `custom:<service_id>:<key>`, so this read never sees it, even when a
        # row of a custom service takes a built-in provider name.
        stored = await self.get_by_type(BUILTIN_INTEGRATION_TYPE)
        for service, key_map in INTEGRATION_ENV_MAP.items():
            for suffix, env_var in key_map.items():
                provider = f"{service}:{suffix}"
                key = stored.get(provider, "")
                if key:
                    try:
                        check_env_write(env_var, key)
                    except EnvWriteRefused as exc:
                        _log.warning(
                            "key_store.integration_row_refused",
                            env_var=env_var,
                            rule="platform" if exc.platform else "value",
                        )
                        continue
                    os.environ[env_var] = key
                    _log.debug(
                        "key_store.integration_configured",
                        service=service,
                        env_var=env_var,
                    )
                elif not is_platform_env(env_var):
                    # Preserve any existing env var (from .env bootstrap)
                    existing = os.environ.get(env_var, "")
                    if existing and existing.strip():
                        # Auto-migrate from env → encrypted store
                        await self.put(
                            provider,
                            existing.strip(),
                            credential_type="integration",
                            service=service,
                        )
                        _log.info(
                            "key_store.integration_migrated",
                            service=service,
                            env_var=env_var,
                        )

    async def provider_key(self, provider: str, *, organization_id: str | None) -> str:
        """The key of ONE direct model call to *provider*, or ``""``.

        D58.2 (task 625, H-43). A direct litellm call passes this value as
        ``api_key``. Nothing copies it into ``os.environ`` or into a litellm
        module attribute, so no other caller in the process can send it.

        The order:

        1. The stored key of *organization_id*. ``None`` resolves to the sole
           organization, and to nothing once a second one exists
           (:meth:`_resolve_org`). A caller with no organization therefore
           never gets a tenant key on a pooled box.
        2. Else the PLATFORM key: the env var of :data:`PROVIDER_ENV_VARS`,
           which only the operator sets on the box (:func:`platform_key`).

        A stored value that fails layer A (``acb_common.env_guard``) is not
        sent, because a control character in a header is an injection. A store
        that does not answer falls back to the platform key, as the old loader
        did. The key name is logged, never the value.

        Fence: ``tests/unit/test_llm_call_credentials.py``.
        """
        if provider not in PROVIDER_ENV_VARS:
            return ""
        key = ""
        try:
            key = (await self.get(provider, organization_id=organization_id)).strip()
        except Exception as exc:  # the platform key still answers
            _log.warning(
                "key_store.provider_key_unavailable",
                provider=provider, error=str(exc)[:160],
            )
        if key and value_problem(key):
            _log.warning("key_store.provider_key_refused", provider=provider)
            key = ""
        return key or platform_key(provider)


#: Store slug → the env var of the PLATFORM key of that provider. The one
#: provider table of the key store. The env value is the operator's (the env
#: file of the box). No code path copies a stored key into it (D58.2).
PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    # Speech-to-text (the Note Taker STT tier). litellm's Deepgram handler and
    # the native AssemblyAI provider both take the key per call.
    "deepgram": "DEEPGRAM_API_KEY",
    "assemblyai": "ASSEMBLYAI_API_KEY",
}

#: A litellm provider name → its store slug, where the two differ.
_LITELLM_PROVIDER_SLUG: dict[str, str] = {"together_ai": "together"}


def provider_slug(name: str) -> str | None:
    """The store slug of a litellm provider name or a model prefix, or None."""
    slug = _LITELLM_PROVIDER_SLUG.get(name, name)
    return slug if slug in PROVIDER_ENV_VARS else None


def platform_key(provider: str) -> str:
    """The operator's key for *provider* from the env, or ``""``.

    It READS the env and never writes it. A value that fails layer A is not
    returned.
    """
    env_var = PROVIDER_ENV_VARS.get(provider, "")
    value = os.environ.get(env_var, "").strip() if env_var else ""
    if value and value_problem(value):
        _log.warning("key_store.platform_key_refused", env_var=env_var)
        return ""
    return value


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_store: ProviderKeyStore | None = None


def get_key_store() -> ProviderKeyStore:
    """Return the global ProviderKeyStore singleton."""
    global _store
    if _store is None:
        _store = ProviderKeyStore()
    return _store


async def caller_provider_key(provider: str) -> str:
    """The key of ONE direct call to *provider*, for the bound organization.

    D58.2 (task 625). The one entry point that the direct callers use: the
    litellm arm (``acb_llm.client.call_credentials``), the live STT token
    (``gateway.routes.notes.live``) and the AssemblyAI provider. The
    organization is ``acb_common.db.current_tenant()``, which the auth
    dependency or a job binds. It never comes from request input (R5, R11).
    See :meth:`ProviderKeyStore.provider_key` for the order.
    """
    from acb_common.db import current_tenant

    return await get_key_store().provider_key(provider, organization_id=current_tenant())
