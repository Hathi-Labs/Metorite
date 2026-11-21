"""WS-28q — the display image.

Spec: `project-docs/specs/people_center_app.md` §3.1a · **D-PC-17**.

The claim this file locks is a single one, stated four ways: **what is stored is
what the server produced, never what the browser sent.** Everything else —
fixed dimensions, bounded weight, an unbypassable crop, no polyglot files —
falls out of that rather than being a rule somebody has to keep enforcing.

Two behaviours here were **measured, not assumed**, and both would have shipped
as defects otherwise:

* the decoder is not a type check — MuPDF renders SVG, and an SVG handed to
  `fitz.open(filetype="image")` opens happily. So bytes are sniffed before the
  decoder sees them;
* the crop rectangle cannot be in pixels — a 1000x400 pixel image opens as a
  750x300 *point* page, and the first probe of a pixel rectangle produced a
  256x192 image from what should have been a square.

These run the real encoder against real images: PyMuPDF is a gateway dependency
already, no Postgres is involved, and a fake image codec would tell us nothing
about the one that ships. `tests/live/live_ws28q.py` is the database half.

Since task 2186 the encoder runs in the confined child of
`gateway/signature_image_worker.py`, and section 5 is its fence: the request
process decodes nothing, and a child past its time bound is stopped with a
plain sentence. A real decode needs a child that can confine itself, so on a
Linux kernel with no Landlock those tests skip (the child fails closed there).
"""

from __future__ import annotations

import ast
import asyncio
import base64
import struct
import sys
import time
import zlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway import avatar
from gateway import signature_image as si
from gateway import signature_image_worker as worker
from gateway.routes.people import core as people_core
from gateway.routes.people import fields as people_fields
from gateway.routes.people import profile as people_profile
from gateway.routes.people import selfservice as people_self
from gateway.routes.tasks import people as tasks_people
from tests.unit._sql_match import hits


def run(coro):
    return asyncio.run(coro)


def _confinable() -> bool:
    """True where the real child can confine itself: any dev box, or Linux
    x86-64 with Landlock at the ABI that the parent asks."""
    if sys.platform != "linux":
        return True
    import platform

    cc = worker._load_confine(si._confine_path())
    return platform.machine() == "x86_64" and cc.landlock_abi() >= si.MIN_LANDLOCK_ABI


#: A real decode needs a confinable child, because the parent refuses any
#: other answer on Linux. The box and the CI runners have Landlock.
needs_child = pytest.mark.skipif(
    not _confinable(),
    reason="this Linux kernel cannot confine the child, so it fails closed here",
)
linux_only = pytest.mark.skipif(
    sys.platform != "linux",
    reason="RLIMIT_AS is set on Linux only; the box and CI run Linux",
)


# ── Real images, built with the library that will decode them ───────────────

def image(width: int, height: int, fmt: str = "png",
          bands: tuple[tuple[int, int, int], ...] = ((200, 30, 30),)) -> bytes:
    """A real encoded image. Bands are painted left to right so a crop is
    provable by reading a pixel back, rather than by trusting a dimension."""
    import pymupdf as fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    step = width // len(bands)
    for index, colour in enumerate(bands):
        right = width if index == len(bands) - 1 else (index + 1) * step
        pix.set_rect(fitz.IRect(index * step, 0, right, height), colour)
    return pix.tobytes(fmt)


def decoded(data: bytes) -> Any:
    import pymupdf as fitz

    return fitz.Pixmap(data)


def band_at(data: bytes, x: int = 128, y: int = 128) -> tuple[int, int, int]:
    """Which painted band the pixel at (x, y) belongs to.

    **Nearest match, not equality**: the output is a JPEG and JPEG is lossy, so
    a band painted (30, 200, 30) reads back as (30, 200, 31). Asserting the
    exact triple would be asserting that the encoder is lossless, which is a
    different — and false — claim than "the right region was cropped".
    """
    pixel = decoded(data).pixel(x, y)
    return min((RED, GREEN, BLUE),
               key=lambda c: sum((a - b) ** 2 for a, b in zip(c, pixel, strict=True)))


RED = (200, 30, 30)
GREEN = (30, 200, 30)
BLUE = (30, 30, 200)


# ══════════════════════════════════════════════════════════════════════════
# 1. The stored image is the server's, whatever arrived
# ══════════════════════════════════════════════════════════════════════════

