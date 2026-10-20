"""The object store — the one seam to file bytes (MT-1g-1, D108).

Spec: ``project-docs/specs/saas_multitenancy.md`` MT-1g. Decision: D108
(owner, 2026-10-20). Fence: ``tests/unit/test_object_store.py``.

Postgres keeps rows a query reads. This store keeps file bytes that no SQL
predicate reads: uploads, generated files, media and bundles. The box's disk
is a cache, never the only copy.

**Keys.** Every object lives at ``<organization_id>/<app>/<object id>``. The
seam makes the key from the tenant bound to the caller's context
(``acb_common.db.current_tenant``). A job with no bound tenant may pass
``organization_id`` explicitly. When a tenant is bound, an explicit
``organization_id`` must equal it, or the call is refused, because a key
holds its organization and a careless caller would copy it from there. The
seam never takes a tenant from request input (R5). A caller stores the
returned key in its own row.

**Isolation.** One app credential can read every tenant's objects, so the
store cannot do what FORCE RLS does for a table. This seam does it: every
read, head and delete parses the key and refuses one whose organization is
not the caller's (:class:`ObjectKeyRefused`). The check is the isolation.

**Sign-in.** Two modes, chosen by the settings that are set:

* **Supabase** (production, staging). The seam signs in as the files user
  through Supabase Auth (``/auth/v1/token?grant_type=password``) and sends
  the JWT as the S3 session token. The access key field holds the project
  ref, and the secret field holds the anon key, as BH-4 does for the backup.
  Storage policies limit the files user to its own bucket. The JWT is cached
  and renewed five minutes before it expires.
* **A key pair** (a local MinIO, the tests). Only the two key fields. The
  seam refuses this mode on a Supabase endpoint, and whenever
  ``object_store_supabase_url`` is set, because a Supabase S3 access key opens
  every bucket of the project, the backups too (D108 part 3, H-271).

**The flag.** ``OBJECT_STORE_ENABLED``, default off. :func:`enabled` says
whether a caller may use the store. Each call raises
:class:`ObjectStoreDisabled` when it is off, so no caller writes by accident.

**Async.** boto3 is synchronous. Each call runs in a worker thread. The key
check runs on the caller's frame before the thread starts, so a refused key
never reaches the store.

No other module may import an S3 client. The fence greps the tree.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("acb_common.object_store")

__all__ = [
    "ObjectKeyRefused",
    "ObjectNotFound",
    "ObjectStoreDisabled",
    "ObjectStoreError",
    "ObjectTooLarge",
    "StoredObject",
    "check_key",
    "delete",
    "enabled",
    "get_bytes",
    "head",
    "iter_chunks",
    "new_key",
    "put_bytes",
    "put_file",
]

# ── Keys ──────────────────────────────────────────────────────────────────────

_ORG_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_APP_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,12}$")
_KEY_RE = re.compile(
    rf"^(?P<org>{_ORG_RE})/(?P<app>[a-z][a-z0-9_-]{{1,31}})/(?P<obj>[0-9a-f]{{32}}(?:\.[a-z0-9]{{1,12}})?)$"
)

_GET_BYTES_MAX = 64 * 1024 * 1024
_CHUNK = 1024 * 1024
# Renew the JWT when less than this is left, or half its life for a short one.
# botocore freezes the credentials of a call, so the token of a long part
# upload must outlive the upload: 900 s holds 512 MB at about 0.6 MB/s.
_TOKEN_MARGIN_SECS = 900


class ObjectStoreError(RuntimeError):
    """The store refused or failed a call. The message names no secret."""


class ObjectStoreDisabled(ObjectStoreError):
    """``OBJECT_STORE_ENABLED`` is off, or the store is not configured."""


class ObjectKeyRefused(ObjectStoreError):
    """The key is not well formed, or it belongs to another organization."""


class ObjectTooLarge(ObjectStoreError):
    """The object is larger than the cap of the call."""


class ObjectNotFound(ObjectStoreError):
    """No object at that key."""


@dataclass(frozen=True)
class StoredObject:
    """What a put returns, and what :func:`head` reads."""

    key: str
    size: int
    content_type: str
    sha256: str | None = None


def enabled() -> bool:
    """True when the flag is on and the store is configured. Fails closed."""
    try:
        s = get_settings()
    except Exception:
        return False
    return (
        getattr(s, "object_store_enabled", False) is True
        and bool(s.object_store_endpoint)
        and bool(s.object_store_bucket)
    )


def _norm_org(value: object) -> str:
    org = str(value).strip().lower()
    if not re.fullmatch(_ORG_RE, org):
        raise ObjectKeyRefused("the organization id is not a UUID")
    return org


def _tenant(organization_id: str | None) -> str:
    """The organization of this call.

    The bound tenant wins. An explicit *organization_id* is for a job with no
    bound tenant. When both are present they must be equal, or the call is
    refused (review, 2026-10-20): the key check is the only isolation of the
    store, so it must not take its answer from the caller. With neither, the
    call is refused, as ``tenant_session`` refuses.
    """
    try:
        from acb_common.db import current_tenant
    except ImportError:  # pragma: no cover
        current_tenant = None  # type: ignore[assignment]
    bound = current_tenant() if current_tenant else None
    if bound and organization_id and _norm_org(organization_id) != _norm_org(bound):
        raise ObjectKeyRefused("organization_id differs from the bound tenant")
    org = bound or organization_id
    if not org:
        from acb_common.db import TenantUnbound

        raise TenantUnbound("the object store needs a tenant: bind one, or pass organization_id")
    return _norm_org(org)


def new_key(app: str, *, suffix: str = "", organization_id: str | None = None) -> str:
    """A new key ``<org>/<app>/<32 hex>[suffix]`` for the caller's tenant.

    *app* is a short slug (``tasks``, ``notes``, ``agents``). *suffix* is an
    optional lower-case extension such as ``.pdf``. The object id is random,
    so a file name from a member never becomes a key.
    """
    if not _APP_RE.fullmatch(app or ""):
        raise ObjectKeyRefused(f"app slug {app!r} is not allowed")
    if suffix and not _SUFFIX_RE.fullmatch(suffix):
        raise ObjectKeyRefused("the suffix must look like '.pdf'")
    return f"{_tenant(organization_id)}/{app}/{uuid.uuid4().hex}{suffix}"


def check_key(key: str, *, organization_id: str | None = None) -> str:
    """Return *key* when it is well formed and belongs to the caller's tenant.

    Raises :class:`ObjectKeyRefused` otherwise. This is the isolation of the
    store, so every read, head and delete calls it first.
    """
    m = _KEY_RE.fullmatch(key or "")
    if m is None:
        raise ObjectKeyRefused("the key is not an object store key")
    if m.group("org") != _tenant(organization_id):
        raise ObjectKeyRefused("the key belongs to another organization")
    return key


# ── Sign-in and the client ────────────────────────────────────────────────────


class _Credentials:
    """The credentials of the configured mode, with the JWT cached."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_until = 0.0
        self._client: Any = None
        self._client_token: str | None = None
        self._client_config: tuple | None = None

    @staticmethod
    def _supabase_mode(s: Any) -> bool:
        return bool(s.object_store_auth_email and s.object_store_auth_password and s.object_store_supabase_url)

    @staticmethod
    def _refuse_project_key(s: Any) -> None:
        host = (urllib.parse.urlparse(s.object_store_endpoint).hostname or "").rstrip(".").lower()
        supabase_host = host.endswith((".supabase.co", ".supabase.in"))
        if (supabase_host or s.object_store_supabase_url) and not _Credentials._supabase_mode(s):
            raise ObjectStoreDisabled(
                "a Supabase endpoint needs the files user (D108). A project-wide S3 key is refused."
            )

    def _jwt(self, s: Any) -> str:
        now = time.monotonic()
        if self._token and now < self._token_until:
            return self._token
        url = s.object_store_supabase_url.rstrip("/") + "/auth/v1/token?grant_type=password"
        body = json.dumps({"email": s.object_store_auth_email, "password": s.object_store_auth_password}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"apikey": s.object_store_secret_access_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            # The exception text can carry the URL, never the password.
            raise ObjectStoreError(f"the files user could not sign in: {type(exc).__name__}") from None
        token = str(payload.get("access_token") or "")
        if not token:
            raise ObjectStoreError("the files user got no token")
        ttl = float(payload.get("expires_in") or 3600)
        self._token = token
        self._token_until = now + max(0.0, ttl - min(_TOKEN_MARGIN_SECS, ttl / 2))
        return token

    def client(self) -> Any:
        """A boto3 S3 client for the current credentials. Thread-safe."""
        s = get_settings()
        if not enabled():
            raise ObjectStoreDisabled("the object store is off")
        self._refuse_project_key(s)
        import boto3
        from botocore.config import Config

        with self._lock:
            token = self._jwt(s) if self._supabase_mode(s) else None
            config = (s.object_store_endpoint, s.object_store_region, s.object_store_access_key_id)
            if self._client is not None and self._client_token == token and self._client_config == config:
                return self._client
            self._client = boto3.client(
                "s3",
                endpoint_url=s.object_store_endpoint,
                region_name=s.object_store_region or "us-east-1",
                aws_access_key_id=s.object_store_access_key_id,
                aws_secret_access_key=s.object_store_secret_access_key,
                aws_session_token=token,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    # S3-compatible stores (Supabase, MinIO) do not all take
                    # the CRC checksums botocore sends by default since 1.36.
                    # A part upload then fails its check on read. Send and
                    # check one only where the protocol asks for it.
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=60,
                ),
            )
            self._client_token = token
            self._client_config = config
            return self._client


