"""WS-17 SG-1 — the upload of a signature image, and its confined child.

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.4, §23.7, D-EM-67.

The rules pinned here:

* The sniff runs in the request, before any decoder: an SVG, a GIF, a WebP and
  a file that is not an image never start the child.
* The parent stops a child that passes its time bound, and the route answers
  with a plain sentence (a tiny bound and a sleeping child here).
* The route refuses a member past 20 images and an org past 100, and the same
  bytes give the same row with no new count.
* With no https ``GATEWAY_PUBLIC_URL`` the upload refuses and says why.
* The parent trusts no value of the child: a bad line, a wrong magic and a
  flood are each a refusal.
* The REAL child (``signature_image_worker.py``) keeps the aspect of a logo,
  crops a photo square, never scales up, writes JPEG only for an opaque
  photo, drops trailing bytes, steps down to fit 200 KB and then refuses, and
  reports a dark logo on a clear ground. On Windows the child runs with no
  confinement (``child_confine`` does nothing there). The confinement itself
  is fenced on Linux by ``test_attachment_ocr.py`` and
  ``test_web_reader_confine.py``.

Hermetic: the database is a fake that records each statement. The R8 half is
``test_email_signature_images_r8.py``.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pymupdf
import pytest
from fastapi import HTTPException, UploadFile
from gateway import signature_image as si
from gateway.routes.email import signature_images as routes

_ORG = "1111aaaa-2222-4333-8444-5555bbbb5555"
_ACCOUNT = "a0000000-0000-4000-8000-000000000001"
_ME = "jane@acme.example"
_BASE = "https://api.example.test"


# ── helpers ─────────────────────────────────────────────────────────────────


def _png(width: int, height: int, *, alpha: bool = True, fill: int = 0x336699) -> bytes:
    """One opaque colour. With ``alpha`` the PNG has an alpha channel."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), alpha)
    rgb = ((fill >> 16) & 255, (fill >> 8) & 255, fill & 255)
    pix.set_rect(pix.irect, (*rgb, 255) if alpha else rgb)
    return pix.tobytes("png")


def _noise_png(side: int) -> bytes:
    samples = os.urandom(side * side * 4)
    return pymupdf.Pixmap(pymupdf.csRGB, side, side, samples, 1).tobytes("png")


