"""Provider slug -> adapter class. One list (``knowledge_base.md`` §7.1).

A second provider (OneDrive, KC-2) adds one line here and one adapter package
beside ``google_drive/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from gateway.kb_sources.google_drive import GoogleDriveSource

SOURCES: Mapping[str, type[GoogleDriveSource]] = MappingProxyType(
    {
        GoogleDriveSource.provider: GoogleDriveSource,
    }
)


def source_class(provider: str) -> type[GoogleDriveSource]:
    """The adapter class of ``provider``, or ``KeyError`` for an unknown slug."""
    try:
        return SOURCES[provider]
    except KeyError:
        raise KeyError(f"No Knowledge source is registered for {provider!r}") from None


__all__ = ["SOURCES", "source_class"]