@needs_child
@pytest.mark.parametrize("size", [(1000, 400), (400, 1000), (64, 64),
                                  (1200, 1200), (1, 1)])
def test_every_upload_leaves_as_the_same_square(size) -> None:
    """The rule that makes "no random image sizes" true by construction rather
    than by a validation somebody has to maintain."""
    out = decoded(avatar.normalise(image(*size)))
    assert (out.width, out.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)


@needs_child
def test_a_tiny_image_is_scaled_UP_not_left_small() -> None:
    """Otherwise a 16x16 favicon renders as a smudge beside 256px neighbours."""
    out = decoded(avatar.normalise(image(16, 16)))
    assert out.width == avatar.AVATAR_PX


@needs_child
def test_the_output_is_a_jpeg_whatever_went_in() -> None:
    for fmt in ("png", "jpg"):
        assert avatar.normalise(image(300, 300, fmt)).startswith(b"\xff\xd8\xff")


@needs_child
def test_the_stored_form_is_a_data_uri() -> None:
    uri = avatar.to_data_uri(avatar.normalise(image(300, 300)))
    assert uri.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]).startswith(b"\xff\xd8\xff")


@needs_child
def test_the_stored_image_is_small_whatever_arrived() -> None:
    """Weight is bounded by the OUTPUT, not by the input — which is why the
    upload cap is a guard against the decoder, not what keeps the table small."""
    assert len(avatar.normalise(image(1600, 1200))) < 60_000


@needs_child
def test_an_alpha_image_composites_onto_white_not_black() -> None:
    """JPEG has no alpha, and the default for "no alpha" is exactly the kind of
    thing that is only wrong in the corner of somebody's profile picture."""
    import pymupdf as fitz

    transparent = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), True)
    transparent.clear_with()
    out = decoded(avatar.normalise(transparent.tobytes("png")))
    assert out.pixel(4, 4) == (255, 255, 255)


# ══════════════════════════════════════════════════════════════════════════
# 2. The crop is enforced, not requested
# ══════════════════════════════════════════════════════════════════════════

@needs_child
def test_no_crop_takes_the_centre() -> None:
    wide = image(900, 300, bands=(RED, GREEN, BLUE))
    assert band_at(avatar.normalise(wide)) == GREEN


@needs_child
def test_a_fractional_crop_selects_the_region_asked_for() -> None:
    """Fractions, not pixels: a 1000x400 pixel image opens as a 750x300 POINT
    page, so a pixel rectangle would silently crop the wrong region. Measured —
    the first probe produced a 256x192 image from what should have been square."""
    wide = image(900, 300, bands=(RED, GREEN, BLUE))
    assert band_at(avatar.normalise(wide, crop=(0.0, 0.0, 1.0))) == RED
    assert band_at(avatar.normalise(wide, crop=(0.7, 0.0, 1.0))) == BLUE


@needs_child
def test_a_zoomed_crop_is_still_square() -> None:
    out = decoded(avatar.normalise(image(900, 300), crop=(0.4, 0.2, 0.5)))
    assert (out.width, out.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)


@needs_child
@pytest.mark.parametrize("crop", [
    (9.0, 9.0, 1.0),        # off the edge entirely
    (-5.0, -5.0, 1.0),
    (0.0, 0.0, 99.0),       # a side larger than the image
    (0.0, 0.0, 0.0),        # no selection at all
    (0.0, 0.0, -1.0),
])
def test_a_nonsense_crop_is_clamped_rather_than_refused(crop) -> None:
    """A picture that lands slightly wrong is a person's to fix in a second
    attempt; refusing the upload teaches them the feature is broken."""
    out = decoded(avatar.normalise(image(900, 300), crop=crop))
    assert (out.width, out.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)


@needs_child
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "left", None])
def test_a_crop_value_that_is_not_a_number_is_survived(bad) -> None:
    """NaN passes every comparison in the clamp — all of them answer False — so
    it would reach the clip rectangle intact and fail somewhere less
    explainable."""
    out = decoded(avatar.normalise(image(400, 400), crop=(bad, bad, bad)))
    assert out.width == avatar.AVATAR_PX