def _dark_on_clear_png() -> bytes:
    """A black bar on a transparent ground: the logo that vanishes in dark mode."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 100), True)
    pix.clear_with()  # no value: every byte 0, so every pixel is transparent
    pix.set_rect(pymupdf.IRect(0, 0, 400, 40), (10, 10, 10, 255))
    return pix.tobytes("png")


def _user(email: str = _ME, *, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        email=email, has_permission=lambda cap: admin and cap == routes.ADMIN_CAPABILITY)


def _upload(data: bytes, name: str = "logo.png") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


class _FakeDb:
    """Answers each statement of the store by its text, and records it."""

    def __init__(self, *, same=None, org_count: int = 0, mine: int = 0) -> None:
        self.same = same
        self.org_count = org_count
        self.mine = mine
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, sql, params=None):
        s = str(sql)
        self.executed.append((s, dict(params or {})))
        if "pg_advisory_xact_lock" in s:
            one = None
        elif "WHERE sha256 = :sha" in s:
            one = self.same
        elif "count(*)" in s:
            one = SimpleNamespace(org_count=self.org_count, mine=self.mine)
        elif s.startswith("INSERT INTO email_signature_images"):
            one = SimpleNamespace(id="b0000000-0000-4000-8000-000000000009")
        else:
            one = None
        return MagicMock(fetchone=MagicMock(return_value=one))

    def sql(self, clause: str) -> list[tuple[str, dict]]:
        return [(s, p) for s, p in self.executed if clause in s]


def _bind(monkeypatch, db: _FakeDb) -> list[int]:
    opened: list[int] = []

    @asynccontextmanager
    async def _session(org=None):
        opened.append(1)
        yield db

    monkeypatch.setattr(routes, "_tenant_session", _session)
    monkeypatch.setattr(routes, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(routes, "current_tenant", lambda: _ORG)
    return opened


def _encoded(data: bytes = b"\x89PNG\r\n\x1a\nfake", **over) -> si.Encoded:
    base = {"data": data, "content_type": "image/png", "width": 480, "height": 192,
            "dark_share": 0.1, "clear_share": 0.0}
    base.update(over)
    return si.Encoded(**base)


@pytest.fixture
def base_url(monkeypatch):
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", _BASE + "/")


# ── 1. The sniff runs before the child ─────────────────────────────────────


@pytest.fixture
def no_child(monkeypatch):
    calls: list[list[str]] = []

    async def _never(argv, data, timeout):
        calls.append(argv)
        raise AssertionError("the child started for a file that the sniff refuses")

    monkeypatch.setattr(si, "_run_child", _never)
    return calls


@pytest.mark.parametrize(("data", "words"), [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "SVG is not accepted"),
    (b'<?xml version="1.0"?><svg/>', "SVG is not accepted"),
    (b"GIF89a" + b"\x00" * 32, "not an image the product accepts"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16, "not an image the product accepts"),
    (b"%PDF-1.7\n", "not an image the product accepts"),
    (b"MZ\x90\x00", "not an image the product accepts"),
])
async def test_a_file_that_is_no_png_or_jpeg_never_starts_the_child(no_child, data, words):
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(data, "logo")
    assert words in str(caught.value)
    assert caught.value.status == 400
    assert no_child == []


async def test_the_svg_refusal_names_a_signature_image(no_child):
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(b"<svg></svg>", "logo")
    text = str(caught.value)
    assert "a signature image" in text and "profile picture" not in text
    assert "Use one of: JPEG, PNG." in text


async def test_an_upload_past_the_byte_cap_never_starts_the_child(no_child):
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * si.MAX_UPLOAD_BYTES
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(data, "logo")
    assert caught.value.status == 413
    assert no_child == []


async def test_the_route_refuses_an_svg_before_the_child(monkeypatch, base_url, no_child):
    db = _FakeDb()
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_signature_image(
            file=_upload(b"<svg onload='x()'></svg>"), account_id=_ACCOUNT,
            kind="logo", user=_user())
    assert caught.value.status_code == 400
    assert "SVG" in caught.value.detail
    assert no_child == []
    assert db.sql("INSERT") == []


def test_the_avatar_sniff_keeps_its_own_words() -> None:
    """The ``what=`` keyword is new. The profile picture sentence stays."""
    from gateway.avatar import AvatarError, sniff

    with pytest.raises(AvatarError) as caught:
        sniff(b"<svg></svg>")
    assert "for a profile picture" in str(caught.value)
    assert "shown on every page" in str(caught.value)
    assert sniff(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16) == "image/webp"


# ── 2. The time bound ───────────────────────────────────────────────────────


@pytest.fixture
def sleeping_child(monkeypatch):
    """A child that reads its stdin and then sleeps for 30 s."""
    started: list[asyncio.subprocess.Process] = []
    real_exec = asyncio.create_subprocess_exec

    async def _spy(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(si.asyncio, "create_subprocess_exec", _spy)
    monkeypatch.setattr(si, "_worker_argv", lambda spec: [
        sys.executable, "-c",
        "import sys, time; sys.stdin.buffer.read(); time.sleep(30)"])
    monkeypatch.setattr(si, "TIMEOUT_S", 0.5)
    return started


async def test_the_parent_stops_a_child_past_its_time_bound(sleeping_child):
    began = time.monotonic()
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(_png(10, 10), "logo")
    took = time.monotonic() - began
    assert caught.value.status == 503
    assert "took longer than" in str(caught.value) and "seconds" in str(caught.value)
    assert took < 10, f"the bound did not stop the child ({took:.1f} s)"
    [proc] = sleeping_child
    assert proc.returncode is not None, "the child still runs after the refusal"


async def test_the_route_answers_a_slow_child_with_a_plain_sentence(
    monkeypatch, base_url, sleeping_child,
):
    db = _FakeDb()
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_signature_image(
            file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    assert caught.value.status_code == 503
    assert caught.value.detail.startswith("Reading that image took longer than")
    assert db.sql("INSERT") == []
    assert all(p.returncode is not None for p in sleeping_child)


async def test_the_slot_is_free_after_a_timeout(sleeping_child):
    for _ in range(si.MAX_CHILDREN + 1):
        with pytest.raises(si.SignatureImageError):
            await si.encode(_png(10, 10), "logo")
    assert si._SLOT.acquire(blocking=False)
    si._SLOT.release()


# ── 3. The caps, and one copy of each image ────────────────────────────────


@pytest.fixture
def fake_encode(monkeypatch):
    seen: list[tuple[bytes, str]] = []

    async def _encode(data, kind):
        seen.append((data, kind))
        return _encoded()

    monkeypatch.setattr(routes.signature_image, "encode", _encode)
    return seen


async def test_a_member_past_20_images_is_refused(monkeypatch, base_url, fake_encode):
    db = _FakeDb(org_count=20, mine=routes.MEMBER_CAP)
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_signature_image(
            file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    assert caught.value.status_code == 409
    assert caught.value.detail.startswith("You have uploaded 20 signature images")
    assert db.sql("INSERT") == []
    # The count names the session's member, never a value of the request.
    [(_, params)] = db.sql("count(*)")
    assert params == {"me": _ME}


async def test_member_19_may_upload(monkeypatch, base_url, fake_encode):
    db = _FakeDb(org_count=19, mine=routes.MEMBER_CAP - 1)
    _bind(monkeypatch, db)
    out = await routes.upload_signature_image(
        file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    [(_, insert)] = db.sql("INSERT INTO email_signature_images")
    assert insert["me"] == _ME and insert["kind"] == "logo"
    assert insert["ct"] == "image/png" and len(insert["token"]) == 43
    assert out["url"] == f"{_BASE}/email/signature-image/{_ORG}/{insert['token']}.png"


async def test_an_org_past_100_images_is_refused(monkeypatch, base_url, fake_encode):
    db = _FakeDb(org_count=routes.ORG_CAP, mine=0)
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_signature_image(
            file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    assert caught.value.status_code == 409
    assert "Your organization has 100 signature images" in caught.value.detail


async def test_the_same_bytes_give_the_same_row_and_count_nothing(
    monkeypatch, base_url, fake_encode,
):
    same = SimpleNamespace(id="b0000000-0000-4000-8000-000000000001", kind="logo",
                           content_type="image/png", width=480, height=192,
                           public_token="t" * 43, retired_at="2026-11-01")
    db = _FakeDb(same=same, org_count=routes.ORG_CAP, mine=routes.MEMBER_CAP)
    _bind(monkeypatch, db)
    out = await routes.upload_signature_image(
        file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    assert out["id"] == same.id
    assert out["url"].endswith(f"/{_ORG}/{'t' * 43}.png")
    assert db.sql("count(*)") == [] and db.sql("INSERT") == []
    assert len(db.sql("SET retired_at = NULL")) == 1, "a retired copy comes back"


async def test_the_caps_run_under_the_lock_of_the_org(monkeypatch, base_url, fake_encode):
    db = _FakeDb()
    _bind(monkeypatch, db)
    await routes.upload_signature_image(
        file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    order = [s for s, _ in db.executed]
    assert "pg_advisory_xact_lock" in order[0]
    assert "current_setting('app.tenant_id', true)" in order[0]
    assert order.index(next(s for s in order if "count(*)" in s)) < order.index(
        next(s for s in order if s.startswith("INSERT")))


async def test_the_upload_checks_send_access_first(monkeypatch, base_url, fake_encode):
    db = _FakeDb()
    _bind(monkeypatch, db)
    owner = AsyncMock(side_effect=HTTPException(status_code=404, detail="Account not found"))
    monkeypatch.setattr(routes, "_assert_account_owner", owner)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_signature_image(
            file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    assert caught.value.status_code == 404
    assert owner.await_args.kwargs == {"level": "send"}
    assert owner.await_args.args[1:] == (_ACCOUNT, _ME)
    assert fake_encode == [] and db.sql("INSERT") == []


# ── 4. No public base URL ───────────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "", "http://localhost:8000", "https://"])
async def test_with_no_https_base_url_the_upload_refuses_and_says_why(
    monkeypatch, fake_encode, value,
):
    if value is None:
        monkeypatch.delenv("GATEWAY_PUBLIC_URL", raising=False)
    else:
        monkeypatch.setenv("GATEWAY_PUBLIC_URL", value)
    db = _FakeDb()
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.upload_signature_image(
            file=_upload(_png(10, 10)), account_id=_ACCOUNT, kind="logo", user=_user())
    assert caught.value.status_code == 503
    # The member's words name no setting (§23.3.9). The log names it.
    assert caught.value.detail == "Image upload is not set up on this server yet. Ask your admin."
    assert "GATEWAY_PUBLIC_URL" not in caught.value.detail
    assert fake_encode == [], "the child ran with nowhere to host the image"
    assert db.sql("INSERT") == []


def test_the_base_url_is_the_https_gateway_url(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", " https://api.metorite.com/ ")
    assert routes.public_base_url() == "https://api.metorite.com"


def test_the_settings_answer_says_whether_upload_is_on(monkeypatch) -> None:
    """The editor turns its upload button off from this key (§23.3.9)."""
    from gateway.routes.email.automation import assistant as assistant_mod

    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "https://api.metorite.com")
    assert assistant_mod._signature_upload_on() is True
    monkeypatch.setenv("GATEWAY_PUBLIC_URL", "http://localhost:8000")
    assert assistant_mod._signature_upload_on() is False
    monkeypatch.delenv("GATEWAY_PUBLIC_URL")
    assert assistant_mod._signature_upload_on() is False
    source = Path(assistant_mod.__file__).read_text(encoding="utf-8")
    assert source.count('"signature_image_upload": _signature_upload_on()') == 2, (
        "the GET and the PUT answer each carry the key")


# ── 5. The retire ───────────────────────────────────────────────────────────


async def test_the_retire_binds_who_may_call_it_into_the_statement(monkeypatch):
    db = _FakeDb()
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.retire_signature_image(
            "b0000000-0000-4000-8000-000000000001", user=_user())
    assert caught.value.status_code == 404
    [(sql, params)] = db.sql("UPDATE email_signature_images")
    assert "(created_by = :me OR :admin)" in sql
    assert "COALESCE(retired_at, now())" in sql and "DELETE" not in sql
    assert params["me"] == _ME and params["admin"] is False


async def test_an_admin_retire_passes_the_admin_bit(monkeypatch):
    db = _FakeDb()
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException):
        await routes.retire_signature_image(
            "b0000000-0000-4000-8000-000000000001", user=_user(admin=True))
    [(_, params)] = db.sql("UPDATE email_signature_images")
    assert params["admin"] is True


async def test_a_retire_of_a_bad_id_is_404_with_no_statement(monkeypatch):
    db = _FakeDb()
    _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.retire_signature_image("not-a-uuid", user=_user())
    assert caught.value.status_code == 404
    assert db.executed == []


# ── 6. The public route refuses a bad shape before any session ─────────────


@pytest.mark.parametrize(("org", "token", "ext"), [
    ("not-a-uuid", "a" * 43, "png"),
    (_ORG.upper(), "a" * 43, "png"),
    (_ORG, "a" * 42, "png"),
    (_ORG, "a" * 42 + "=", "png"),
    (_ORG, "a" * 43, "gif"),
    (_ORG, "a" * 43, "svg"),
])
async def test_a_bad_shape_is_the_same_404_and_opens_no_session(monkeypatch, org, token, ext):
    db = _FakeDb()
    opened = _bind(monkeypatch, db)
    with pytest.raises(HTTPException) as caught:
        await routes.signature_image_file(org, token, ext)
    assert caught.value.status_code == 404 and caught.value.detail == "Not Found"
    assert opened == []


def test_the_public_headers_are_the_spec_headers() -> None:
    assert dict(routes.PUBLIC_HEADERS) == {
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
        "Cross-Origin-Resource-Policy": "cross-origin",
    }


def test_the_public_route_never_calls_get_db() -> None:
    import inspect

    source = inspect.getsource(routes)
    assert "get_db" not in source.replace("never calls ``get_db``", "")
    assert "_tenant_session(tenant)" in inspect.getsource(routes.signature_image_file)


# ── 7. The parent trusts no value of the child ─────────────────────────────


@pytest.mark.parametrize("answer", [
    b"", b"not json", b'{"ok": 1}', b'{"ok": {}, "x": 1}',
    b'{"ok": {"content_type": "text/html", "data": "PGI+", "width": 1, "height": 1,'
    b' "dark_share": 0, "clear_share": 0, "confined": true}}',
    # The type says PNG, and the bytes are HTML.
    b'{"ok": {"content_type": "image/png", "data": "PGh0bWw+", "width": 1, "height": 1,'
    b' "dark_share": 0, "clear_share": 0, "confined": true}}',
    b'{"ok": {"content_type": "image/png", "data": "iVBORw0KGgo=", "width": 481,'
    b' "height": 1, "dark_share": 0, "clear_share": 0, "confined": true}}',
    b'{"ok": {"content_type": "image/png", "data": "iVBORw0KGgo=", "width": 1,'
    b' "height": 1, "dark_share": 2, "clear_share": 0, "confined": true}}',
    b'{"refused": 7}',
])
def test_a_bad_answer_is_a_plain_refusal(answer) -> None:
    with pytest.raises(si.SignatureImageError) as caught:
        si._parse(answer)
    assert str(caught.value) == "That image could not be read. Upload a PNG or a JPEG."


def test_a_refused_code_becomes_its_sentence() -> None:
    with pytest.raises(si.SignatureImageError) as caught:
        si._parse(b'{"refused": "too_big"}')
    assert "still larger than 200 KB" in str(caught.value)
    with pytest.raises(si.SignatureImageError) as caught:
        si._parse(b'{"refused": "unavailable"}')
    assert caught.value.status == 503


# ── 7a. The parent takes only a confined answer (fix round 1, P2) ──────────

_GOOD = {"content_type": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\nx").decode(),
         "width": 1, "height": 1, "dark_share": 0, "clear_share": 0}


def _answer(**marks) -> bytes:
    import json

    return json.dumps({"ok": {**_GOOD, **marks}}).encode()


def test_an_answer_with_no_confinement_mark_is_refused() -> None:
    with pytest.raises(si.SignatureImageError) as caught:
        si._parse(_answer())
    assert caught.value.status == 503
    assert "cannot isolate the image reader" in str(caught.value)
    assert si._parse(_answer(confined=True)).width == 1


def test_linux_never_takes_an_unconfined_answer(monkeypatch) -> None:
    monkeypatch.setattr(si.sys, "platform", "linux")
    with pytest.raises(si.SignatureImageError):
        si._parse(_answer(confined=False, unconfined_os="linux"))


def test_a_dev_box_takes_the_marker_of_its_own_system_only(monkeypatch) -> None:
    monkeypatch.setattr(si.sys, "platform", "win32")
    assert si._parse(_answer(confined=False, unconfined_os="win32")).width == 1
    with pytest.raises(si.SignatureImageError):
        si._parse(_answer(confined=False, unconfined_os="darwin"))


async def test_a_child_with_its_confine_step_deleted_is_refused(monkeypatch) -> None:
    """The real worker with ``_confine`` replaced by a no-op that sets no
    mark: the parent refuses its answer, on Linux and on a dev box alike."""
    from gateway import signature_image_worker as worker

    script = (
        "import importlib.util, sys\n"
        f"s = importlib.util.spec_from_file_location('w', {str(worker.__file__)!r})\n"
        "w = importlib.util.module_from_spec(s); s.loader.exec_module(w)\n"
        "w._confine = lambda cc, spec, state: None\n"
        "sys.exit(w.main(['w', sys.argv[1]]))\n"
    )
    real = si._worker_argv
    monkeypatch.setattr(si, "_worker_argv", lambda spec: [
        *real(spec)[:2], "-c", script, real(spec)[-1]])
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(_png(40, 20), "logo")
    assert caught.value.status == 503
    assert "cannot isolate the image reader" in str(caught.value)
    # The untouched worker is taken on this system.
    monkeypatch.setattr(si, "_worker_argv", real)
    assert (await si.encode(_png(40, 20), "logo")).width == 40


async def test_a_child_that_floods_its_answer_is_killed(monkeypatch):
    monkeypatch.setattr(si, "_worker_argv", lambda spec: [
        sys.executable, "-c",
        "import sys\nsys.stdin.buffer.read()\nwhile True: sys.stdout.write('x' * 65536)"])
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(_png(10, 10), "logo")
    assert str(caught.value) == "That image could not be read. Upload a PNG or a JPEG."


# ── 8. The real child ───────────────────────────────────────────────────────


async def test_a_logo_keeps_its_shape_and_goes_to_480() -> None:
    out = await si.encode(_png(1000, 400), "logo")
    assert (out.width, out.height) == (480, 192)
    assert out.content_type == "image/png"
    page = pymupdf.open(stream=out.data, filetype="image")[0]
    assert page.get_image_info()[0]["width"] == 480


async def test_a_small_logo_is_never_scaled_up() -> None:
    out = await si.encode(_png(100, 40), "logo")
    assert (out.width, out.height) == (100, 40)


async def test_a_photo_is_a_square_jpeg_when_it_is_opaque() -> None:
    out = await si.encode(_png(640, 480, alpha=False), "photo")
    assert (out.width, out.height) == (480, 480)
    assert out.content_type == "image/jpeg"
    assert out.data.startswith(b"\xff\xd8\xff")


async def test_a_photo_with_a_clear_pixel_stays_png() -> None:
    out = await si.encode(_dark_on_clear_png(), "photo")
    assert out.content_type == "image/png"
    assert out.width == out.height == 100


async def test_trailing_bytes_do_not_survive() -> None:
    data = _png(60, 30) + b"TRAILING-SECRET-PAYLOAD"
    out = await si.encode(data, "logo")
    assert b"TRAILING-SECRET-PAYLOAD" not in out.data


async def test_a_dark_logo_on_a_clear_ground_is_reported() -> None:
    out = await si.encode(_dark_on_clear_png(), "logo")
    assert out.clear_share > 0.5
    assert out.dark_share > 0.9
    light = await si.encode(_png(200, 100, fill=0xFFFFFF), "logo")
    assert light.dark_share == 0.0 and light.clear_share == 0.0


def test_the_child_steps_the_edge_down_to_fit() -> None:
    """480 px first, then 360 px when 480 passes the byte cap (in process:
    ``_encode`` is pure, so the step is exact)."""
    from gateway import signature_image_worker as worker

    data = _noise_png(480)
    spec = {**si._spec("logo"), "max_output_bytes": 10**9}
    first = worker._encode(pymupdf, data, spec)
    assert first["width"] == 480
    size_480 = len(base64.b64decode(first["data"]))
    second = worker._encode(pymupdf, data, {**spec, "max_output_bytes": size_480 - 1})
    assert second["width"] == 360
    assert len(base64.b64decode(second["data"])) < size_480


async def test_an_image_that_never_fits_steps_down_and_refuses(monkeypatch) -> None:
    """The real child, with the cap lowered so no step can fit."""
    monkeypatch.setattr(si, "MAX_OUTPUT_BYTES", 2000)
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(_noise_png(300), "logo")
    assert "still larger than 200 KB at 240 pixels wide" in str(caught.value)
    assert caught.value.status == 400


async def test_a_truncated_png_is_a_plain_refusal() -> None:
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(_png(50, 50)[:40], "logo")
    assert str(caught.value).startswith("That image could not be read.")
    assert caught.value.status == 400


# ── 9. The save of a signature that holds a legacy data: image ─────────────
# The editor keeps a legacy `data:` image (allowBase64 true), so an edit of the
# text sends it back. The save must host it, or refuse in plain words. It must
# never drop it with no word.

_LEGACY_SRC = "data:image/png;base64," + base64.b64encode(_png(64, 32)).decode("ascii")
_LEGACY = f'<p>Jane Doe</p><img src="{_LEGACY_SRC}" width="64">'
_EDITED = f'<p>Jane Doe, CEO</p><img src="{_LEGACY_SRC}" width="64">'


class _StoredDb:
    """Answers the stored-signature read with ``stored``."""

    def __init__(self, stored: str) -> None:
        self.stored = stored

    async def execute(self, sql, params=None):
        return MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(signature=self.stored)))


@pytest.fixture
def stored_legacy(monkeypatch):
    @asynccontextmanager
    async def _session(org=None):
        yield _StoredDb(_LEGACY)

    monkeypatch.setattr(routes, "_tenant_session", _session)
    monkeypatch.setattr(routes, "_assert_account_owner", AsyncMock())
    hosted: list[str] = []

    async def _store(encoded, *, kind, member, base):
        hosted.append(member)
        return {"url": f"{base}/email/signature-image/{_ORG}/{'h' * 43}.png"}

    monkeypatch.setattr(routes, "store_image", _store)
    return hosted


async def test_an_edit_hosts_the_legacy_image_and_keeps_the_edit(
    monkeypatch, base_url, stored_legacy,
):
    out, removed = await routes.signature_for_save(_ACCOUNT, _ME, _EDITED)
    assert out == (f'<p>Jane Doe, CEO</p><img src="{_BASE}/email/signature-image/'
                   f'{_ORG}/{"h" * 43}.png" width="64">')
    assert removed == []
    assert stored_legacy == [_ME], "the real child ran once and the image was stored"


async def test_the_save_names_each_address_that_the_clean_removed(
    monkeypatch, base_url, stored_legacy,
):
    """Fix round 1, P2: D-EM-70 stays strict, and the member hears what went."""
    edited = ('<p>Jane</p><a href="http://acme.example/about">About</a>'
              '<img src="http://cdn.example/logo.png"><a href="https://ok.example">ok</a>')
    out, removed = await routes.signature_for_save(_ACCOUNT, _ME, edited)
    assert out == '<p>Jane</p><a>About</a><a href="https://ok.example">ok</a>'
    assert removed == [
        {"kind": "link", "value": "http://acme.example/about"},
        {"kind": "image", "value": "http://cdn.example/logo.png"},
    ]


def test_the_put_answer_carries_the_removed_list() -> None:
    from gateway.routes.email.automation import assistant as assistant_mod

    source = Path(assistant_mod.__file__).read_text(encoding="utf-8")
    assert '"signature_removed": signature_removed,' in source
    assert "signature, signature_removed = await signature_for_save(" in source


async def test_with_no_https_base_an_edit_refuses_and_drops_nothing(
    monkeypatch, stored_legacy,
):
    monkeypatch.delenv("GATEWAY_PUBLIC_URL", raising=False)
    with pytest.raises(HTTPException) as caught:
        await routes.signature_for_save(_ACCOUNT, _ME, _EDITED)
    assert caught.value.status_code == 503
    assert caught.value.detail.startswith(
        "Your signature holds an embedded image, and this server cannot host it.")
    assert stored_legacy == []


async def test_a_save_of_the_stored_legacy_signature_changes_nothing(
    monkeypatch, stored_legacy,
):
    monkeypatch.delenv("GATEWAY_PUBLIC_URL", raising=False)
    assert await routes.signature_for_save(_ACCOUNT, _ME, _LEGACY) == (_LEGACY, [])
    assert stored_legacy == []
