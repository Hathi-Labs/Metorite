"""Provider slug -> adapter class. One list (``crm_platform.md`` §5.1).

A second provider adds one line here and one adapter package beside ``zoho/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType, ModuleType

from gateway.crm_sources.base import CrmSource
from gateway.crm_sources.zoho import ZohoSource
from gateway.crm_sources.zoho import mapping as zoho_mapping

SOURCES: Mapping[str, type[CrmSource]] = MappingProxyType(
    {
        "zoho": ZohoSource,
    }
)


#: Provider slug -> the module that maps its records to canonical columns
#: (``map_record``, ``map_field_def``, ``ENTITY_TABLES``, ``OWNER_NAME_KEY``).
#: The sync engine reads a provider's field names through this module only.
MAPPINGS: Mapping[str, ModuleType] = MappingProxyType({"zoho": zoho_mapping})


def mapping_for(provider: str) -> ModuleType:
    """The record mapping of ``provider``, or ``KeyError`` for an unknown slug."""
    try:
        return MAPPINGS[provider]
    except KeyError:
        raise KeyError(f"No CRM mapping is registered for {provider!r}") from None


def source_class(provider: str) -> type[CrmSource]:
    """The adapter class of ``provider``, or ``KeyError`` for an unknown slug."""
    try:
        return SOURCES[provider]
    except KeyError:
        raise KeyError(f"No CRM source is registered for {provider!r}") from None


__all__ = ["MAPPINGS", "SOURCES", "mapping_for", "source_class"]
