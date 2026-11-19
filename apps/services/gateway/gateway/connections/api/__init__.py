"""API connections: the reviewed templates of the outside APIs (API-1).

Spec: ``project-docs/specs/api_connections.md``. Decision D228.

* ``template`` holds the grammar and the pure validator.
* ``zoho_books`` is the first template.
* :data:`TEMPLATES` holds each template. Each one passes the validator when
  this package loads, so a template that breaks a rule cannot import.

This package does no I/O. It reads no setting, no environment and no
credential. The gateway routes of API-3 make the calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from gateway.connections.api.template import ClientHosts, Template, validate
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.crm_sources.zoho.auth import API_HOSTS

#: Each client that Metorite holds, with the origins of its own host list. A
#: token of a client goes only to these hosts (§2.1, §2.7).
CLIENT_HOSTS: ClientHosts = MappingProxyType({
    "zoho": frozenset(f"https://{host}" for host in API_HOSTS),
})


def _registry(*templates: Template) -> Mapping[str, Template]:
    out: dict[str, Template] = {}
    for one in templates:
        validate(one, CLIENT_HOSTS)
        if one.id in out:
            raise ValueError(f"the template id {one.id!r} is used twice")
        out[one.id] = one
    return MappingProxyType(out)


#: The T1 templates, by id.
TEMPLATES: Mapping[str, Template] = _registry(ZOHO_BOOKS)


def template(template_id: object) -> Template | None:
    """The template *template_id*, or ``None``."""
    return TEMPLATES.get(template_id) if isinstance(template_id, str) else None


__all__ = ["CLIENT_HOSTS", "TEMPLATES", "template"]
