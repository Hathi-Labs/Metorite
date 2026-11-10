"""The connector seam for a company file source (WS-61 KC-1a).

Spec: ``project-docs/specs/knowledge_base.md`` §7.1 and
``project-docs/specs/knowledge_curation.md`` §7. ``base`` holds the protocol
and the shapes, ``http`` the one network door, ``registry`` maps a provider
slug to its adapter, and each provider has a package of its own.
"""

from gateway.kb_sources.base import (
    AccountIdentity,
    DiscoverCursor,
    DiscoverPage,
    KbSource,
    SourceNode,
)

__all__ = ["AccountIdentity", "DiscoverCursor", "DiscoverPage", "KbSource", "SourceNode"]
