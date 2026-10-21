"""MT-1g-1 — the object store seam (D108).

Spec: ``project-docs/specs/saas_multitenancy.md`` MT-1g. Seam:
``packages/acb_common/acb_common/object_store.py``.

Two halves.

**Hermetic** (no store). The key rules, the tenant check that IS the store's
isolation, the flag, the refusal of a project-wide key on a Supabase
endpoint, the JWT cache, the caps, and the fence that no other module talks
to S3.

**Round trip** (a real S3 server). Set ``OBJECT_STORE_TEST_ENDPOINT``, for
example to a local MinIO or ``moto_server`` at ``http://127.0.0.1:19000``.
Without it the class skips, as the R8 suites skip without a database. It
proves put, get, head, a ranged stream, a large file in parts, the get cap,
a missing key and delete against the real protocol.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import uuid
from pathlib import Path

import pytest
from acb_common import get_settings
from acb_common import object_store as store
from acb_common.db import TenantUnbound, bind_tenant, release_tenant

_REPO = Path(__file__).resolve().parents[2]
_SEAM = _REPO / "packages" / "acb_common" / "acb_common" / "object_store.py"

ORG_A = "5150aaaa-0000-4000-8000-0000000000a1"
ORG_B = "5150bbbb-0000-4000-8000-0000000000b2"


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch):
    """Store settings for a plain-key endpoint, and a fresh credential cache."""
    s = get_settings()
    for name, value in {
        "object_store_enabled": True,
        "object_store_endpoint": os.environ.get("OBJECT_STORE_TEST_ENDPOINT", "http://127.0.0.1:1"),
        "object_store_region": "us-east-1",
        "object_store_bucket": "metorite-files-test",
        "object_store_access_key_id": "testaccess",
        "object_store_secret_access_key": "testsecret",
        "object_store_supabase_url": "",
        "object_store_auth_email": "",
        "object_store_auth_password": "",
        "object_store_max_object_mb": 512,
    }.items():
        monkeypatch.setattr(s, name, value)
    monkeypatch.setattr(store, "_CREDS", store._Credentials())
    return s


@pytest.fixture
def org_a():
    token = bind_tenant(ORG_A)
    try:
        yield ORG_A
    finally:
        release_tenant(token)


# ── Keys and the tenant check ───────────────────────────────────────────────


def test_a_new_key_carries_the_bound_tenant_and_a_random_id(org_a) -> None:
    key = store.new_key("tasks", suffix=".pdf")
    assert re.fullmatch(rf"{ORG_A}/tasks/[0-9a-f]{{32}}\.pdf", key)
    assert store.new_key("tasks") != store.new_key("tasks")
    # A bound tenant wins, and an explicit organization that differs is refused.
    with pytest.raises(store.ObjectKeyRefused, match="differs"):
        store.new_key("notes", organization_id=ORG_B)
    assert store.new_key("notes", organization_id=ORG_A).startswith(f"{ORG_A}/notes/")


def test_a_job_with_no_bound_tenant_names_its_organization() -> None:
    assert store.new_key("notes", organization_id=ORG_B).startswith(f"{ORG_B}/notes/")
    theirs = f"{ORG_B}/notes/{uuid.uuid4().hex}"
    assert store.check_key(theirs, organization_id=ORG_B) == theirs
    with pytest.raises(store.ObjectKeyRefused, match="another organization"):
        store.check_key(theirs, organization_id=ORG_A)


def test_a_key_needs_a_tenant() -> None:
    with pytest.raises(TenantUnbound):
        store.new_key("tasks")
    with pytest.raises(TenantUnbound):
        store.check_key(f"{ORG_A}/tasks/{uuid.uuid4().hex}")


@pytest.mark.parametrize("app", ["", "T", "Tasks", "tasks/x", "../x", "a" * 40, "1tasks", "tasks\n"])
def test_a_bad_app_slug_is_refused(org_a, app: str) -> None:
    with pytest.raises(store.ObjectKeyRefused):
        store.new_key(app)


@pytest.mark.parametrize("suffix", ["pdf", ".PDF", "./x", ".a/b", "." + "x" * 13, ".pdf\n"])
def test_a_bad_suffix_is_refused(org_a, suffix: str) -> None:
    with pytest.raises(store.ObjectKeyRefused):
        store.new_key("tasks", suffix=suffix)


def test_a_key_of_another_organization_is_refused(org_a) -> None:
    mine = store.new_key("tasks")
    assert store.check_key(mine) == mine
    theirs = f"{ORG_B}/tasks/{uuid.uuid4().hex}"
    with pytest.raises(store.ObjectKeyRefused, match="another organization"):
        store.check_key(theirs)
    # A caller cannot widen the bound tenant by naming the key's organization.
    with pytest.raises(store.ObjectKeyRefused, match="differs"):
        store.check_key(theirs, organization_id=ORG_B)
    with pytest.raises(store.ObjectKeyRefused, match="differs"):
        store.check_key(mine, organization_id=ORG_B)


@pytest.mark.parametrize(
    "key",
    [
        "",
        f"{ORG_A}/tasks",
        f"{ORG_A}/tasks/../{ORG_B}/tasks/" + "a" * 32,
        f"{ORG_A}/tasks/" + "a" * 31,
        f"{ORG_A}/tasks/" + "A" * 32,
        f"{ORG_A}/tasks/" + "a" * 32 + "/x",
        f"/{ORG_A}/tasks/" + "a" * 32,
        f"{ORG_A.upper()}/tasks/" + "a" * 32,
        f"{ORG_A}/tasks/" + "a" * 32 + ".PDF",
        f"{ORG_A}/tasks/" + "a" * 32 + "\n",
        f"{ORG_A}/tasks\n/" + "a" * 32,
    ],
)
def test_a_malformed_key_is_refused(org_a, key: str) -> None:
    with pytest.raises(store.ObjectKeyRefused):
        store.check_key(key)


async def test_every_read_checks_the_tenant_before_any_call(cfg, org_a, monkeypatch) -> None:
    """A foreign key fails before the store is asked, so nothing leaks."""
    called: list[str] = []
    monkeypatch.setattr(store._CREDS, "client", lambda: called.append("client"))
    theirs = f"{ORG_B}/tasks/{uuid.uuid4().hex}"
    with pytest.raises(store.ObjectKeyRefused):
        await store.get_bytes(theirs)
    with pytest.raises(store.ObjectKeyRefused):
        await store.head(theirs)
    with pytest.raises(store.ObjectKeyRefused):
        await store.delete(theirs)
    with pytest.raises(store.ObjectKeyRefused):
        await store.iter_chunks(theirs)  # refused at the call, before any status
    assert called == []


# ── The flag and the sign-in rules ─────────────────────────────────────────


async def test_every_call_is_refused_with_the_flag_off(cfg, org_a, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "object_store_enabled", False)
    assert store.enabled() is False
    key = store.new_key("tasks")
    with pytest.raises(store.ObjectStoreDisabled):
        await store.put_bytes("tasks", b"x")
    with pytest.raises(store.ObjectStoreDisabled):
        await store.get_bytes(key)
    with pytest.raises(store.ObjectStoreDisabled):
        await store.head(key)
    with pytest.raises(store.ObjectStoreDisabled):
        await store.delete(key)
    with pytest.raises(store.ObjectStoreDisabled):
        await store.iter_chunks(key)
    with pytest.raises(store.ObjectStoreDisabled):
        await store.put_file("tasks", Path(__file__))


def test_the_store_is_off_without_an_endpoint_or_a_bucket(cfg, monkeypatch) -> None:
    assert store.enabled() is True
    monkeypatch.setattr(cfg, "object_store_bucket", "")
    assert store.enabled() is False
    monkeypatch.setattr(cfg, "object_store_bucket", "b")
    monkeypatch.setattr(cfg, "object_store_endpoint", "")
    assert store.enabled() is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://abcd.storage.supabase.co/storage/v1/s3",
        "https://ABCD.STORAGE.SUPABASE.CO/storage/v1/s3",
        "https://abcd.storage.supabase.co./storage/v1/s3",
        "https://abcd.supabase.in/storage/v1/s3",
    ],
)
def test_a_supabase_endpoint_refuses_a_plain_key_pair(cfg, monkeypatch, endpoint: str) -> None:
    """D108 part 3: a Supabase S3 key opens every bucket, the backups too."""
    monkeypatch.setattr(cfg, "object_store_endpoint", endpoint)
    with pytest.raises(store.ObjectStoreDisabled, match="files user"):
        store._CREDS.client()


def test_a_supabase_url_without_the_files_user_refuses_a_key_pair(cfg, monkeypatch) -> None:
    """A custom domain hides the Supabase host, so the URL setting decides too."""
    monkeypatch.setattr(cfg, "object_store_endpoint", "https://files.example.com/s3")
    monkeypatch.setattr(cfg, "object_store_supabase_url", "https://abcd.supabase.co")
    with pytest.raises(store.ObjectStoreDisabled, match="files user"):
        store._CREDS.client()


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _supabase(cfg, monkeypatch, *, expires_in: int = 3600):
    monkeypatch.setattr(cfg, "object_store_endpoint", "https://abcd.storage.supabase.co/storage/v1/s3")
    monkeypatch.setattr(cfg, "object_store_supabase_url", "https://abcd.supabase.co")
    monkeypatch.setattr(cfg, "object_store_auth_email", "files@metorite.test")
    monkeypatch.setattr(cfg, "object_store_auth_password", "p@ss-not-in-any-log")
    monkeypatch.setattr(cfg, "object_store_access_key_id", "abcd")
    monkeypatch.setattr(cfg, "object_store_secret_access_key", "anon-key")
    calls: list[dict] = []

    def fake_urlopen(req, timeout=0):
        calls.append({"url": req.full_url, "apikey": req.get_header("Apikey"), "body": json.loads(req.data)})
        return _FakeResp(json.dumps({"access_token": f"jwt-{len(calls)}", "expires_in": expires_in}).encode())

    monkeypatch.setattr(store.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_the_files_user_signs_in_once_and_the_jwt_is_the_session_token(cfg, monkeypatch) -> None:
    calls = _supabase(cfg, monkeypatch)
    client = store._CREDS.client()
    assert store._CREDS.client() is client, "a cached JWT reuses the client"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://abcd.supabase.co/auth/v1/token?grant_type=password"
    assert calls[0]["apikey"] == "anon-key"
    assert calls[0]["body"] == {"email": "files@metorite.test", "password": "p@ss-not-in-any-log"}
    creds = client._request_signer._credentials.get_frozen_credentials()
    assert creds.token == "jwt-1" and creds.access_key == "abcd"


def test_the_jwt_is_renewed_before_it_expires(cfg, monkeypatch) -> None:
    calls = _supabase(cfg, monkeypatch, expires_in=3600)
    first = store._CREDS.client()
    store._CREDS._token_until = 0.0  # the margin has passed
    second = store._CREDS.client()
    assert len(calls) == 2 and second is not first
    assert second._request_signer._credentials.get_frozen_credentials().token == "jwt-2"


@pytest.mark.parametrize(("ttl", "kept"), [(3600, 2700), (600, 300), (60, 30)])
def test_a_jwt_is_kept_for_at_most_its_life_less_the_margin(cfg, monkeypatch, ttl: int, kept: int) -> None:
    """A long part upload must not outlive its token (review, 2026-10-20)."""
    _supabase(cfg, monkeypatch, expires_in=ttl)
    clock = [1000.0]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
    store._CREDS.client()
    assert store._CREDS._token_until == pytest.approx(1000.0 + kept)


def test_a_failed_sign_in_names_no_secret(cfg, monkeypatch) -> None:
    _supabase(cfg, monkeypatch)

    def boom(req, timeout=0):
        raise OSError(f"refused {req.full_url} p@ss-not-in-any-log")

    monkeypatch.setattr(store.urllib.request, "urlopen", boom)
    with pytest.raises(store.ObjectStoreError) as err:
        store._CREDS.client()
    assert "p@ss" not in str(err.value) and "OSError" in str(err.value)


async def test_a_put_over_the_cap_is_refused(cfg, org_a, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg, "object_store_max_object_mb", 1)
    with pytest.raises(store.ObjectTooLarge):
        await store.put_bytes("tasks", b"x" * (1024 * 1024 + 1))
    with pytest.raises(store.ObjectTooLarge):
        await store.put_bytes("tasks", b"x" * 11, max_bytes=10)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(store.ObjectTooLarge):
        await store.put_file("tasks", big)
    link = tmp_path / "link.bin"
    link.symlink_to(big)
    with pytest.raises(store.ObjectStoreError, match="regular file"):
        await store.put_file("tasks", link)


# ── The fence: one seam ─────────────────────────────────────────────────────

_S3_MODULES = {"boto3", "botocore", "aioboto3", "aiobotocore", "minio", "s3fs", "smart_open"}
_S3_PACKAGES = re.compile(r'"(@aws-sdk/client-s3|@aws-sdk/lib-storage|@supabase/storage-js|minio|aws-sdk)"\s*:')
_SKIP_PARTS = {"node_modules", ".venv", "__pycache__", ".next", "tests"}


def _s3_imports(source: str) -> list[str]:
    """Every way a module can load an S3 client: import, from-import,
    ``__import__("x")`` and ``importlib.import_module("x")``."""
    import ast

    found: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if called in ("__import__", "import_module") and isinstance(node.args[0].value, str):
                names = [node.args[0].value]
        found += [n for n in names if n.split(".")[0] in _S3_MODULES]
    return found


def test_the_fence_sees_every_import_form() -> None:
    for src in (
        "import os, boto3",
        "import json; import boto3",
        "def f():\n    import botocore.config",
        "from boto3.session import Session",
        "__import__('boto3')",
        "import importlib\nimportlib.import_module('aiobotocore.session')",
    ):
        assert _s3_imports(src), src
    assert _s3_imports("import json\nfrom acb_common import object_store") == []


def _tracked(*patterns: str) -> list[Path]:
    """The tracked files that match, from git. A walk of the tree would
    descend into every ``node_modules`` first, and took 60 s."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns], cwd=_REPO, check=True, capture_output=True,
    ).stdout.decode("utf-8")
    return [_REPO / name for name in out.split("\0") if name]


