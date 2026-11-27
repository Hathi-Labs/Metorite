"""WS-STU D24a — the re-encode of a Studio photo capture.

Spec: ``project-docs/specs/ai_studio.md`` §14, row D24a.

The rules pinned here:

* A capture keeps its aspect ratio and has no square crop.
* The EXIF orientation is applied BEFORE the re-encode, so the stored image
  is upright, for all eight orientations.
* The stored bytes hold no EXIF or GPS block. The child re-encodes from pixels
  and copies no metadata.
* The long side is at most ``MAX_CAPTURE_SIDE`` (2048 px), and never scaled up.
* An SVG, a WebP and a GIF are refused by the sniff, so no child starts.
* A capture over the byte cap is refused ``413``, and an output that never fits
  is refused ``too_big``.
* ``encode`` keeps the logo and photo kinds, and refuses ``capture``, so the
  signature route cannot store a capture.

The real child runs where it can confine itself (the box and CI). The pure
``worker._encode`` runs everywhere. No database and no route here: D24a adds
neither.
"""
from __future__ import annotations

import io
import os
import sys

import pymupdf
import pytest
from gateway import signature_image as si
from gateway import signature_image_worker as worker
from PIL import Image, ImageOps


def _confinable() -> bool:
    if sys.platform != "linux":
        return True
    import platform

    cc = worker._load_confine(si._confine_path())
    return platform.machine() == "x86_64" and cc.landlock_abi() >= si.MIN_LANDLOCK_ABI


needs_child = pytest.mark.skipif(
    not _confinable(),
    reason="this Linux kernel cannot confine the child, so it fails closed here",
)

#: The colour of each corner block, so a flip or a turn shows.
_CORNERS = {"tl": (230, 20, 20), "tr": (20, 200, 20), "bl": (20, 20, 230), "br": (230, 230, 20)}


def _quad(width: int, height: int) -> Image.Image:
    """Four solid blocks, one colour for each corner."""
    img = Image.new("RGB", (width, height))
    hw, hh = width // 2, height // 2
    for name, (x, y) in {"tl": (0, 0), "tr": (hw, 0), "bl": (0, hh), "br": (hw, hh)}.items():
        img.paste(_CORNERS[name], (x, y, x + hw, y + hh))
    return img


def _jpeg(width: int, height: int, *, orientation: int = 1, gps: bool = True) -> bytes:
    """A JPEG with an orientation tag and, by default, a GPS IFD."""
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "PhoneMaker"
    if gps:
        ifd = exif.get_ifd(0x8825)
        ifd[1], ifd[2], ifd[3], ifd[4] = "N", (12.0, 58.0, 30.0), "E", (77.0, 35.0, 10.0)
        exif[0x8825] = ifd
    buf = io.BytesIO()
    _quad(width, height).save(buf, "JPEG", quality=95, exif=exif)
    return buf.getvalue()


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    _quad(width, height).save(buf, "PNG")
    return buf.getvalue()


def _spec(**over) -> dict:
    return {**si._spec("capture"), **over}


def _encode_pure(data: bytes, **over) -> tuple[Image.Image, bytes]:
    """The pure encode of the child, in process. Returns the image and bytes."""
    import base64

    ok = worker._encode(pymupdf, data, _spec(**over))
    raw = base64.b64decode(ok["data"])
    return Image.open(io.BytesIO(raw)).convert("RGB"), raw


