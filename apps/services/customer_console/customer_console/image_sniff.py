"""Name the format of an image from its first bytes (VOX-4).

Spec: ``project-docs/specs/projects_ai_chat.md`` §31.5 VOX-4.

🔴 **No pixel is decoded.** The "Try image analysis" route on ``/tiers``
sends the file to a vision model, and that model decodes it. This module only
reads the magic bytes, so a file that claims to be a PNG and is not is refused
before it reaches a vendor. The file name and the declared content type are
never trusted.

⚠️ The Console may not import a tenant package
(``tests/unit/test_console_dependency_boundary.py``), so this small sniffer
lives here and not in a shared one.
"""
from __future__ import annotations

__all__ = ["IMAGE_TYPES", "sniff_image"]

#: The formats the try route takes.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def sniff_image(data: bytes) -> str | None:
    """The media type of *data*, or ``None`` when it is not a supported image."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
