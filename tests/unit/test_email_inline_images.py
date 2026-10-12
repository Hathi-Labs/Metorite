"""WS-17 — the inline ``cid:`` pictures of a mail body.

Spec: ``project-docs/specs/email_app_master_plan.md`` §16.6. The owner's
report of 2026-10-13: a pasted screenshot showed as a broken picture with the
alt text "image.png". The pane could not fetch ``cid:``, and nothing served
the part.

R7 fences named here, each a test class:

* ``email-inline-outlook`` (:class:`TestTheOutlookRead`). The list read
  selects base properties only, so Graph sends no bytes of a real file. Only
  an ``isInline`` or ``image/*`` file part is read, at most ``max_count``. A
  part over ``max_bytes``, an Outlook item, and a part whose read fails are
  skipped. A 429 that remains raises.
* ``email-inline-gmail`` (:class:`TestTheGmailRead`). A part with a
  Content-ID, in the tree or by ``attachmentId``. An attached mail is never
  opened.
* ``email-inline-sniff`` (:class:`TestTheSniff`). The type comes from the
  bytes. SVG and HTML are never a picture.
* ``email-inline-route`` (:class:`TestTheRoute`). The owner read runs first,
  and a message of another member is 404 with no cache read and no provider
  call. One provider read serves a burst of requests. A cache hit and a
  cached list of names make no provider call. No session is open across the
  provider call. The response holds ``nosniff`` and a CSP. A 429 answers 503.
* ``email-inline-owner-r8`` (:class:`TestTheOwnerCheckOnARealDatabase`, R8).
  The owner read on the real catalog as the non-privileged role.

Run::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_inline_images.py -v -rs
"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis as tr
from acb_common.tenant_redis import TenantRedis
from email_ingestion.providers import outlook as outlook_mod
from email_ingestion.providers.base import (
    BaseEmailProvider,
    InlineImage,
    normalize_content_id,
)
from email_ingestion.providers.gmail import GMAIL_API_BASE, GmailProvider
from email_ingestion.providers.outlook import GRAPH_API_BASE, OutlookProvider
from fastapi import HTTPException
from gateway.routes.email.transport import inline_images as ii

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_html_tier import (
    _assert_non_priv,
    _bound,
    _purge,
    _seed_cold_message,
)

# The two-org phase-4 fixture and its DB gate. ``promoted`` and
# ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
MSG_ID = "33333333-3333-4333-8333-333333333333"
OWNER = "owner@inline.test"
SECOND = "second@inline.test"

#: A 24 x 12 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAMCAIAAAD3UuoiAAAAF0lEQVR4nGO4aaVNFcQwatCoQaMG4UMAAZdl0KrP06AAAAAASUVORK5CYII="
)
GIF = b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
HTML = b"<html><script>alert(1)</script></html>"


def _png_like(n: int) -> bytes:
    """A PNG head and *n* bytes in all."""
    return PNG[:16] + b"\x00" * max(0, n - 16)


# ── 1. The Outlook read ─────────────────────────────────────────────────────


class _Graph:
    """A fake Graph on ``httpx.MockTransport``. It records each request."""

    def __init__(self, parts: list[dict[str, Any]], *, page_size: int = 100) -> None:
        self.parts = parts
        self.page_size = page_size
        self.requests: list[httpx.Request] = []
        self.status: dict[str, int] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        base = "/v1.0/me/messages/pm-1/attachments"
        if path == base:
            skip = int(request.url.params.get("$skip", "0"))
            page = self.parts[skip:skip + self.page_size]
            listed = [{k: p[k] for k in ("@odata.type", "id", "name", "contentType",
                                         "size", "isInline") if k in p} for p in page]
            body: dict[str, Any] = {"value": listed}
            if skip + self.page_size < len(self.parts):
                body["@odata.nextLink"] = (
                    f"{GRAPH_API_BASE}/me/messages/pm-1/attachments?$skip={skip + self.page_size}")
            return httpx.Response(200, json=body)
        att_id = path.rsplit("/", 1)[-1]
        if att_id in self.status:
            return httpx.Response(self.status[att_id], json={"error": {}},
                                  headers={"Retry-After": "0"})
        for p in self.parts:
            if p["id"] == att_id:
                body = {k: v for k, v in p.items() if k != "_bytes"}
                body["contentBytes"] = base64.b64encode(p["_bytes"]).decode()
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {}})

    def part_reads(self) -> list[str]:
        base = "/v1.0/me/messages/pm-1/attachments"
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests
                if r.url.path.startswith(base + "/")]


def _part(att_id: str, *, cid: str, data: bytes = PNG, inline: bool = True,
          ctype: str = "image/png", odata: str = "#microsoft.graph.fileAttachment",
          size: int | None = None) -> dict[str, Any]:
    return {"@odata.type": odata, "id": att_id, "name": f"{att_id}.png",
            "contentType": ctype, "size": len(data) if size is None else size,
            "isInline": inline, "contentId": cid, "_bytes": data}


def _outlook(graph: _Graph) -> OutlookProvider:
    provider = OutlookProvider({"access_token": "t", "refresh_token": "r"})
    provider._http = httpx.AsyncClient(
        base_url=GRAPH_API_BASE, transport=httpx.MockTransport(graph.handle))
    return provider


class TestTheOutlookRead:

    async def test_the_list_reads_no_bytes_and_each_inline_part_is_read(self) -> None:
        graph = _Graph([
            _part("a-logo", cid="<image001.png@01DD5A75.A7B8EA10>"),
            _part("a-chart", cid="ii_mv5obra50", data=GIF, ctype="image/gif"),
            # A real file: a PDF that is not inline. Its bytes are never read.
            _part("a-pdf", cid="", data=b"%PDF-1.7" * 1000, inline=False,
                  ctype="application/pdf"),
        ])
        got = await _outlook(graph).get_inline_images(
            "pm-1", max_count=100, max_bytes=5_000_000)
        assert sorted(i.content_id for i in got) == [
            "ii_mv5obra50", "image001.png@01dd5a75.a7b8ea10"]
        assert {i.content_id: i.content for i in got}["ii_mv5obra50"] == GIF
        listing = graph.requests[0]
        assert listing.url.params.get("$select") == "id,name,contentType,size,isInline"
        assert "$expand" not in listing.url.params
        assert "contentBytes" not in listing.url.params.get("$select", "")
        assert sorted(graph.part_reads()) == ["a-chart", "a-logo"], (
            "the read fetched a part that is not an inline picture")

    async def test_an_image_with_no_inline_flag_still_counts(self) -> None:
        graph = _Graph([_part("a-1", cid="logo", inline=False)])
        got = await _outlook(graph).get_inline_images("pm-1", max_count=10, max_bytes=10_000)
        assert [i.content_id for i in got] == ["logo"]

    async def test_an_item_a_big_part_and_a_failed_read_are_skipped(self) -> None:
        graph = _Graph([
            _part("a-item", cid="x", odata="#microsoft.graph.itemAttachment"),
            _part("a-big", cid="big", data=_png_like(300_000)),
            _part("a-gone", cid="gone"),
            _part("a-ok", cid="ok"),
        ])
        graph.status["a-gone"] = 404
        got = await _outlook(graph).get_inline_images("pm-1", max_count=10, max_bytes=100_000)
        assert [i.content_id for i in got] == ["ok"]
        assert "a-item" not in graph.part_reads() and "a-big" not in graph.part_reads()

    async def test_max_count_bounds_the_reads(self) -> None:
        graph = _Graph([_part(f"a-{n}", cid=f"c{n}") for n in range(12)])
        got = await _outlook(graph).get_inline_images("pm-1", max_count=5, max_bytes=10_000)
        assert len(got) == 5 and len(graph.part_reads()) == 5

    async def test_the_list_follows_the_next_link(self) -> None:
        graph = _Graph([_part(f"a-{n}", cid=f"c{n}") for n in range(5)], page_size=2)
        got = await _outlook(graph).get_inline_images("pm-1", max_count=10, max_bytes=10_000)
        assert sorted(i.content_id for i in got) == [f"c{n}" for n in range(5)]

    async def test_a_429_that_remains_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(outlook_mod, "_MAX_RETRY_AFTER_SECS", 0)
        graph = _Graph([_part("a-1", cid="c1")])
        graph.status["a-1"] = 429
        with pytest.raises(httpx.HTTPStatusError) as err:
            await _outlook(graph).get_inline_images("pm-1", max_count=10, max_bytes=10_000)
        assert err.value.response.status_code == 429
        assert graph.part_reads() == ["a-1", "a-1"], "one retry, then the raise"

    async def test_the_base_default_gives_no_part(self) -> None:
        class _Fake:
            get_inline_images = BaseEmailProvider.get_inline_images

        assert await _Fake().get_inline_images("p", max_count=5, max_bytes=5) == []


# ── 2. The Gmail read ───────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class TestTheGmailRead:

    async def test_each_part_with_a_content_id(self) -> None:
        tree = {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "multipart/related", "parts": [
                {"mimeType": "text/html", "body": {"size": 10, "data": _b64url(b"<p>x</p>")}},
                {"mimeType": "image/png", "headers": [{"name": "Content-ID", "value": "<ii_small>"}],
                 "body": {"size": len(PNG), "data": _b64url(PNG)}},
                {"mimeType": "image/gif", "headers": [{"name": "content-id", "value": "<Big@X>"}],
                 "body": {"size": len(GIF), "attachmentId": "att-big"}},
                {"mimeType": "image/png", "headers": [{"name": "Content-ID", "value": "<huge>"}],
                 "body": {"size": 9_000_000, "attachmentId": "att-huge"}},
            ]},
            # An attached mail: its inline part belongs to that mail.
            {"mimeType": "message/rfc822", "parts": [
                {"mimeType": "image/png", "headers": [{"name": "Content-ID", "value": "<inner>"}],
                 "body": {"size": len(PNG), "data": _b64url(PNG)}},
            ]},
            {"mimeType": "application/pdf", "filename": "a.pdf",
             "body": {"size": 100, "attachmentId": "att-pdf"}},
        ]}
        reads: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/messages/g-1"):
                assert request.url.params.get("format") == "full"
                return httpx.Response(200, json={"id": "g-1", "payload": tree})
            att = path.rsplit("/", 1)[-1]
            reads.append(att)
            return httpx.Response(200, json={"data": _b64url(GIF)})

        provider = GmailProvider({"access_token": "t", "refresh_token": "r"})
        provider._http = httpx.AsyncClient(
            base_url=GMAIL_API_BASE, transport=httpx.MockTransport(handle))
        got = await provider.get_inline_images("g-1", max_count=10, max_bytes=5_000_000)
        assert {i.content_id: i.content for i in got} == {"ii_small": PNG, "big@x": GIF}
        assert reads == ["att-big"], "the read fetched a part that is not inline"


# ── 3. The sniff ────────────────────────────────────────────────────────────


class TestTheSniff:

    @pytest.mark.parametrize(("data", "kind"), [
        (PNG, "image/png"), (GIF, "image/gif"), (JPEG, "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 10, "image/webp"),
        (b"\x00\x00\x00\x1cftypavif" + b"\x00" * 10, "image/avif"),
        (b"BM" + b"\x00" * 40, "image/bmp"),
    ])
    def test_a_raster_picture(self, data: bytes, kind: str) -> None:
        assert ii.sniff_image_type(data) == kind

    @pytest.mark.parametrize("data", [SVG, HTML, b"", b"BM", b"%PDF-1.7"])
    def test_never_a_picture(self, data: bytes) -> None:
        assert ii.sniff_image_type(data) is None

    def test_the_content_id_form(self) -> None:
        assert normalize_content_id("<Image001.PNG@01DD5A75.A7B8EA10>") == \
            "image001.png@01dd5a75.a7b8ea10"
        assert normalize_content_id(" a%40b ") == "a@b"
        assert normalize_content_id(None) == ""


# ── 4. The route, hermetic ──────────────────────────────────────────────────


class _BinRedis:
    """A redis-py stand-in for the binary pool. It records each call."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(self, name: str) -> bytes | None:
        self.calls.append(("get", name))
        return self.store.get(name)

    async def setex(self, name: str, seconds: int, value: bytes) -> bool:
        self.calls.append(("setex", name))
        assert isinstance(value, bytes)
        self.store[name] = value
        return True