def _corner_colours(img: Image.Image) -> dict[str, tuple[int, int, int]]:
    w, h = img.size
    pts = {"tl": (w // 8, h // 8), "tr": (w - w // 8, h // 8),
           "bl": (w // 8, h - h // 8), "br": (w - w // 8, h - h // 8)}
    return {k: img.getpixel(v) for k, v in pts.items()}


def _near(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return all(abs(x - y) < 40 for x, y in zip(a, b, strict=True))


# ── the kind and its caps ───────────────────────────────────────────────────


def test_the_capture_kind_is_not_a_signature_kind() -> None:
    assert si.CAPTURE not in si.KINDS
    assert si.MAX_CAPTURE_SIDE == 2048
    assert si.MAX_CAPTURE_UPLOAD_BYTES == 15 * 1024 * 1024
    spec = si._spec("capture")
    assert spec["max_bytes"] == si.MAX_CAPTURE_UPLOAD_BYTES
    assert spec["capture_side"] == 2048
    assert si._spec("logo")["max_bytes"] == si.MAX_UPLOAD_BYTES


async def test_encode_still_refuses_capture() -> None:
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode(_jpeg(40, 20), "capture")
    assert caught.value.status == 422


def test_a_capture_may_pass_the_two_mb_cap_of_a_signature_image() -> None:
    big = _jpeg(40, 20) + b"\0" * (3 * 1024 * 1024)
    assert si.check_type(big, "capture") == "image/jpeg"
    with pytest.raises(si.SignatureImageError) as caught:
        si.check_type(big, "logo")
    assert caught.value.status == 413


def test_a_capture_over_fifteen_mb_is_refused_before_any_decode() -> None:
    big = b"\xff\xd8\xff" + b"\0" * si.MAX_CAPTURE_UPLOAD_BYTES
    with pytest.raises(si.SignatureImageError) as caught:
        si.check_type(big, "capture")
    assert caught.value.status == 413
    assert "15 MB" in str(caught.value)


# ── the sniff: nothing reaches a decoder ────────────────────────────────────


def _never_started(monkeypatch) -> None:
    async def _never(*_a, **_k):
        raise AssertionError("the child must not start")

    monkeypatch.setattr(si, "_run_child", _never)


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9"><script>1</script></svg>'
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\0" * 20


@pytest.mark.parametrize("body", [_SVG, b"<?xml version='1.0'?>" + _SVG, _WEBP, b"GIF89a" + b"\0" * 30,
                                  b"not an image at all"])
async def test_svg_webp_gif_and_junk_are_refused_before_the_child(monkeypatch, body) -> None:
    _never_started(monkeypatch)
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode_capture(body)
    assert caught.value.status == 400


async def test_the_svg_refusal_names_the_fix(monkeypatch) -> None:
    _never_started(monkeypatch)
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode_capture(_SVG)
    assert "SVG is not accepted" in str(caught.value)


# ── the pure encode ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("orientation", range(1, 9))
def test_each_exif_orientation_comes_out_upright(orientation: int) -> None:
    data = _jpeg(400, 300, orientation=orientation)
    ref = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    out, _raw = _encode_pure(data)
    assert out.size == ref.size
    want, got = _corner_colours(ref), _corner_colours(out)
    assert all(_near(got[k], want[k]) for k in want), (orientation, got, want)


def test_orientation_six_swaps_the_dimensions() -> None:
    out, _raw = _encode_pure(_jpeg(400, 300, orientation=6))
    assert out.size == (300, 400)


def test_the_output_holds_no_exif_and_no_gps() -> None:
    data = _jpeg(400, 300, orientation=6)
    assert b"Exif" in data and len(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825)) > 0
    out, raw = _encode_pure(data)
    assert b"Exif" not in raw
    reopened = Image.open(io.BytesIO(raw))
    assert len(reopened.getexif()) == 0
    assert len(reopened.getexif().get_ifd(0x8825)) == 0
    assert b"PhoneMaker" not in raw
    assert out.size == (300, 400)


def test_the_aspect_ratio_holds() -> None:
    out, _raw = _encode_pure(_jpeg(1000, 500))
    assert out.size == (1000, 500)
    out, _raw = _encode_pure(_png(300, 900))
    assert out.size == (300, 900)


def test_a_large_image_is_scaled_to_the_long_side() -> None:
    out, _raw = _encode_pure(_jpeg(4000, 3000))
    assert out.size == (2048, 1536)
    out, _raw = _encode_pure(_jpeg(3000, 4000, orientation=1))
    assert out.size == (1536, 2048)


def test_a_rotated_large_image_is_scaled_after_the_turn() -> None:
    out, _raw = _encode_pure(_jpeg(4000, 3000, orientation=6))
    assert out.size == (1536, 2048)


def test_a_small_image_is_never_scaled_up() -> None:
    out, _raw = _encode_pure(_jpeg(120, 80))
    assert out.size == (120, 80)


def test_a_jpeg_stays_jpeg_and_a_png_with_no_clear_pixel_is_jpeg_too() -> None:
    _out, raw = _encode_pure(_jpeg(200, 100))
    assert raw.startswith(b"\xff\xd8\xff")
    _out, raw = _encode_pure(_png(200, 100))
    assert raw.startswith(b"\xff\xd8\xff")


def test_a_png_with_a_clear_pixel_stays_png() -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 40), True)
    pix.clear_with()
    _out, raw = _encode_pure(pix.tobytes("png"))
    assert raw.startswith(b"\x89PNG")


def test_an_output_that_never_fits_is_too_big() -> None:
    noise = pymupdf.Pixmap(pymupdf.csRGB, 300, 300, os.urandom(300 * 300 * 4), 1).tobytes("png")
    with pytest.raises(worker._Refused) as caught:
        worker._encode(pymupdf, noise, _spec(max_output_bytes=500))
    assert caught.value.reason == "too_big"


def test_the_output_steps_the_long_side_down_to_fit() -> None:
    noise = pymupdf.Pixmap(pymupdf.csRGB, 400, 400, os.urandom(400 * 400 * 4), 1).tobytes("png")
    spec = _spec(capture_side=400, max_output_bytes=10**9)
    first = worker._encode(pymupdf, noise, spec)
    import base64

    size = len(base64.b64decode(first["data"]))
    second = worker._encode(pymupdf, noise, {**spec, "max_output_bytes": size - 1})
    assert first["width"] == 400 and second["width"] == 300


# ── the real child ──────────────────────────────────────────────────────────


@needs_child
async def test_the_real_child_gives_an_upright_jpeg_with_no_location() -> None:
    out = await si.encode_capture(_jpeg(400, 300, orientation=6))
    assert (out.width, out.height) == (300, 400)
    assert out.content_type == "image/jpeg"
    assert b"Exif" not in out.data
    assert len(Image.open(io.BytesIO(out.data)).getexif()) == 0


@needs_child
async def test_the_real_child_scales_a_large_photo() -> None:
    out = await si.encode_capture(_jpeg(4000, 3000))
    assert (out.width, out.height) == (2048, 1536)


@needs_child
async def test_the_real_child_refuses_an_output_over_the_cap(monkeypatch) -> None:
    monkeypatch.setattr(si, "MAX_CAPTURE_OUTPUT_BYTES", 500)
    noise = pymupdf.Pixmap(pymupdf.csRGB, 300, 300, os.urandom(300 * 300 * 4), 1).tobytes("png")
    with pytest.raises(si.SignatureImageError) as caught:
        await si.encode_capture(noise)
    assert caught.value.status == 400
    assert "still larger than" in str(caught.value)


# ── Merge with #2714 (task 3343 delta verify): the two fences that were missing ──


def _unconfined_ok() -> bytes:
    import base64
    import json

    good = {"content_type": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\nx").decode(),
            "width": 1, "height": 1, "dark_share": 0, "clear_share": 0,
            "confined": False, "unconfined_os": "linux"}
    return json.dumps({"ok": good}).encode()


@pytest.mark.parametrize("kind", ["logo", "photo", "avatar", si.CAPTURE])
def test_no_kind_takes_an_unconfined_answer_on_linux(kind, monkeypatch) -> None:
    """The child read hostile bytes, so an `ok` with no confinement mark is
    refused for EVERY kind, capture and avatar too (`read_answer`,
    `_was_confined`). An exemption for one kind turns this red."""
    monkeypatch.setattr(si.sys, "platform", "linux")
    assert si.read_answer(_unconfined_ok(), kind=kind) == ("refused", "unavailable")
    if kind != "avatar":  # avatar reads the answer through avatar.py, not _parse
        with pytest.raises(si.SignatureImageError) as caught:
            si._parse(_unconfined_ok(), kind)
        assert caught.value.status == 503


async def test_only_a_capture_reads_the_larger_answer(monkeypatch) -> None:
    """`run_worker` passes the 5 MiB answer cap for a capture only. Every
    other kind keeps the default cap of `_run_child`."""
    seen: dict[str, dict] = {}

    async def spy(argv, data, timeout, **kw):
        import json

        seen[json.loads(argv[-1])["kind"]] = kw
        return None, False

    monkeypatch.setattr(si, "_run_child", spy)
    monkeypatch.setattr(si, "_confine_path", lambda: "/nowhere/child_confine.py")
    for kind in ("logo", "photo", "avatar", si.CAPTURE):
        await si.run_worker(kind, b"x")
    assert seen[si.CAPTURE] == {"cap": si._MAX_CAPTURE_ANSWER_BYTES}
    for kind in ("logo", "photo", "avatar"):
        assert "cap" not in seen[kind], kind
    assert si._MAX_CAPTURE_ANSWER_BYTES > si._MAX_ANSWER_BYTES
