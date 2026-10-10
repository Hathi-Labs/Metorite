"""The connector seam for an external CRM (WS-53, ``crm_platform.md`` §5.1).

``base`` holds the protocol and the shapes, ``registry`` maps a provider slug
to its adapter, and each provider has a package of its own.
"""

from gateway.crm_sources.base import (
    CANONICAL_ENTITIES,
    BudgetExhausted,
    CrmSource,
    NeedsReconnect,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceCursor,
    SourceError,
    SourcePage,
    SourceRecord,
    UntrustedHost,
)

__all__ = [
    "CANONICAL_ENTITIES",
    "BudgetExhausted",
    "CrmSource",
    "NeedsReconnect",
    "OAuthClientConfig",
    "RateLimited",
    "SourceCredential",
    "SourceCursor",
    "SourceError",
    "SourcePage",
    "SourceRecord",
    "UntrustedHost",
]
