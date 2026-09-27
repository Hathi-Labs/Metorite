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
"""

from __future__ import annotations

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

#: Leading bytes → what the file claims to be. Sniffed rather than trusting the
#: multipart ``Content-Type``, which the client chooses.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),          # narrowed below — RIFF is a container
)

ACCEPTED = ("JPEG", "PNG", "WebP")


class AvatarError(ValueError):
    """A refusal, with the reason as its text.

    A plain ``ValueError`` so this module imports no web framework; the route
    turns it into a 400 that repeats the sentence, because the person choosing
    the file is the person who has to choose a different one.
    """


def sniff(data: bytes) -> str:
    """What these bytes actually are, or refuse.

    **This is the type check, and it runs before the decoder**, because the
    decoder is not one: MuPDF renders SVG, and an SVG is a document that can
    carry script and external references. The multipart content-type is not
    consulted at all — the client chooses it.
    """
    if not data:
        raise AvatarError("The file is empty.")
    if data[:4] == b"RIFF":
        # RIFF is a container: WAV and AVI open the same way. The form is
        # 'RIFF' + 4 size bytes + 'WEBP'.
        if len(data) < 12 or data[8:12] != b"WEBP":
            raise AvatarError(
                f"That file is not an image the product accepts. Use one of: "
                f"{', '.join(ACCEPTED)}.")
        return "image/webp"
    for magic, mime in _MAGIC:
        if magic != b"RIFF" and data.startswith(magic):
            return mime
    if data.lstrip()[:5].lower() in (b"<svg ", b"<svg>", b"<?xml"):
        # Named specifically: it is the file somebody will most reasonably try,
        # and "not an image" would be a confusing thing to tell them about an
        # SVG. The refusal is deliberate, not a gap in the sniffer.
        raise AvatarError(
            "SVG is not accepted for a profile picture - it is a document that "
            f"can carry script, and this one is shown on every page. Use one of: "
            f"{', '.join(ACCEPTED)}.")
    raise AvatarError(
        f"That file is not an image the product accepts. Use one of: "
        f"{', '.join(ACCEPTED)}.")


def normalise(
    data: bytes,
    *,
    crop: tuple[float, float, float] | None = None,
    size: int = AVATAR_PX,
) -> bytes:
    """Bytes in → a square ``size``x``size`` JPEG out. Raises :class:`AvatarError`.

    ``crop`` is ``(x, y, side)`` as **fractions of the source** in ``[0, 1]``,
    which is what the cropper sends. Absent, out of range or nonsense all fall
    back to a centre crop rather than failing: a picture that lands slightly
    wrong is a person's to fix in a second attempt, and refusing the upload
    teaches them the feature is broken.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise AvatarError(
            f"That image is {len(data) // (1024 * 1024)} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB - most phone photos are "
            "under it, and the stored picture is resized to "
            f"{size}x{size} either way.")
    sniff(data)

    import pymupdf

    try:
        page = pymupdf.open(stream=data, filetype="image")[0]
    except Exception as exc:
        raise AvatarError(
            "That image could not be read - it may be truncated or corrupt."
        ) from exc

    width, height = float(page.rect.width), float(page.rect.height)
    if width <= 0 or height <= 0:
        raise AvatarError("That image has no dimensions.")
    if max(width, height) > MAX_SOURCE_EDGE:
        raise AvatarError(
            f"That image is larger than {MAX_SOURCE_EDGE} pixels on a side.")

    clip = _square(page, width, height, crop)
    # ⚠️ `Matrix(scale, scale)` is NOT exact: MuPDF rounds the transformed
    # rectangle outward, so a zoomed crop rendered at `size / side` came back
    # 257x257 — measured. `Rect.torect` builds the matrix that maps the clip
    # onto the target box exactly, which is the difference between "about 256"
    # and the constant this whole design rests on.
    pixmap = page.get_pixmap(
        matrix=clip.torect(pymupdf.Rect(0, 0, size, size)), clip=clip,
        # No alpha: JPEG has none, and the composite lands on WHITE rather than
        # black — verified against a fully transparent PNG, because the default
        # for "no alpha" is the kind of thing that is only wrong in the corner
        # of somebody's profile picture.
        alpha=False,
    )
    return pixmap.tobytes("jpg", jpg_quality=JPEG_QUALITY)


def _square(page: Any, width: float, height: float,
            crop: tuple[float, float, float] | None) -> Any:
    """The clip rectangle: always square, always inside the page.

    The clamping is what makes the fractional protocol safe to accept from a
    browser. A caller can send ``(9, 9, 5)`` and gets the largest square that
    fits, not an exception and not a rectangle off the edge of the image.
    """
    import pymupdf

    shortest = min(width, height)
    if not crop:
        side = shortest
        x = (width - side) / 2
        y = (height - side) / 2
    else:
        fx, fy, fside = (_finite(v) for v in crop)
        # A zero or negative side means the client sent nothing usable; the
        # whole shortest edge is the honest reading of "no valid selection".
        side = shortest if fside <= 0 else min(fside * shortest, shortest)
        x = min(max(fx * width, 0.0), width - side)
        y = min(max(fy * height, 0.0), height - side)
    return pymupdf.Rect(x, y, x + side, y + side)


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