@needs_child
def test_a_client_that_sends_no_crop_at_all_still_gets_a_square() -> None:
    """The cropper is a courtesy; the square is a guarantee (D-PC-17). A caller
    hitting the API directly cannot produce a non-square avatar."""
    out = decoded(avatar.normalise(image(1200, 200)))
    assert out.width == out.height


# ══════════════════════════════════════════════════════════════════════════
# 3. What is refused, and why the decoder is not the check
# ══════════════════════════════════════════════════════════════════════════

def test_svg_is_refused_by_NAME_because_the_decoder_would_accept_it() -> None:
    """**Measured.** MuPDF renders SVG: handed to `fitz.open(filetype="image")`
    it opens happily. An avatar is displayed on every page in the product, and
    an SVG is a document that can carry script and external references."""
    import pymupdf as fitz

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    fitz.open(stream=svg, filetype="image")      # the decoder does NOT refuse it
    with pytest.raises(avatar.AvatarError) as exc:
        avatar.normalise(svg)
    assert "SVG" in str(exc.value)


def test_the_sniffer_does_not_trust_the_content_type() -> None:
    """The multipart content-type is chosen by the client, so it is not
    consulted at all — the bytes are."""
    with pytest.raises(avatar.AvatarError):
        avatar.sniff(b"GIF89a" + b"\x00" * 32)


@pytest.mark.parametrize("blob", [
    b"", b"not an image", b"\x00" * 64,
    b"RIFF\x00\x00\x00\x00WAVE",         # RIFF is a container: WAV opens too
    b"%PDF-1.4\n",
])
def test_anything_that_is_not_an_accepted_image_is_refused(blob) -> None:
    with pytest.raises(avatar.AvatarError):
        avatar.normalise(blob)


def test_a_real_webp_container_is_accepted() -> None:
    assert avatar.sniff(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16) == "image/webp"


def test_an_upload_over_the_cap_is_refused_before_the_decoder() -> None:
    """The cap exists to stop somebody streaming a film into the decoder, not
    to keep the table small — the re-encode does that."""
    with pytest.raises(avatar.AvatarError) as exc:
        avatar.normalise(b"\xff\xd8\xff" + b"\x00" * avatar.MAX_UPLOAD_BYTES)
    assert "limit" in str(exc.value).lower()


@needs_child
def test_a_truncated_image_is_a_sentence_not_a_traceback() -> None:
    truncated = image(400, 400)[:80]
    with pytest.raises(avatar.AvatarError) as exc:
        avatar.normalise(truncated)
    assert "could not be read" in str(exc.value)


def test_every_refusal_says_what_to_do_instead() -> None:
    """The person choosing the file is the person who has to choose another."""
    for blob in (b"not an image", b'<svg xmlns="http://www.w3.org/2000/svg"/>'):
        with pytest.raises(avatar.AvatarError) as exc:
            avatar.normalise(blob)
        assert any(fmt in str(exc.value) for fmt in avatar.ACCEPTED)


# ══════════════════════════════════════════════════════════════════════════
# 4. Who may set it
# ══════════════════════════════════════════════════════════════════════════

class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


#: Every column `_row_to_person` reads by name rather than by `getattr`. A
#: double missing one of them fails in the mapper, which says nothing about the
#: avatar — so the fixture carries the whole pre-WS-28g shape.
PERSON = SimpleNamespace(
    id="11111111-1111-1111-1111-111111111111", name="Priya",
    email="priya@fracktal.in", role="Engineer", title="Firmware lead",
    department="Engineering", team="Firmware", reports_to=None,
    manager_id=None, status="active", skills=[], skills_source={},
    domain=None, resume_summary=None, years_experience=None,
    capacity_hours_per_week=None, current_load_hours_per_week=None,
    available_hours_per_week=None, clickup_user_id=None, email_conflict=None,
    avatar=None, avatar_updated_at=None, working_hours=None,
)


class FakeDB:
    def __init__(self, row: Any | None = PERSON):
        self.row = row
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if statement.startswith("SELECT 1 FROM app_user"):
            return _Result([SimpleNamespace()])
        if "FROM org_settings" in statement:
            return _Result([])
        if hits(statement, "FROM people"):
            wanted = (params or {}).get("email")
            if wanted is not None and self.row is not None:
                mine = (getattr(self.row, "email", None) or "").lower()
                return _Result([self.row] if mine == wanted else [])
            return _Result([self.row] if self.row is not None else [])
        return _Result([])

    async def commit(self) -> None:
        return None

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)

    def params_for(self, fragment: str) -> dict:
        for statement, params in zip(self.statements, self.params, strict=True):
            if fragment in statement:
                return params
        raise AssertionError(f"no statement contained {fragment!r}")


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    for module in (people_core, people_self, people_profile, tasks_people):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)


