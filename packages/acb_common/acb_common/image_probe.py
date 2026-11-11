"""Read an image's format and dimensions from its header. Nothing else.

Used by the organisation-branding routes to decide whether an uploaded logo is
acceptable before it is stored and served back to every member of the org.

WHY THIS EXISTS RATHER THAN A CALL TO PILLOW
--------------------------------------------
An uploaded logo is attacker-controlled bytes from any admin of any tenant, and
it ends up rendered in every colleague's browser. The question we need answered
is narrow — *is this a PNG/JPEG/WebP, and how big is the canvas* — and the
narrowest possible reader is the correct tool for it:

* **It never decodes pixels.** Image decoders are where image CVEs live: a
  malicious PNG is dangerous because something inflates it. This module reads
  fixed offsets and length-prefixed chunk headers and stops. There is no
  decompression, no allocation proportional to the declared dimensions, and no
  code path that touches the payload after the header.
* **It has no dependencies.** Pillow is not currently in the tree, and pulling a
  C-extension image library into the gateway to read four integers would be the
  larger security decision of the two.

The trade is honest and worth stating: we verify the header, not the whole file.
A file whose header says PNG and whose body is corrupt passes here and renders
as a broken image in the browser. That is a cosmetic failure the uploader sees
immediately, and it is a much better failure mode than running a full decoder
over hostile input on the server.

**The format sniff is done on the BYTES, never on the declared content type.**
A browser's `File.type` comes from the client and an API caller can send
anything at all; `image/png` on a file that is really an SVG is exactly how a
script gets into a page that believed it was serving a raster. The MIME type
this module returns is derived from the magic bytes and is the only one that is
allowed downstream.

**The one sniff of a chat image** (WS-VOX VOX-3, ``projects_ai_chat.md`` §31).
:func:`sniff_image_mime` gives the type of a PNG, JPEG, GIF or WebP file from
its first bytes, and it reads nothing more. ``probe_image`` uses the same
sniff, and it still refuses a GIF logo. ``acb_skills.attachment_tools`` uses
it for ``analyze_image``. The module moved here from ``gateway/`` for that
caller, because ``acb_skills`` cannot import the gateway. It imports the
standard library only, so it decodes no pixel in any process.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ImageInfo", "UnsupportedImage", "probe_image", "sniff_image_mime"]

#: The first bytes of each image type that :func:`sniff_image_mime` names.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"
_GIF_MAGICS = (b"GIF87a", b"GIF89a")


def sniff_image_mime(data: bytes) -> str | None:
    """The type of *data* by its first bytes, or ``None``.

    It names ``image/png``, ``image/jpeg``, ``image/gif`` or ``image/webp``.
    A WebP file is ``RIFF``, four size bytes, then ``WEBP``, because WAV and
    AVI files also start with ``RIFF``. It never trusts a declared type or a
    file name, and it decodes no pixel. An SVG is text that can hold a
    script, so it is never an image here.
    """
    head = bytes(data[:16])
    if head.startswith(_PNG_MAGIC):
        return "image/png"
    if head.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if head[:6] in _GIF_MAGICS:
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


class UnsupportedImage(ValueError):
    """The bytes are not a raster image in a format we accept.

    Carries a message written for the person who chose the file, because it is
    relayed to them verbatim by the upload route.
    """


@dataclass(frozen=True)
class ImageInfo:
    """What the header says. Dimensions are in pixels."""

    mime: str
    width: int
    height: int


def probe_image(data: bytes) -> ImageInfo:
    """Identify `data` as PNG, JPEG or WebP and read its canvas size.

    Raises `UnsupportedImage` for anything else — including SVG, which is
    refused deliberately rather than by omission (see `_reject_reason`).
    """
    if len(data) < 16:
        raise UnsupportedImage("That file is too small to be an image.")

    # The one sniff. A GIF falls through to the refusal, as before VOX-3.
    mime = sniff_image_mime(data)
    if mime == "image/png":
        return _probe_png(data)
    if mime == "image/jpeg":
        return _probe_jpeg(data)
    if mime == "image/webp":
        return _probe_webp(data)

    raise UnsupportedImage(_reject_reason(data))


def _reject_reason(data: bytes) -> str:
    """Name the format we think this is, so the message is actionable.

    "Unsupported file" sends someone back to their file manager to guess. The
    two cases worth naming are the two people actually try: an SVG (the format
    a designer hands over) and a PDF (what a brand guidelines export looks
    like).
    """
    head = data[:1024].lstrip()[:512].lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        # Not an oversight. An SVG is a document with a script element and
        # external references available to it, and this one would be stored by
        # one tenant and rendered in the shell of that tenant's every member.
        # Browsers do block script in an <img>-embedded SVG, but that is one
        # sandbox deep and it is not the sandbox we would be relying on if the
        # lockup ever became an inline <svg> for theming reasons. Raster is
        # sufficient for a 28px header slot; SVG is a bigger decision than this
        # feature needs, so it is refused until it has been made deliberately.
        return (
            "SVG logos are not accepted. Please export your logo as a PNG "
            "(with a transparent background, at 2× or 3× the display size)."
        )
    if data[:5] == b"%PDF-":
        return "That is a PDF. Please upload a PNG, JPEG or WebP image."
    return "That file is not a PNG, JPEG or WebP image."


def _probe_png(data: bytes) -> ImageInfo:
    # IHDR is required by the spec to be the first chunk: 8-byte signature,
    # 4-byte length, 4-byte type, then width and height as big-endian u32.
    if data[12:16] != b"IHDR" or len(data) < 24:
        raise UnsupportedImage("That PNG file looks damaged.")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return ImageInfo("image/png", width, height)


#: Start-of-frame markers carry the dimensions. 0xC4 (DHT), 0xC8 (JPG) and
#: 0xCC (DAC) share the 0xC0-0xCF range but are not frame headers.
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _probe_jpeg(data: bytes) -> ImageInfo:
    """Walk the segment chain to the first start-of-frame.

    JPEG has no fixed dimension offset — the frame header sits after a variable
    run of metadata segments (EXIF, ICC profiles, comments), so it has to be
    walked. The walk is bounded by the buffer and every step advances, so a
    truncated or hostile file terminates rather than looping.
    """
    i = 2
    end = len(data)
    while i + 3 < end:
        if data[i] != 0xFF:
            # Not at a marker boundary: the file is malformed, and scanning
            # forward for the next 0xFF would be guessing at attacker input.
            break
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2  # Standalone markers: no length field.
            continue
        if marker == 0xDA:
            break  # Start of scan — compressed data begins, no SOF was found.
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if length < 2:
            break
        if marker in _JPEG_SOF:
            # Segment: length(2) precision(1) height(2) width(2).
            if i + 9 > end:
                break
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            return ImageInfo("image/jpeg", width, height)
        i += 2 + length
    raise UnsupportedImage("That JPEG file looks damaged.")


def _probe_webp(data: bytes) -> ImageInfo:
    """Read the canvas size from whichever of the three WebP variants this is."""
    chunk = data[12:16]

    if chunk == b"VP8X" and len(data) >= 30:
        # Extended format: 24-bit canvas dimensions, minus one, little-endian.
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return ImageInfo("image/webp", width, height)

    if chunk == b"VP8 " and len(data) >= 30:
        # Lossy: 3-byte frame tag, then the 3-byte start code, then 14-bit
        # dimensions (the top two bits of each u16 are the scaling factor).
        if data[23:26] != b"\x9d\x01\x2a":
            raise UnsupportedImage("That WebP file looks damaged.")
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return ImageInfo("image/webp", width, height)

    if chunk == b"VP8L" and len(data) >= 25:
        # Lossless: a 0x2F signature byte, then 14 bits of width-1 and 14 bits
        # of height-1 packed across the next four bytes, little-endian.
        if data[20] != 0x2F:
            raise UnsupportedImage("That WebP file looks damaged.")
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return ImageInfo("image/webp", width, height)

    raise UnsupportedImage("That WebP file looks damaged.")