class _Harness:
    """Patches the seams of the route for one test.

    The fake session honours the owner predicate as text: it returns the row
    only to an owner, and only when the SQL holds ``ea.user_id = :user_id``.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *,
                 parts: list[InlineImage] | None = None,
                 owners: frozenset[str] = frozenset({OWNER})) -> None:
        self.redis = _BinRedis()
        self.parts = parts if parts is not None else [
            InlineImage("image001.png@01dd5a75.a7b8ea10", PNG, "image/png"),
            InlineImage("ii_chart", GIF, "application/octet-stream"),
            InlineImage("evil", SVG, "image/png"),
            InlineImage("page", HTML, "image/png"),
        ]
        self.provider_calls = 0
        self.on_provider: BaseException | None = None
        self.delay = 0.0
        self.rotate = False
        self.sessions_opened = 0
        self.session_open = False
        self.persisted = 0
        self.row = SimpleNamespace(
            id=MSG_ID, provider_message_id="prov-msg", account_id="acct-1",
            provider="microsoft", credentials_encrypted="blob",
        )
        harness = self

        class _Db:
            async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
                sql = str(statement)
                owned = (params.get("user_id") in owners
                         or "ea.user_id = :user_id" not in sql)
                return SimpleNamespace(fetchone=lambda: harness.row if owned else None)

        @asynccontextmanager
        async def _tenant_session():
            harness.sessions_opened += 1
            harness.session_open = True
            try:
                yield _Db()
            finally:
                harness.session_open = False

        class _Provider:
            def __init__(self) -> None:
                self._dirty = False

            async def authenticate(self) -> bool:
                assert not harness.session_open, "a session is open across authenticate"
                self._dirty = harness.rotate
                return True

            async def get_inline_images(self, pmid: str, *, max_count: int,
                                        max_bytes: int) -> list[InlineImage]:
                assert not harness.session_open, "a session is open across the fetch"
                assert pmid == "prov-msg"
                assert (max_count, max_bytes) == (
                    ii.MAX_INLINE_IMAGES, ii.MAX_INLINE_IMAGE_BYTES)
                harness.provider_calls += 1
                if harness.delay:
                    await asyncio.sleep(harness.delay)
                if harness.on_provider is not None:
                    raise harness.on_provider
                return list(harness.parts)

            def credentials_dirty(self) -> bool:
                return self._dirty

        async def _persist(_db, _store, _account_id, _provider) -> None:
            harness.persisted += 1

        def _get_tenant_redis(*, binary: bool = False) -> TenantRedis:
            assert binary is True, "a picture goes through the binary pool"
            return TenantRedis(harness.redis)

        monkeypatch.setattr(ii, "_tenant_session", _tenant_session)
        monkeypatch.setattr(ii, "_decrypt_credentials", lambda _blob: ({}, object()))
        monkeypatch.setattr(ii, "_instantiate_provider", lambda _name, _creds: _Provider())
        monkeypatch.setattr(ii, "_persist_rotated_creds", _persist)
        monkeypatch.setattr(ii, "get_tenant_redis", _get_tenant_redis)


@pytest.fixture(autouse=True)
def _clean():
    token = tr._ORGANIZATION_ID.set(None)
    ii._FLIGHTS.clear()
    try:
        yield
    finally:
        tr._ORGANIZATION_ID.reset(token)
        ii._FLIGHTS.clear()


def _user(email: str = OWNER, org: str | None = ORG_A) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


async def _get(cid: str, mid: str = MSG_ID, *, email: str = OWNER,
               org: str | None = ORG_A) -> Any:
    return await ii.get_inline_image(mid, cid=cid, user=_user(email, org))


def _ids_key(org: str = ORG_A) -> str:
    return f"cc:{org}:email-inline-ids:{MSG_ID}"


class TestTheRoute:

    async def test_the_owner_gets_the_picture_with_its_true_type(self, monkeypatch) -> None:
        _Harness(monkeypatch)
        got = await _get("image001.png@01DD5A75.A7B8EA10")
        assert got.status_code == 200
        assert got.body == PNG
        assert got.media_type == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff"
        assert got.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert got.headers["cache-control"] == "private, max-age=3600"

    async def test_the_declared_type_never_decides(self, monkeypatch) -> None:
        _Harness(monkeypatch)
        got = await _get("ii_chart")
        assert (got.media_type, got.body) == ("image/gif", GIF)

    @pytest.mark.parametrize("cid", ["evil", "page"])
    async def test_svg_and_html_are_never_served(self, monkeypatch, cid: str) -> None:
        _Harness(monkeypatch)
        with pytest.raises(HTTPException) as err:
            await _get(cid)
        assert err.value.status_code == 404

    async def test_a_cid_url_escape_matches(self, monkeypatch) -> None:
        _Harness(monkeypatch)
        got = await _get("<image001.png%4001dd5a75.a7b8ea10>")
        assert got.body == PNG

    async def test_a_message_of_another_member_is_404_with_no_cache_and_no_provider(
        self, monkeypatch,
    ) -> None:
        h = _Harness(monkeypatch)
        await _get("ii_chart")
        h.redis.calls.clear()
        with pytest.raises(HTTPException) as err:
            await _get("ii_chart", email=SECOND)
        assert err.value.status_code == 404
        assert h.redis.calls == [], "the route read a cache key for a message it does not own"
        assert h.provider_calls == 1

    @pytest.mark.parametrize("mid", ["not-a-uuid", "{" + MSG_ID + "}"])
    async def test_a_bad_id_is_404_with_no_session(self, monkeypatch, mid: str) -> None:
        h = _Harness(monkeypatch)
        with pytest.raises(HTTPException) as err:
            await _get("ii_chart", mid)
        assert err.value.status_code == 404
        assert h.sessions_opened == 0

    async def test_a_hit_and_a_cached_miss_make_no_provider_call(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _get("ii_chart")
        assert h.provider_calls == 1
        stored = json.loads(h.redis.store[_ids_key()])
        assert len(stored) == 2, "only the two raster pictures are listed"
        again = await _get("II_CHART")
        assert again.body == GIF
        with pytest.raises(HTTPException) as err:
            await _get("not-in-this-mail")
        assert err.value.status_code == 404
        assert h.provider_calls == 1, "a cached answer reached the provider"
        assert all(k.startswith(f"cc:{ORG_A}:email-inline") for k in h.redis.store)

    async def test_org_b_never_reads_the_entry_of_org_a(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _get("ii_chart", org=ORG_A)
        await _get("ii_chart", org=ORG_B)
        assert h.provider_calls == 2
        assert _ids_key(ORG_A) in h.redis.store and _ids_key(ORG_B) in h.redis.store

    async def test_a_burst_of_one_message_makes_one_provider_read(self, monkeypatch) -> None:
        """A body names many pictures, and the frame asks for each at once."""
        h = _Harness(monkeypatch)
        h.delay = 0.05
        cids = ["image001.png@01dd5a75.a7b8ea10", "ii_chart"] * 10
        got = await asyncio.gather(*(_get(c) for c in cids))
        assert all(r.status_code == 200 for r in got)
        assert h.provider_calls == 1
        assert ii._FLIGHTS == {}, "a finished burst left its entry"

    async def test_a_burst_with_no_organization_still_reads_once(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.delay = 0.05
        got = await asyncio.gather(*(_get("ii_chart", org=None) for _ in range(8)))
        assert all(r.body == GIF for r in got)
        assert h.provider_calls == 1
        assert h.redis.calls == [], "no organization, so no cache"

    async def test_a_failed_read_is_asked_once_for_the_burst(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.delay = 0.05
        h.on_provider = RuntimeError("graph down")
        got = await asyncio.gather(*(_get("ii_chart") for _ in range(6)),
                                   return_exceptions=True)
        assert all(isinstance(e, HTTPException) and e.status_code == 502 for e in got)
        assert h.provider_calls == 1

    async def test_a_provider_429_is_503_with_retry_after(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        response = httpx.Response(429, headers={"Retry-After": "12"},
                                  request=httpx.Request("GET", "https://graph.test"))
        h.on_provider = httpx.HTTPStatusError("429", request=response.request,
                                              response=response)
        with pytest.raises(HTTPException) as err:
            await _get("ii_chart")
        assert err.value.status_code == 503
        assert err.value.headers == {"Retry-After": "12"}

    async def test_a_rotated_token_is_kept_in_a_second_session(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        h.rotate = True
        await _get("ii_chart")
        assert h.persisted == 1 and h.sessions_opened == 2

    async def test_no_rotation_opens_one_session(self, monkeypatch) -> None:
        h = _Harness(monkeypatch)
        await _get("ii_chart")
        assert h.sessions_opened == 1 and h.persisted == 0

    async def test_the_total_cap_holds(self, monkeypatch) -> None:
        big = _png_like(ii.MAX_INLINE_IMAGE_BYTES)
        parts = [InlineImage(f"p{n}", big, "image/png") for n in range(5)]
        h = _Harness(monkeypatch, parts=parts)
        await _get("p0")
        assert ii.MAX_INLINE_TOTAL_BYTES // ii.MAX_INLINE_IMAGE_BYTES == 4
        assert len(json.loads(h.redis.store[_ids_key()])) == 4
        with pytest.raises(HTTPException) as err:
            await _get("p4")
        assert err.value.status_code == 404

    def test_the_route_reads_the_owner_and_the_session_from_the_seam(self) -> None:
        """R11 and R5: no organization or member comes from the request, and
        the one session idiom of the email routes opens each session."""
        import inspect

        params = inspect.signature(ii.get_inline_image).parameters
        assert set(params) == {"message_id", "cid", "user"}
        assert "ea.user_id = :user_id" in inspect.getsource(ii.get_inline_image)
        src = inspect.getsource(ii)
        assert "get_db(" not in src and "create_async_engine" not in src
        assert "redis.asyncio" not in src and "Redis(" not in src


# ── 5. R8: the owner check on a real database ───────────────────────────────


@pytest.fixture()
def fake_inline(monkeypatch) -> dict[str, Any]:
    """The provider and the cache are fakes. The owner SQL is real."""
    state: dict[str, Any] = {"calls": 0}
    redis = _BinRedis()

    class _Provider:
        async def authenticate(self) -> bool:
            return True

        async def get_inline_images(self, _pmid: str, **_kw: Any) -> list[InlineImage]:
            state["calls"] += 1
            return [InlineImage("logo", PNG, "image/png")]

        def credentials_dirty(self) -> bool:
            return False

    monkeypatch.setattr(ii, "_decrypt_credentials", lambda _blob: ({}, object()))
    monkeypatch.setattr(ii, "_instantiate_provider", lambda _name, _creds: _Provider())
    monkeypatch.setattr(ii, "get_tenant_redis", lambda *, binary=False: TenantRedis(redis))
    state["redis"] = redis
    return state


@_DB_GATE
class TestTheOwnerCheckOnARealDatabase:

    async def test_only_the_owner_reads_the_picture(
        self, promoted, app_engine, fake_inline,  # noqa: F811
    ) -> None:
        """Two members of ONE organization: row level security does not hide
        the row, so only the owner predicate of the route does. And two
        organizations: a session of org A never reads a message of org B."""
        _assert_non_priv(app_engine)
        p = promoted
        member_a = f"a-{uuid.uuid4().hex[:8]}@inline.test"
        member_b = f"b-{uuid.uuid4().hex[:8]}@inline.test"
        acc_a, msg_a = _seed_cold_message(p.admin_engine, org=p.org_b, owner=member_a)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                with _bound(p.org_b):
                    got = await ii.get_inline_image(
                        msg_a, cid="LOGO", user=_user(member_a, p.org_b))
                    assert (got.status_code, got.body) == (200, PNG)
                    with pytest.raises(HTTPException) as err:
                        await ii.get_inline_image(
                            msg_a, cid="logo", user=_user(member_b, p.org_b))
                    assert err.value.status_code == 404, "another member of org B"
                with _bound(p.org_a):
                    with pytest.raises(HTTPException) as err:
                        await ii.get_inline_image(
                            msg_a, cid="logo", user=_user(member_a, p.org_a))
                    assert err.value.status_code == 404, (
                        "a session of org A read a message of org B")
            assert fake_inline["calls"] == 1, "only the owner read may reach the provider"
        finally:
            _purge(p.admin_engine, [acc_a])
