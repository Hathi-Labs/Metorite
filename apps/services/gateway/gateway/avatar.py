"""The display image — decode, crop square, resize, re-encode (WS-28q).

Spec: ``project-docs/specs/people_center_app.md`` §3.1a · **D-PC-17**.

**One decision does the work of four rules: what is stored is what this module
produced, never what the browser sent.** Every upload is decoded, cropped to a
square, scaled to exactly :data:`AVATAR_PX` and re-encoded. The uploaded bytes
are discarded.

That is why there is no list of validations to keep in step:

* **Size drift** cannot exist — the stored dimensions are a constant. A
  4000x3000 phone photo and a 64x64 icon both leave as 256x256.
* **Weight** is bounded by the output, not by the input. :data:`MAX_UPLOAD_BYTES`
  only stops somebody streaming a film into the decoder; it is not what keeps
  the table small.
* **The crop is enforced here**, so a client that skips the cropper — or a
  caller hitting the API directly — still cannot produce a non-square avatar.
  The cropper is a courtesy; the square is a guarantee.
* **EXIF, colour profiles and trailing payloads** do not survive a re-encode, so
  the polyglot-file class of problem is gone rather than filtered for.

⚠️ **The decoder is NOT the type check.** MuPDF renders SVG, which was measured
rather than assumed — an SVG handed to ``pymupdf.open(filetype="image")`` opens
happily, and an avatar is displayed on every page in the product. So the bytes
are sniffed against :data:`_MAGIC` **before** the decoder sees them, and only
JPEG, PNG and WebP get that far.

⚠️ **The crop rectangle is FRACTIONAL, not in pixels**, and that too was
measured: a 1000x400 pixel image opens as a 750x300 *point* page, so a pixel
rectangle from the browser would silently crop the wrong region — the first
probe of this produced a 256x192 image from what should have been a square.
Fractions cancel the units, and the client never has to know the DPI.

⚠️ **The decode runs in a confined child, never in the request** (task 2186).
A 2 MB JPEG of 12,000 pixels on a side decodes to 144 megapixels, and native
code reads it. The request process does the byte cap and the sniff only. The
child is ``signature_image_worker.py`` with the kind ``avatar``: it confines
itself with ``acb_skills/child_confine.py`` and caps its own memory, and the
parent kills it at ``signature_image.TIMEOUT_S``. This module imports no
decoder. The fence is ``tests/unit/test_people_avatar.py``, section 5.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

#: The stored edge, in pixels. A constant rather than a setting: the whole
#: point is that every avatar is the same size, and a tunable would be a way to
#: stop that being true.
AVATAR_PX = 256

#: JPEG quality. 82 is where a 256px photo stops getting visibly better and
#: keeps getting bigger.
JPEG_QUALITY = 82

#: Refused before the decoder is handed anything. Not the thing keeping the
#: stored image small — that is the re-encode — but the thing that stops a
#: 200 MB upload becoming the decoder's problem.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

#: A source larger than this on either side is refused rather than decoded. The
#: render is clipped and scaled to 256px so the OUTPUT is bounded either way,
#: but the source still has to be decompressed to get there, and a 2 MB JPEG
#: can expand to a hundred megapixels.
MAX_SOURCE_EDGE = 12_000

#: A source with more pixels than this is refused before the decode. It is the
#: cap of a signature image, and the memory bound of the child fits it.
MAX_SOURCE_PIXELS = 40_000_000

#: Leading bytes → what the file claims to be. Sniffed rather than trusting the
#: multipart ``Content-Type``, which the client chooses.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),          # narrowed below — RIFF is a container
    # GIF is known so a caller can accept it (a signature image, WS-17 SG-1).
    # The profile picture does not: it is not in ACCEPTED.
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

ACCEPTED = ("JPEG", "PNG", "WebP")

#: The largest stored image that the parent takes from the child. A 256px
#: JPEG at quality 82 is under 60 KB, so this bounds only a bad answer.
MAX_OUTPUT_BYTES = 200 * 1024

#: The sentence for each refusal code of the child. A code that is not here
#: (and an answer that is not one) gets :data:`_UNREADABLE`.
_UNREADABLE = "That image could not be read - it may be truncated or corrupt."
_CHILD_REFUSALS = {
    "broken": _UNREADABLE,
    "pixels": (f"That image is too large: more than {MAX_SOURCE_EDGE} pixels on a "
               f"side, or more than {MAX_SOURCE_PIXELS // 1_000_000} million pixels. "
               "Upload a smaller picture."),
    "unavailable": ("This server cannot isolate the image reader, so it cannot "
                    "take profile pictures now. Ask your administrator."),
}


class AvatarError(ValueError):
    """A refusal, with the reason as its text.

    A plain ``ValueError`` so this module imports no web framework; the route
    turns it into a 400 that repeats the sentence, because the person choosing
    the file is the person who has to choose a different one.
    """


#: The name of each sniffed type, as :data:`ACCEPTED` spells it.
_NAMES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WebP", "image/gif": "GIF"}

#: Why an SVG is refused, for each ``what`` that a caller names. The default
#: is the profile picture, so its sentence stays as it was.
_SVG_REACH = {
    "a profile picture": "this one is shown on every page",
    "a signature image": "this one goes out in each mail you send",
}


def sniff(
    data: bytes, *, what: str = "a profile picture",
    accepted: tuple[str, ...] = ACCEPTED,
) -> str:
    """What these bytes actually are, or refuse.

    **This is the type check, and it runs before the decoder**, because the
    decoder is not one: MuPDF renders SVG, and an SVG is a document that can
    carry script and external references. The multipart content-type is not
    consulted at all — the client chooses it.

    ``what`` names the image in the refusal, so a signature upload never reads
    "profile picture" (WS-17 SG-1). ``accepted`` narrows the types: a type
    that the bytes are, but that is not in it, gets the same refusal as a file
    that is not an image.
    """
    use = ", ".join(accepted)
    not_accepted = f"That file is not an image the product accepts. Use one of: {use}."
    if not data:
        raise AvatarError("The file is empty.")
    if data[:4] == b"RIFF":
        # RIFF is a container: WAV and AVI open the same way. The form is
        # 'RIFF' + 4 size bytes + 'WEBP'.
        if len(data) < 12 or data[8:12] != b"WEBP" or "WebP" not in accepted:
            raise AvatarError(not_accepted)
        return "image/webp"
    for magic, mime in _MAGIC:
        if magic != b"RIFF" and data.startswith(magic):
            if _NAMES[mime] not in accepted:
                raise AvatarError(not_accepted)
            return mime
    if data.lstrip()[:5].lower() in (b"<svg ", b"<svg>", b"<?xml"):
        # Named specifically: it is the file somebody will most reasonably try,
        # and "not an image" would be a confusing thing to tell them about an
        # SVG. The refusal is deliberate, not a gap in the sniffer.
        reach = _SVG_REACH.get(what, "other people see it")
        raise AvatarError(
            f"SVG is not accepted for {what} - it is a document that "
            f"can carry script, and {reach}. Use one of: {use}.")
    raise AvatarError(not_accepted)


def normalise(
    data: bytes,
    *,
    crop: tuple[float, float, float] | None = None,
    size: int = AVATAR_PX,
) -> bytes:
    """Bytes in → a square ``size``x``size`` JPEG out. Raises :class:`AvatarError`.

    The form for a caller with no running event loop. A route awaits
    :func:`normalise_async`, because this one starts a loop of its own.

    ``crop`` is ``(x, y, side)`` as **fractions of the source** in ``[0, 1]``,
    which is what the cropper sends. Absent, out of range or nonsense all fall
    back to a centre crop rather than failing: a picture that lands slightly
    wrong is a person's to fix in a second attempt, and refusing the upload
    teaches them the feature is broken.
    """
    return asyncio.run(normalise_async(data, crop=crop, size=size))


async def normalise_async(
    data: bytes,
    *,
    crop: tuple[float, float, float] | None = None,
    size: int = AVATAR_PX,
) -> bytes:
    """:func:`normalise`, for a caller in an event loop. Raises :class:`AvatarError`.

    The byte cap and the sniff run here, in the request. The decode, the crop,
    the scale and the JPEG run in the confined child (task 2186). A child past
    its time bound is killed. A crash, a bad answer and an answer that is not
    a ``size``x``size`` JPEG each become one plain refusal.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarError(
            f"That image is {len(data) // (1024 * 1024)} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB - most phone photos are "
            "under it, and the stored picture is resized to "
            f"{size}x{size} either way.")
    sniff(data)
    # Imported here: signature_image imports this module for sniff().
    from gateway import signature_image as si

    clean = None if not crop else [_finite(v) for v in crop]
    if clean is not None and len(clean) != 3:
        clean = None
    try:
        answer, timed_out = await si.run_worker(
            "avatar", data, crop=clean, size=size, max_side=MAX_SOURCE_EDGE,
            max_pixels=MAX_SOURCE_PIXELS, max_output_bytes=MAX_OUTPUT_BYTES,
            jpeg_quality=JPEG_QUALITY)
    except si.SignatureImageError as exc:
        if exc.code == "unavailable":
            raise AvatarError(_CHILD_REFUSALS["unavailable"]) from exc
        raise AvatarError(str(exc)) from exc
    if timed_out:
        raise AvatarError(
            f"Reading that image took longer than {si.TIMEOUT_S:g} seconds, so it "
            "was stopped. Upload a smaller picture.")
    return _stored_image(si.read_answer(answer), size)