def test_no_module_but_the_seam_imports_an_s3_client() -> None:
    """D108 part 2. A second S3 client is a second seam, and it would skip
    the tenant check. Tests may use one to set up a bucket."""
    offenders: list[str] = []
    for py in _tracked("*.py"):
        rel = py.relative_to(_REPO)
        if py == _SEAM or _SKIP_PARTS.intersection(rel.parts) or not py.is_file():
            continue
        if _s3_imports(py.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(rel))
    for pkg in _tracked("package.json", "*/package.json"):
        rel = pkg.relative_to(_REPO)
        if _SKIP_PARTS.intersection(rel.parts) or not pkg.is_file():
            continue
        if _S3_PACKAGES.search(pkg.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(rel))
    assert len(_tracked("*.py")) > 500, "git ls-files found too few files to trust"
    assert offenders == []


# ── Round trip against a real S3 server ─────────────────────────────────────

_ENDPOINT = os.environ.get("OBJECT_STORE_TEST_ENDPOINT", "")


@pytest.mark.skipif(not _ENDPOINT, reason="set OBJECT_STORE_TEST_ENDPOINT to a MinIO or moto_server")
class TestRoundTrip:
    @pytest.fixture(autouse=True)
    def _bucket(self, cfg):
        import boto3

        s3 = boto3.client(
            "s3", endpoint_url=_ENDPOINT, region_name="us-east-1",
            aws_access_key_id=cfg.object_store_access_key_id,
            aws_secret_access_key=cfg.object_store_secret_access_key,
        )
        with contextlib.suppress(s3.exceptions.BucketAlreadyOwnedByYou):
            s3.create_bucket(Bucket=cfg.object_store_bucket)

    async def test_put_get_head_delete(self, org_a) -> None:
        put = await store.put_bytes("tasks", b"hello object store", content_type="text/plain", suffix=".txt")
        assert put.key.startswith(f"{ORG_A}/tasks/") and put.size == 18
        assert await store.get_bytes(put.key) == b"hello object store"
        meta = await store.head(put.key)
        assert meta is not None and meta.size == 18 and meta.content_type == "text/plain"
        await store.delete(put.key)
        assert await store.head(put.key) is None
        with pytest.raises(store.ObjectNotFound):
            await store.get_bytes(put.key)
        with pytest.raises(store.ObjectNotFound):
            await store.iter_chunks(put.key)  # a 404 at the call, before any status
        await store.delete(put.key)  # a second delete is not an error

    async def test_a_ranged_stream_and_the_get_cap(self, org_a) -> None:
        data = bytes(range(256)) * 4096  # 1 MiB
        put = await store.put_bytes("notes", data, suffix=".bin")
        chunks = [c async for c in await store.iter_chunks(put.key, chunk_size=64 * 1024)]
        assert b"".join(chunks) == data and len(chunks) == 16
        part = b"".join([c async for c in await store.iter_chunks(put.key, byte_range=(10, 19))])
        assert part == data[10:20]
        with pytest.raises(store.ObjectTooLarge):
            await store.get_bytes(put.key, max_bytes=1000)
        await store.delete(put.key)

    async def test_a_large_file_goes_up_in_parts(self, org_a, tmp_path) -> None:
        big = tmp_path / "audio.webm"
        big.write_bytes(os.urandom(9 * 1024 * 1024))  # over boto3's 8 MiB part threshold
        put = await store.put_file("notes", big, content_type="audio/webm", suffix=".webm")
        assert put.size == big.stat().st_size
        got = b"".join([c async for c in await store.iter_chunks(put.key)])
        assert got == big.read_bytes()
        await store.delete(put.key)

    async def test_another_tenant_cannot_read_a_key(self, org_a) -> None:
        put = await store.put_bytes("tasks", b"org a only")
        token = bind_tenant(ORG_B)
        try:
            with pytest.raises(store.ObjectKeyRefused):
                await store.get_bytes(put.key)
            with pytest.raises(store.ObjectKeyRefused):
                await store.iter_chunks(put.key)
        finally:
            release_tenant(token)
        await store.delete(put.key)