def _user(email: str | None, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


SUBJECT = _user("priya@fracktal.in")                       # NO grants at all
ADMIN = _user("admin@fracktal.in", "feature:people", "admin:members:manage")
STRANGER = _user("someone@fracktal.in", "feature:people")


class _Upload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data


def test_the_avatar_is_self_writable_and_upload_only() -> None:
    """It is in the self class — the same question the PATCH asks about a
    timezone — but the payload cannot carry it, because it arrives as a file."""
    assert "avatar" in people_fields.editable_fields(is_admin=False, is_self=True)
    assert "avatar" in people_fields.UPLOAD_ONLY_FIELDS
    assert "avatar" not in tasks_people.PersonWrite.model_fields


def test_the_payload_is_the_writable_set_less_the_upload_only_ones() -> None:
    assert set(tasks_people.PersonWrite.model_fields) == (
        set(people_fields.WRITABLE_FIELDS) - set(people_fields.UPLOAD_ONLY_FIELDS))


@needs_child
def test_a_member_with_no_grants_may_set_their_own_picture(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.upload_my_avatar(
        _Upload(image(600, 400)), 0.0, 0.0, 1.0, user=SUBJECT))
    stored = db.params_for("UPDATE people SET avatar")
    assert stored["avatar"].startswith("data:image/jpeg;base64,")
    assert stored["by"] == SUBJECT.email


@needs_child
def test_the_upload_stamps_when_it_changed(monkeypatch) -> None:
    """`updated_at` stops being able to answer "when did they last change their
    picture" the moment anything else on the row moves."""
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.upload_my_avatar(
        _Upload(image(300, 300)), 0.0, 0.0, 1.0, user=SUBJECT))
    assert db.issued("avatar_updated_at = now()")


def test_a_stranger_may_not_set_somebody_elses(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_profile.upload_avatar(
            PERSON.id, _Upload(image(300, 300)), 0.0, 0.0, 1.0, user=STRANGER))
    assert exc.value.status_code == 403
    assert not db.issued("UPDATE people SET avatar")


@needs_child
def test_an_admin_may_set_anyones(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_profile.upload_avatar(
        PERSON.id, _Upload(image(300, 300)), 0.0, 0.0, 1.0, user=ADMIN))
    assert db.issued("UPDATE people SET avatar")


def test_a_refused_file_is_a_400_that_repeats_the_reason(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_self.upload_my_avatar(
            _Upload(b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
            0.0, 0.0, 1.0, user=SUBJECT))
    assert exc.value.status_code == 400
    assert "SVG" in exc.value.detail
    assert not db.issued("UPDATE people SET avatar")


def test_removing_it_clears_the_column_and_stamps_the_change(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.delete_my_avatar(user=SUBJECT))
    assert db.issued("avatar = NULL")
    # Stamped, not cleared: "they took their picture down just now" is still a
    # change the client has to notice.
    assert db.issued("avatar_updated_at = now()")


def test_a_member_with_no_directory_row_gets_404_not_a_silent_no_op(
        monkeypatch) -> None:
    db = FakeDB(row=None)
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_self.upload_my_avatar(
            _Upload(image(300, 300)), 0.0, 0.0, 1.0, user=SUBJECT))
    assert exc.value.status_code == 404


def test_the_self_router_still_addresses_only_me() -> None:
    """The avatar routes must not have introduced a way to name a PERSON on the
    ungated router — that is D-PC-15's structural guarantee, and the avatar
    endpoints take a file, not an id."""
    import re

    from gateway.routes.people import self_router

    for route in self_router.routes:
        for param in re.findall(r"\{([a-z_]+)\}", route.path):
            assert "person" not in param, route.path


def test_both_doors_share_one_normaliser() -> None:
    """A second normaliser would be a second answer to what shape an avatar is,
    and only one of them would get the SVG refusal."""
    import inspect

    for module in (people_self, people_profile):
        body = inspect.getsource(module)
        assert "store_avatar(" in body
        assert "def store_avatar" not in body