def _stored_image(item: tuple[str, Any], size: int) -> bytes:
    """The JPEG of the child's answer. The parent trusts no field of a child
    that read hostile bytes: the type, the size and the magic must hold."""
    name, value = item
    if name != "ok":
        raise AvatarError(_CHILD_REFUSALS.get(value, _UNREADABLE))
    width, height = value.get("width"), value.get("height")
    if (value.get("content_type") != "image/jpeg" or type(width) is not int
            or type(height) is not int or (width, height) != (size, size)
            or not isinstance(value.get("data"), str)):
        raise AvatarError(_UNREADABLE)
    try:
        jpeg = base64.b64decode(value["data"], validate=True)
    except ValueError:  # binascii.Error is a ValueError
        raise AvatarError(_UNREADABLE) from None
    if not jpeg.startswith(b"\xff\xd8\xff") or len(jpeg) > MAX_OUTPUT_BYTES:
        raise AvatarError(_UNREADABLE)
    return jpeg


def _finite(value: Any) -> float:
    """A float, or 0.0 — NaN and infinity are neither refused nor propagated.

    ``float("nan")`` survives every comparison in :func:`_square` (all of them
    answer False), so a NaN would reach the clip rectangle intact and the
    render would fail somewhere less explainable.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def to_data_uri(jpeg: bytes) -> str:
    """The stored form. One place builds it, so one place can change it."""
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
