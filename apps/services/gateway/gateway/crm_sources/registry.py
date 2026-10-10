"""Provider slug -> adapter class. One list (``crm_platform.md`` §5.1).

A second provider adds one line here and one adapter package beside ``zoho/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from gateway.crm_sources.base import CrmSource
from gateway.crm_sources.zoho import ZohoSource

SOURCES: Mapping[str, type[CrmSource]] = MappingProxyType(
    {
        "zoho": ZohoSource,
    }
)


def source_class(provider: str) -> type[CrmSource]:
    """The adapter class of ``provider``, or ``KeyError`` for an unknown slug."""
    try:
        return SOURCES[provider]
    except KeyError:
        raise KeyError(f"No CRM source is registered for {provider!r}") from None


__all__ = ["SOURCES", "source_class"]