# ══════════════════════════════════════════════════════════════════════════
# 5. The decode runs in a confined child, never in the request (task 2186)
# ══════════════════════════════════════════════════════════════════════════

AVATAR_PY = Path(avatar.__file__)


def test_the_avatar_module_imports_no_decoder() -> None:
    """The request process holds the byte cap and the sniff only. A decoder
    import here would put native code on the member's bytes in the gateway."""
    tree = ast.parse(AVATAR_PY.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"pymupdf", "fitz", "PIL", "cv2"}, names


def _no_decode_in_parent(monkeypatch) -> list[bytes]:
    """Make the decoder of THIS process refuse. The child is another process,
    so a decode that still works was done there."""
    import pymupdf

    seen: list[bytes] = []

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("stream") or b"")
        raise AssertionError("the request process decoded the upload")

    monkeypatch.setattr(pymupdf, "open", _refuse)
    return seen


@needs_child
@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_a_valid_image_decodes_in_the_child_not_the_request(monkeypatch, fmt) -> None:
    seen = _no_decode_in_parent(monkeypatch)
    out = avatar.normalise(image(600, 400, fmt), crop=(0.1, 0.0, 0.8))
    assert out.startswith(b"\xff\xd8\xff")
    pix = decoded(out)
    assert (pix.width, pix.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)
    assert seen == []


@needs_child
def test_the_route_stores_a_picture_with_no_decoder_in_the_request(monkeypatch) -> None:
    seen = _no_decode_in_parent(monkeypatch)
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.upload_my_avatar(
        _Upload(image(300, 300, "jpg")), 0.0, 0.0, 1.0, user=SUBJECT))
    assert db.params_for("UPDATE people SET avatar")["avatar"].startswith(
        "data:image/jpeg;base64,")
    assert seen == []


@pytest.fixture
def sleeping_child(monkeypatch):
    """A child that reads its stdin and then sleeps for 30 s, and a bound of
    half a second. The hook is the one of the signature image tests."""
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


def test_the_parent_stops_a_child_past_its_time_bound(sleeping_child) -> None:
    """THE fence of task 2186: one slow image cannot hold a gateway worker."""
    began = time.monotonic()
    with pytest.raises(avatar.AvatarError) as caught:
        avatar.normalise(image(10, 10))
    took = time.monotonic() - began
    assert str(caught.value) == (
        "Reading that image took longer than 0.5 seconds, so it was stopped. "
        "Upload a smaller picture.")
    assert took < 10, f"the bound did not stop the child ({took:.1f} s)"
    [proc] = sleeping_child
    assert proc.returncode is not None, "the child still runs after the refusal"


def test_the_route_answers_a_slow_child_with_a_plain_400(
        monkeypatch, sleeping_child) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_self.upload_my_avatar(
            _Upload(image(300, 300)), 0.0, 0.0, 1.0, user=SUBJECT))
    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("Reading that image took longer than")
    assert not db.issued("UPDATE people SET avatar")
    assert sleeping_child and all(p.returncode is not None for p in sleeping_child)


def test_the_slot_is_free_after_a_timeout(sleeping_child) -> None:
    for _ in range(si.MAX_CHILDREN + 1):
        with pytest.raises(avatar.AvatarError):
            avatar.normalise(image(10, 10))
    assert si._SLOT.acquire(blocking=False)
    si._SLOT.release()


@pytest.mark.parametrize("script", [
    # A crash: a traceback on stderr and no answer.
    "import sys; sys.stdin.buffer.read(); raise SystemExit('boom')",
    # A child that ends with nothing to say.
    "import sys; sys.stdin.buffer.read()",
    # Garbage, and a JSON line that is no answer.
    "import sys; sys.stdin.buffer.read(); print('\\x00garbage')",
    "import sys; sys.stdin.buffer.read(); print('{\"ok\": 1}')",
    # A flood past the answer cap: the parent kills it.
    "import sys\nsys.stdin.buffer.read()\nwhile True: sys.stdout.write('x' * 65536)",
])
def test_a_child_that_crashes_or_answers_garbage_is_a_plain_refusal(
        monkeypatch, script) -> None:
    monkeypatch.setattr(si, "_worker_argv", lambda spec: [sys.executable, "-c", script])
    with pytest.raises(avatar.AvatarError) as caught:
        avatar.normalise(image(10, 10))
    assert str(caught.value) == avatar._UNREADABLE


