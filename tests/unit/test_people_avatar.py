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
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway import avatar
from gateway.routes.people import core as people_core
from gateway.routes.people import fields as people_fields
from gateway.routes.people import profile as people_profile
from gateway.routes.people import selfservice as people_self
from gateway.routes.tasks import people as tasks_people
from tests.unit._sql_match import hits


def run(coro):
    return asyncio.run(coro)


# ── Real images, built with the library that will decode them ───────────────

def image(width: int, height: int, fmt: str = "png",
          bands: tuple[tuple[int, int, int], ...] = ((200, 30, 30),)) -> bytes:
    """A real encoded image. Bands are painted left to right so a crop is
    provable by reading a pixel back, rather than by trusting a dimension."""
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    step = width // len(bands)
    for index, colour in enumerate(bands):
        right = width if index == len(bands) - 1 else (index + 1) * step
        pix.set_rect(fitz.IRect(index * step, 0, right, height), colour)
    return pix.tobytes(fmt)


def decoded(data: bytes) -> Any:
    import fitz

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

@pytest.mark.parametrize("size", [(1000, 400), (400, 1000), (64, 64),
                                  (1200, 1200), (1, 1)])
def test_every_upload_leaves_as_the_same_square(size) -> None:
    """The rule that makes "no random image sizes" true by construction rather
    than by a validation somebody has to maintain."""
    out = decoded(avatar.normalise(image(*size)))
    assert (out.width, out.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)


def test_a_tiny_image_is_scaled_UP_not_left_small() -> None:
    """Otherwise a 16x16 favicon renders as a smudge beside 256px neighbours."""
    out = decoded(avatar.normalise(image(16, 16)))
    assert out.width == avatar.AVATAR_PX


def test_the_output_is_a_jpeg_whatever_went_in() -> None:
    for fmt in ("png", "jpg"):
        assert avatar.normalise(image(300, 300, fmt)).startswith(b"\xff\xd8\xff")


def test_the_stored_form_is_a_data_uri() -> None:
    uri = avatar.to_data_uri(avatar.normalise(image(300, 300)))
    assert uri.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]).startswith(b"\xff\xd8\xff")


def test_the_stored_image_is_small_whatever_arrived() -> None:
    """Weight is bounded by the OUTPUT, not by the input — which is why the
    upload cap is a guard against the decoder, not what keeps the table small."""
    assert len(avatar.normalise(image(1600, 1200))) < 60_000


def test_an_alpha_image_composites_onto_white_not_black() -> None:
    """JPEG has no alpha, and the default for "no alpha" is exactly the kind of
    thing that is only wrong in the corner of somebody's profile picture."""
    import fitz

    transparent = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), True)
    transparent.clear_with()
    out = decoded(avatar.normalise(transparent.tobytes("png")))
    assert out.pixel(4, 4) == (255, 255, 255)


# ══════════════════════════════════════════════════════════════════════════
# 2. The crop is enforced, not requested
# ══════════════════════════════════════════════════════════════════════════

def test_no_crop_takes_the_centre() -> None:
    wide = image(900, 300, bands=(RED, GREEN, BLUE))
    assert band_at(avatar.normalise(wide)) == GREEN


def test_a_fractional_crop_selects_the_region_asked_for() -> None:
    """Fractions, not pixels: a 1000x400 pixel image opens as a 750x300 POINT
    page, so a pixel rectangle would silently crop the wrong region. Measured —
    the first probe produced a 256x192 image from what should have been square."""
    wide = image(900, 300, bands=(RED, GREEN, BLUE))
    assert band_at(avatar.normalise(wide, crop=(0.0, 0.0, 1.0))) == RED
    assert band_at(avatar.normalise(wide, crop=(0.7, 0.0, 1.0))) == BLUE


def test_a_zoomed_crop_is_still_square() -> None:
    out = decoded(avatar.normalise(image(900, 300), crop=(0.4, 0.2, 0.5)))
    assert (out.width, out.height) == (avatar.AVATAR_PX, avatar.AVATAR_PX)


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


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "left", None])
def test_a_crop_value_that_is_not_a_number_is_survived(bad) -> None:
    """NaN passes every comparison in the clamp — all of them answer False — so
    it would reach the clip rectangle intact and fail somewhere less
    explainable."""
    out = decoded(avatar.normalise(image(400, 400), crop=(bad, bad, bad)))
    assert out.width == avatar.AVATAR_PX


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
    import fitz

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


def test_a_member_with_no_grants_may_set_their_own_picture(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.upload_my_avatar(
        _Upload(image(600, 400)), 0.0, 0.0, 1.0, user=SUBJECT))
    stored = db.params_for("UPDATE people SET avatar")
    assert stored["avatar"].startswith("data:image/jpeg;base64,")
    assert stored["by"] == SUBJECT.email


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