_CREDS = _Credentials()


def _bucket() -> str:
    return get_settings().object_store_bucket


def _cap(max_bytes: int | None) -> int:
    limit = int(get_settings().object_store_max_object_mb) * 1024 * 1024
    return min(limit, max_bytes) if max_bytes else limit


def _missing(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


def _fail(op: str, exc: Exception) -> ObjectStoreError:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", "")) or type(exc).__name__
    _log.warning("object_store.failed", op=op, code=code)
    return ObjectStoreError(f"the object store refused {op}: {code}")


# ── The calls ─────────────────────────────────────────────────────────────────


def _sync_put_bytes(key: str, data: bytes, content_type: str) -> StoredObject:
    try:
        _CREDS.client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)
    except ObjectStoreError:
        raise
    except Exception as exc:
        raise _fail("put", exc) from None
    return StoredObject(key, len(data), content_type, hashlib.sha256(data).hexdigest())


async def put_bytes(
    app: str, data: bytes, *, content_type: str = "application/octet-stream",
    suffix: str = "", organization_id: str | None = None, max_bytes: int | None = None,
) -> StoredObject:
    """Store *data* under a new key of the caller's tenant, and return it."""
    if not enabled():
        raise ObjectStoreDisabled("the object store is off")
    if len(data) > _cap(max_bytes):
        raise ObjectTooLarge(f"{len(data)} bytes is over the cap")
    key = new_key(app, suffix=suffix, organization_id=organization_id)
    return await asyncio.to_thread(_sync_put_bytes, key, data, content_type)