def _ok(**fields: Any) -> tuple[str, Any]:
    good = {"content_type": "image/jpeg", "width": 256, "height": 256,
            "data": base64.b64encode(b"\xff\xd8\xff\xe0jpeg").decode()}
    return "ok", {**good, **fields}


@pytest.mark.parametrize("item", [
    _ok(content_type="image/png"),
    _ok(width=255),
    _ok(height=True),
    _ok(width=256.0),
    _ok(data="not base64!"),
    _ok(data=base64.b64encode(b"<html>").decode()),
    _ok(data=base64.b64encode(b"\xff\xd8\xff" + b"\x00" * avatar.MAX_OUTPUT_BYTES).decode()),
    _ok(data=None),
    ("refused", "too_big"),
    ("refused", "no such code"),
])
def test_the_parent_trusts_no_field_of_the_child(item) -> None:
    with pytest.raises(avatar.AvatarError) as caught:
        avatar._stored_image(item, 256)
    assert str(caught.value) == avatar._UNREADABLE


def test_an_unconfined_answer_is_refused_with_its_own_sentence() -> None:
    """An ``ok`` with no confinement mark is no picture (``read_answer``)."""
    import json

    answer = json.dumps({"ok": _ok()[1]}).encode()
    with pytest.raises(avatar.AvatarError) as caught:
        avatar._stored_image(si.read_answer(answer), 256)
    assert "cannot isolate the image reader" in str(caught.value)
    assert "profile pictures" in str(caught.value)
    assert avatar._stored_image(_ok(), 256).startswith(b"\xff\xd8\xff")


def _bomb_png(width: int, height: int, *, rgb: bool = False) -> bytes:
    """A real PNG of zeros, built one row at a time: 1-bit grey, or 8-bit RGB.

    The file is tiny, and the image is huge. The test process never holds
    the raw image, and never decodes it: that is the point of the child."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    depth, colour, row_bytes = (8, 2, width * 3) if rgb else (1, 0, (width + 7) // 8)
    packer = zlib.compressobj(9)
    row = b"\x00" * (1 + row_bytes)
    body = b"".join(packer.compress(row) for _ in range(height)) + packer.flush()
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0))
            + chunk(b"IDAT", body) + chunk(b"IEND", b""))


@needs_child
@pytest.mark.parametrize(("width", "height"), [
    (20_000, 20_000),    # past the edge cap: 400 megapixels
    (7_000, 7_000),      # under the edge cap, past the pixel cap: 49 megapixels
])
def test_a_decompression_bomb_is_refused_in_the_child(monkeypatch, width, height) -> None:
    bomb = _bomb_png(width, height)
    assert len(bomb) < avatar.MAX_UPLOAD_BYTES       # the byte cap lets it in
    seen = _no_decode_in_parent(monkeypatch)
    began = time.monotonic()
    with pytest.raises(avatar.AvatarError) as caught:
        avatar.normalise(bomb)
    assert "too large" in str(caught.value)
    assert time.monotonic() - began < si.TIMEOUT_S
    assert seen == []


@needs_child
def test_the_largest_source_under_the_caps_still_decodes(monkeypatch) -> None:
    """The bounds fit the caps: a 39.6-megapixel RGB source decodes inside the
    memory bound and the time bound. So the caps, not the memory, refuse a
    bomb above."""
    bomb = _bomb_png(6_000, 6_600, rgb=True)
    assert len(bomb) < avatar.MAX_UPLOAD_BYTES
    seen = _no_decode_in_parent(monkeypatch)
    out = decoded(avatar.normalise(bomb))
    assert (out.width, out.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)
    assert seen == []


@linux_only
@needs_child
def test_linux_a_child_past_its_memory_bound_is_a_plain_refusal(monkeypatch) -> None:
    """The memory bound is the child's own ``RLIMIT_AS``. With a bound that
    MuPDF cannot load in, the child dies or refuses, and the parent answers
    with a sentence, never a traceback. The gateway process lives on."""
    monkeypatch.setattr(si, "MEMORY_BYTES", 64 * 1024 * 1024)
    with pytest.raises(avatar.AvatarError) as caught:
        avatar.normalise(image(300, 300))
    assert str(caught.value) in (avatar._UNREADABLE, avatar._CHILD_REFUSALS["pixels"])
