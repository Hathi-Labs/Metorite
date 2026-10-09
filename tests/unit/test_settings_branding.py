"""Tests for the organisation logo (Settings → Organization).

An admin of any tenant can put bytes here, and those bytes are then rendered in
the shell of every one of their colleagues. So the cases pinned below are the
ones where a plausible implementation is wrong in a way nobody notices:

* the declared content type disagrees with the bytes (an SVG announcing itself
  as a PNG is the whole reason `probe_image` sniffs magic bytes),
* the data URI is echoed from the request rather than rebuilt,
* a shape that satisfies every size rule and still destroys the header,
* and a stored blob written by a different build of the app.

The header parsers are exercised against hand-built files rather than fixtures
so that the offsets under test are visible next to the assertion.
"""
from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException
from gateway.image_probe import ImageInfo, UnsupportedImage, probe_image
from gateway.routes import settings as st

# ── Builders: minimal but structurally real files ───────────────────────────

def png(width: int, height: int) -> bytes:
    """Signature + IHDR. CRC is not checked by the probe, so it is left zero."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def jpeg(width: int, height: int, *, metadata_segments: int = 1) -> bytes:
    """SOI, some metadata, then SOF0 — the dimensions are never at a fixed offset."""
    out = b"\xff\xd8"
    for _ in range(metadata_segments):
        body = b"JFIF\x00" + b"\x00" * 9
        out += b"\xff\xe0" + (len(body) + 2).to_bytes(2, "big") + body
    sof = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03"
    out += b"\xff\xc0" + (len(sof) + 2).to_bytes(2, "big") + sof
    return out + b"\xff\xd9"


def webp_vp8x(width: int, height: int) -> bytes:
    body = (
        b"VP8X"
        + (10).to_bytes(4, "little")
        + b"\x10\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + (5).to_bytes(4, "little") + b"\x2f" + bits.to_bytes(4, "little")
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


# ── The probe ───────────────────────────────────────────────────────────────

def test_png_dimensions() -> None:
    assert probe_image(png(600, 160)) == ImageInfo("image/png", 600, 160)


def test_jpeg_dimensions_behind_metadata() -> None:
    """The SOF walk has to skip EXIF/ICC/comment segments to find the frame."""
    assert probe_image(jpeg(800, 200, metadata_segments=4)) == ImageInfo(
        "image/jpeg", 800, 200
    )


def test_webp_extended_and_lossless() -> None:
    assert probe_image(webp_vp8x(640, 128)) == ImageInfo("image/webp", 640, 128)
    assert probe_image(webp_lossless(640, 128)) == ImageInfo("image/webp", 640, 128)


def test_svg_is_refused_by_name_not_by_omission() -> None:
    """The message has to say 'export a PNG', or the admin just tries again."""
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
    with pytest.raises(UnsupportedImage) as exc:
        probe_image(svg + b"\x00" * 64)
    assert "SVG" in str(exc.value)
    assert "PNG" in str(exc.value)


def test_a_pdf_is_named_rather_than_called_unsupported() -> None:
    with pytest.raises(UnsupportedImage) as exc:
        probe_image(b"%PDF-1.7\n" + b"\x00" * 64)
    assert "PDF" in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"nope",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,  # signature, no IHDR
        b"\xff\xd8" + b"\x00" * 64,  # SOI then garbage: never reaches a marker
        b"RIFF\x00\x00\x00\x00WEBPXXXX" + b"\x00" * 32,  # unknown WebP chunk
    ],
)
def test_malformed_input_raises_rather_than_returning_nonsense(data: bytes) -> None:
    """Every rejection path must terminate — a hostile file cannot loop the walk."""
    with pytest.raises(UnsupportedImage):
        probe_image(data)


def test_a_truncated_jpeg_terminates() -> None:
    """A segment length running past the buffer must end the walk, not index it."""
    truncated = jpeg(100, 100)[:8]
    with pytest.raises(UnsupportedImage):
        probe_image(truncated)


# ── The route ───────────────────────────────────────────────────────────────

class _User:
    email = "admin@fracktal.in"


@pytest.fixture()
def saved(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture what would be written, without a database."""
    box: dict = {}

    def _save(key: str, value: object, updated_by: str = "") -> None:
        box["key"] = key
        box["value"] = value
        box["updated_by"] = updated_by

    import acb_common

    monkeypatch.setattr(acb_common, "save_org_setting", _save, raising=False)
    return box


def _upload(data: bytes) -> st.BrandingUpload:
    return st.BrandingUpload(logoBase64=base64.b64encode(data).decode())


def test_a_valid_logo_is_stored_with_derived_dimensions(saved: dict) -> None:
    out = st.put_branding(_upload(png(600, 160)), user=_User())  # type: ignore[arg-type]
    assert out.logo is not None
    assert (out.logo.width, out.logo.height) == (600, 160)
    assert out.logo.mime == "image/png"
    assert saved["key"] == "branding"
    assert saved["value"]["updatedBy"] == "admin@fracktal.in"