def _sync_put_file(key: str, path: Path, content_type: str) -> StoredObject:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    try:
        # upload_file splits a large file into parts by itself.
        _CREDS.client().upload_file(str(path), _bucket(), key, ExtraArgs={"ContentType": content_type})
    except ObjectStoreError:
        raise
    except Exception as exc:
        raise _fail("put", exc) from None
    return StoredObject(key, path.stat().st_size, content_type, digest.hexdigest())


async def put_file(
    app: str, path: str | Path, *, content_type: str = "application/octet-stream",
    suffix: str = "", organization_id: str | None = None, max_bytes: int | None = None,
) -> StoredObject:
    """Store a local file under a new key, in parts when it is large."""
    if not enabled():
        raise ObjectStoreDisabled("the object store is off")
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ObjectStoreError("the source is not a regular file")
    if p.stat().st_size > _cap(max_bytes):
        raise ObjectTooLarge(f"{p.stat().st_size} bytes is over the cap")
    key = new_key(app, suffix=suffix, organization_id=organization_id)
    return await asyncio.to_thread(_sync_put_file, key, p, content_type)


def _sync_get(key: str, max_bytes: int) -> bytes:
    try:
        obj = _CREDS.client().get_object(Bucket=_bucket(), Key=key)
    except ObjectStoreError:
        raise
    except Exception as exc:
        if _missing(exc):
            raise ObjectNotFound("no object at that key") from None
        raise _fail("get", exc) from None
    if int(obj.get("ContentLength") or 0) > max_bytes:
        obj["Body"].close()
        raise ObjectTooLarge("the object is over the cap of get_bytes; use iter_chunks")
    data = obj["Body"].read(max_bytes + 1)
    obj["Body"].close()
    if len(data) > max_bytes:
        raise ObjectTooLarge("the object is over the cap of get_bytes; use iter_chunks")
    return data


