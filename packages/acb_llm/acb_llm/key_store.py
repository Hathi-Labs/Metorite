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
from cryptography.fernet import Fernet

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
        """
        # Service name → {provider suffix → env var name}
        _integration_env_map: dict[str, dict[str, str]] = {
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
            "gmail": {
                "sa_json_path": "GMAIL_SA_JSON_PATH",
                "default_user": "GMAIL_DEFAULT_USER",
            },
            "gmail-send": {
                "sa_json_path": "GMAIL_SA_JSON_PATH",
                "default_user": "GMAIL_DEFAULT_USER",
            },
            "smtp": {
                "host": "SMTP_HOST",
                "port": "SMTP_PORT",
                "username": "SMTP_USERNAME",
                "password": "SMTP_PASSWORD",
            },
            "github": {
                "token": "GITHUB_TOKEN",
                "client_id": "GITHUB_CLIENT_ID",
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
            "gmail-oauth": {
                "gmail_oauth_client_id": "GMAIL_OAUTH_CLIENT_ID",
                "gmail_oauth_client_secret": "GMAIL_OAUTH_CLIENT_SECRET",
            },
            "microsoft-oauth": {
                "msft_oauth_client_id": "MSFT_OAUTH_CLIENT_ID",
                "msft_oauth_client_secret": "MSFT_OAUTH_CLIENT_SECRET",
                "microsoft_tenant_id": "MICROSOFT_TENANT_ID",
            },
        }

        for service, key_map in _integration_env_map.items():
            for suffix, env_var in key_map.items():
                provider = f"{service}:{suffix}"
                key = await self.get(provider)
                if key:
                    os.environ[env_var] = key
                    _log.debug(
                        "key_store.integration_configured",
                        service=service,
                        env_var=env_var,
                    )
                else:
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

    async def configure_litellm(self) -> None:
        """Load all stored keys into litellm's module-level config.

        Call once at startup so litellm.acompletion() can route to any
        configured provider without needing a proxy.  Also sets os.environ
        so the Settings UI shows providers as configured.
        """
        import litellm as _litellm

        # Provider slug → (litellm attr, env var name)
        _provider_config = {
            "openai":     ("api_key",             "OPENAI_API_KEY"),
            "anthropic":  ("anthropic_api_key",   "ANTHROPIC_API_KEY"),
            "gemini":     ("gemini_api_key",      "GEMINI_API_KEY"),
            "deepseek":   ("deepseek_api_key",    "DEEPSEEK_API_KEY"),
            "groq":       ("groq_api_key",        "GROQ_API_KEY"),
            "mistral":    ("mistral_api_key",     "MISTRAL_API_KEY"),
            "together":   ("together_api_key",    "TOGETHER_API_KEY"),
            "openrouter": ("openrouter_api_key",  "OPENROUTER_API_KEY"),
            # Speech-to-text provider (Note Taker STT tier). litellm's Deepgram
            # transcription handler reads DEEPGRAM_API_KEY from the environment.
            "deepgram":   ("deepgram_api_key",    "DEEPGRAM_API_KEY"),
            # AssemblyAI is reached by a NATIVE provider (acb_stt) and by the
            # live-token endpoint, both of which read the env var directly.
            "assemblyai": ("assemblyai_api_key",  "ASSEMBLYAI_API_KEY"),
        }

        all_keys = await self.get_all()
        for provider, key in all_keys.items():
            if not key:
                continue
            cfg = _provider_config.get(provider)
            if cfg:
                litellm_attr, env_var = cfg
                setattr(_litellm, litellm_attr, key)
                os.environ[env_var] = key
                _log.debug("key_store.litellm_configured", provider=provider)
            else:
                _log.debug("key_store.unknown_provider", provider=provider)


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