def test_the_data_uri_is_rebuilt_from_the_sniffed_type(saved: dict) -> None:
    """The stored URI must not carry a MIME type the caller chose.

    This is the whole attack: send PNG magic bytes inside a body that declares
    `image/svg+xml`, and if the declared type is echoed into the stored data
    URI, every colleague's shell renders an SVG from a `data:` URL.
    """
    body = st.BrandingUpload(
        logoBase64="data:image/svg+xml;base64,"
        + base64.b64encode(png(64, 64)).decode()
    )
    out = st.put_branding(body, user=_User())  # type: ignore[arg-type]
    assert out.logo is not None
    assert out.logo.dataUri.startswith("data:image/png;base64,")
    assert "svg" not in out.logo.dataUri


def test_an_svg_wearing_a_png_content_type_is_still_refused(saved: dict) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(HTTPException) as exc:
        st.put_branding(_upload(svg + b" " * 64), user=_User())  # type: ignore[arg-type]
    assert exc.value.status_code == 400
    assert "SVG" in str(exc.value.detail)
    assert "key" not in saved  # nothing was written


def test_oversized_upload_is_rejected_before_decoding(saved: dict) -> None:
    """Rejected on the encoded length, so we never allocate the decoded form."""
    huge = "A" * (st._LOGO_MAX_BYTES * 4)
    with pytest.raises(HTTPException) as exc:
        st.put_branding(st.BrandingUpload(logoBase64=huge), user=_User())  # type: ignore[arg-type]
    assert exc.value.status_code == 413
    assert "key" not in saved


def test_non_base64_is_a_400_not_a_500(saved: dict) -> None:
    with pytest.raises(HTTPException) as exc:
        st.put_branding(st.BrandingUpload(logoBase64="!!! not base64 !!!"), user=_User())  # type: ignore[arg-type]
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("w", "h", "why"),
    [
        (16, 16, "smaller than the minimum edge"),
        (4096, 512, "larger than the maximum edge"),
        (2000, 100, "20:1 — a sliver that would smear across the header"),
        (100, 400, "1:4 — a tower that would render 7px wide at header height"),
    ],
)
def test_shapes_the_header_cannot_render_are_refused(
    saved: dict, w: int, h: int, why: str
) -> None:
    """Size alone is not enough: a legal file size can still wreck the shell."""
    with pytest.raises(HTTPException) as exc:
        st.put_branding(_upload(png(w, h)), user=_User())  # type: ignore[arg-type]
    assert exc.value.status_code == 400, why
    assert "key" not in saved


def test_the_widest_and_tallest_accepted_shapes_are_allowed(saved: dict) -> None:
    """Pin the boundary, so tightening it later is a deliberate act."""
    assert st.put_branding(_upload(png(800, 100)), user=_User()).logo is not None  # type: ignore[arg-type]
    assert st.put_branding(_upload(png(100, 200)), user=_User()).logo is not None  # type: ignore[arg-type]


def test_delete_clears_the_logo_but_keeps_the_author(saved: dict) -> None:
    """'Who removed our logo' is the first question asked when it vanishes."""
    out = st.delete_branding(user=_User())  # type: ignore[arg-type]
    assert out.logo is None
    assert saved["value"]["logo"] is None
    assert saved["value"]["updatedBy"] == "admin@fracktal.in"


# ── Reading a stored blob ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, {}, [], "nonsense", 42, {"logo": "nope"}])
def test_unreadable_rows_degrade_to_no_logo(raw: object) -> None:
    """An unparseable row reads as 'no logo', never as an exception on page load."""
    assert _coerced(raw).logo is None


def test_a_partial_logo_blob_does_not_half_render() -> None:
    """A blob missing `mime` cannot produce an <img> with no type — drop it whole."""
    assert _coerced({"logo": {"dataUri": "data:image/png;base64,AA"}}).logo is None


def test_a_complete_blob_round_trips() -> None:
    out = _coerced(
        {
            "logo": {
                "dataUri": "data:image/png;base64,AA",
                "mime": "image/png",
                "width": 600,
                "height": 160,
                "byteSize": 2,
            },
            "updatedBy": "someone@fracktal.in",
            "updatedAt": "2026-08-14T00:00:00+00:00",
        }
    )
    assert out.logo is not None
    assert out.logo.width == 600
    assert out.updatedBy == "someone@fracktal.in"


def _coerced(raw: object) -> st.BrandingResponse:
    return st._coerce_branding(raw)