async def get_bytes(key: str, *, organization_id: str | None = None, max_bytes: int = _GET_BYTES_MAX) -> bytes:
    """The bytes at *key*. For a large object, use :func:`iter_chunks`."""
    if not enabled():
        raise ObjectStoreDisabled("the object store is off")
    check_key(key, organization_id=organization_id)
    return await asyncio.to_thread(_sync_get, key, max_bytes)


def _sync_open(key: str, byte_range: tuple[int, int] | None) -> Any:
    kwargs: dict[str, Any] = {"Bucket": _bucket(), "Key": key}
    if byte_range is not None:
        start, end = byte_range
        if start < 0 or end < start:
            raise ObjectStoreError("the range is not valid")
        kwargs["Range"] = f"bytes={start}-{end}"
    try:
        return _CREDS.client().get_object(**kwargs)["Body"]
    except ObjectStoreError:
        raise
    except Exception as exc:
        if _missing(exc):
            raise ObjectNotFound("no object at that key") from None
        raise _fail("get", exc) from None


async def iter_chunks(
    key: str, *, organization_id: str | None = None, chunk_size: int = _CHUNK,
    byte_range: tuple[int, int] | None = None,
) -> AsyncIterator[bytes]:
    """Open the object at *key* and return an iterator of its chunks.

    *byte_range* is inclusive. Await this call itself: the flag, the tenant
    check and the open all run here, so a route turns a refusal or a missing
    object into a 403 or a 404 before it sends a status (review, 2026-10-20).
    Then iterate the result::

        chunks = await iter_chunks(key)
        return StreamingResponse(chunks)
    """
    if not enabled():
        raise ObjectStoreDisabled("the object store is off")
    check_key(key, organization_id=organization_id)
    body = await asyncio.to_thread(_sync_open, key, byte_range)
    return _stream(body, chunk_size)


async def _stream(body: Any, chunk_size: int) -> AsyncIterator[bytes]:
    try:
        while True:
            block = await asyncio.to_thread(body.read, chunk_size)
            if not block:
                break
            yield block
    finally:
        await asyncio.to_thread(body.close)


def _sync_head(key: str) -> StoredObject | None:
    try:
        meta = _CREDS.client().head_object(Bucket=_bucket(), Key=key)
    except ObjectStoreError:
        raise
    except Exception as exc:
        if _missing(exc):
            return None
        raise _fail("head", exc) from None
    return StoredObject(key, int(meta.get("ContentLength") or 0), str(meta.get("ContentType") or ""))


async def head(key: str, *, organization_id: str | None = None) -> StoredObject | None:
    """The size and type of the object at *key*, or ``None`` when it is gone."""
    if not enabled():
        raise ObjectStoreDisabled("the object store is off")
    check_key(key, organization_id=organization_id)
    return await asyncio.to_thread(_sync_head, key)


def _sync_delete(key: str) -> None:
    try:
        _CREDS.client().delete_object(Bucket=_bucket(), Key=key)
    except ObjectStoreError:
        raise
    except Exception as exc:
        if _missing(exc):
            return
        raise _fail("delete", exc) from None


async def delete(key: str, *, organization_id: str | None = None) -> None:
    """Delete the object at *key*. A missing object is not an error."""
    if not enabled():
        raise ObjectStoreDisabled("the object store is off")
    check_key(key, organization_id=organization_id)
    await asyncio.to_thread(_sync_delete, key)