# ── The gate ────────────────────────────────────────────────────────────────
#
# Every test above calls `st.put_branding(...)` DIRECTLY, which bypasses
# FastAPI's dependency machinery entirely — so deleting
# `dependencies=[require_permission("admin:settings:manage")]` from the route
# leaves all thirty of them green while any member of the company gains the
# power to replace the whole organisation's logo. That is exactly the shape R7
# exists to forbid: an acceptance criterion with no test that fails when it is
# broken.
#
# The idiom below is lifted from `test_settings_appearance.py`, which had
# already solved this for the neighbouring route and was not reused the first
# time round.


def _required_permissions(route) -> set[str]:
    """Permission strings a route's dependencies enforce.

    `require_permission` closes over its arguments rather than exposing them,
    so the only way to assert the gate from outside is to read the closure.
    """
    found: set[str] = set()
    for dep in getattr(route, "dependencies", []):
        fn = getattr(dep, "dependency", None)
        for cell in getattr(fn, "__closure__", None) or ():
            value = cell.cell_contents
            if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
                found.update(value)
    return found


def _route(path: str, method: str):
    for r in st.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return r
    raise AssertionError(f"no route for {method} {path}")


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_writes_are_gated_on_admin_settings_manage(method: str) -> None:
    """Changing the logo changes what every member of the company sees."""
    assert "admin:settings:manage" in _required_permissions(
        _route("/settings/branding", method)
    )


def test_reading_the_logo_is_open_to_every_member() -> None:
    """Gating the READ would mean only admins saw their own company's branding.

    Asserted as an empty set rather than "not admin-gated", so that adding any
    permission to the read path is a deliberate act that fails here first.
    """
    assert _required_permissions(_route("/settings/branding", "GET")) == set()


# ── Dark mode (owner request, 2026-10-09) ───────────────────────────────────
# One upload covers both colour modes. The uploader's browser decides the
# style; the server checks that the stored pair can never disagree.


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_a_white_dark_logo_is_stored_and_checked_like_the_logo(saved: dict) -> None:
    body = st.BrandingUpload(logoBase64=_b64(png(600, 160)), logoDarkBase64=_b64(png(600, 160)), darkStyle="white")
    out = st.put_branding(body, user=_User())  # type: ignore[arg-type]
    assert out.darkStyle == "white" and out.logoDark is not None
    assert saved["value"]["darkStyle"] == "white"
    assert saved["value"]["logoDark"]["mime"] == "image/png"


def test_the_white_style_needs_its_image(saved: dict) -> None:
    with pytest.raises(HTTPException) as e:
        st.put_branding(st.BrandingUpload(logoBase64=_b64(png(600, 160)), darkStyle="white"), user=_User())  # type: ignore[arg-type]
    assert e.value.status_code == 400
    assert "value" not in saved


def test_a_dark_image_with_any_other_style_is_refused(saved: dict) -> None:
    for style in ("same", "plate"):
        with pytest.raises(HTTPException):
            st.put_branding(  # type: ignore[arg-type]
                st.BrandingUpload(logoBase64=_b64(png(600, 160)), logoDarkBase64=_b64(png(600, 160)), darkStyle=style),
                user=_User(),
            )
    assert "value" not in saved


def test_the_dark_image_meets_every_check_the_logo_does(saved: dict) -> None:
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>" + b" " * 64
    with pytest.raises(HTTPException):
        st.put_branding(  # type: ignore[arg-type]
            st.BrandingUpload(logoBase64=_b64(png(600, 160)), logoDarkBase64=_b64(svg), darkStyle="white"),
            user=_User(),
        )
    assert "value" not in saved


def test_the_plate_style_is_stored_with_no_second_image(saved: dict) -> None:
    out = st.put_branding(st.BrandingUpload(logoBase64=_b64(png(600, 160)), darkStyle="plate"), user=_User())  # type: ignore[arg-type]
    assert (out.darkStyle, out.logoDark) == ("plate", None)


def test_an_old_row_reads_as_the_same_logo_in_both_modes() -> None:
    logo = {"dataUri": "data:image/png;base64,AA==", "mime": "image/png", "width": 2, "height": 1, "byteSize": 1}
    out = st._coerce_branding({"logo": logo})
    assert (out.darkStyle, out.logoDark) == ("same", None)


def test_a_white_style_whose_image_is_broken_degrades_to_the_logo() -> None:
    logo = {"dataUri": "data:image/png;base64,AA==", "mime": "image/png", "width": 2, "height": 1, "byteSize": 1}
    out = st._coerce_branding({"logo": logo, "logoDark": {"broken": True}, "darkStyle": "white"})
    assert (out.darkStyle, out.logoDark) == ("same", None)


def test_an_unknown_style_reads_as_same() -> None:
    logo = {"dataUri": "data:image/png;base64,AA==", "mime": "image/png", "width": 2, "height": 1, "byteSize": 1}
    assert st._coerce_branding({"logo": logo, "darkStyle": "neon"}).darkStyle == "same"
