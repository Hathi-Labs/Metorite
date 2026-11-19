"""The connect step of an API template: pure helpers (API-2, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.2 (the row), §2.10 (Zoho
Books) and §2.11 (the connect flow). The routes are in
``gateway/routes/integrations_api_connect.py``. They do the I/O.

* **The scopes.** :data:`ZOHO_BOOKS_SCOPES` are the Books read scopes. The
  CRM's Zoho client asks for them in the consent request. Zoho takes the
  scopes of each request, so the client needs no new registration
  (``docs/ZOHO_OAUTH_SETUP.md`` section 1). Zoho refuses a whole consent for
  one unknown scope. The first real consent, an owner act, confirms the names.
* **The state.** A Books state is signed with :data:`STATE_PURPOSE`, and it
  carries the public mark :data:`STATE_MARK` before the signed text. The one
  registered Zoho redirect URI is the CRM's. The BFF callback reads the mark
  and sends the state to the Books route. The purpose is only in the MAC, so a
  forged mark changes only where the state goes. The Books route still checks
  the MAC.
* **The context.** :func:`parse_organizations` reads the answer of
  ``GET /books/v3/organizations``. It refuses an id that is not
  :data:`ZOHO_ORG_ID`. The Zoho ``organization_id`` is a context value of the
  connection. It is never a Metorite organization and never a model argument.
* **The row.** :func:`provider_meta` keeps the public facts of §2.2 only:
  :data:`PUBLIC_META_KEYS`. ``grant`` is ``admin``: the API-3 routes and the
  API-4 attach step ask for ``integrations:manage`` too while it is ``admin``.
  Only the admin role holds that permission.

This module does no I/O. It reads no setting, no environment and no
credential store, and it imports no ``acb_skills.integrations``. Fences:
``tests/unit/test_api_connect_zoho_books.py`` and
``tests/unit/test_api_no_env_credentials.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from gateway.connections.api.template import Template, build
from gateway.crm_sources.base import SourceCredential, UntrustedHost
from gateway.crm_sources.zoho.auth import (
    META_ACCOUNTS_SERVER,
    META_API_DOMAIN,
    META_LOCATION,
)

#: The purpose of a Books state. A state of another purpose never verifies.
STATE_PURPOSE = "api.zoho_books.connect"

#: The public routing mark before a Books state. It is not secret and it
#: proves nothing. A CRM state starts with the base64url text of a JSON
#: object (``eyJ``), so a CRM state never starts with this mark.
STATE_MARK = "zb."

#: The Books read scopes (§2.10). ``settings`` reads the organizations, and
#: ``invoices`` reads the invoices of the template. To confirm by the first
#: real consent (an owner act).
ZOHO_BOOKS_SCOPES: tuple[str, ...] = (
    "ZohoBooks.settings.READ",
    "ZohoBooks.invoices.READ",
)

#: The context read of the connect step.
ORGANIZATIONS_PATH = "/books/v3/organizations"

#: The op of the test read, and its one argument.
TEST_READ_OP = "list_invoices"
TEST_READ_ARGS: Mapping[str, str] = {"per_page": "1"}

#: The shape of a Zoho Books organization id.
ZOHO_ORG_ID = re.compile(r"[0-9]{1,20}")

#: The most organizations that one answer may name. More reads as unreadable.
MAX_ORGANIZATIONS = 50

#: The longest organization name that the pick shows.
MAX_NAME = 100

#: The default grant of a books connection (D-API-3): the admin role only.
GRANT_ADMIN = "admin"

META_ORGANIZATION_ID = "organization_id"
META_TEMPLATE_VERSION = "template_version"
META_OPS_HASH = "ops_hash"
META_GRANT = "grant"

#: The public facts of the row (§2.2). No other key reaches ``provider_meta``.
PUBLIC_META_KEYS: tuple[str, ...] = (
    META_LOCATION,
    META_API_DOMAIN,
    META_ACCOUNTS_SERVER,
    META_ORGANIZATION_ID,
    META_TEMPLATE_VERSION,
    META_OPS_HASH,
    META_GRANT,
)


class ContextUnreadable(ValueError):
    """The answer of the context read cannot be used. The text names no value."""


# ── The state mark ──────────────────────────────────────────────────────────


def mark_state(state: str) -> str:
    """The state with its public routing mark."""
    return f"{STATE_MARK}{state}"


def unmark_state(value: str | None) -> str | None:
    """The signed state after the mark, or ``None`` when the mark is absent."""
    if not isinstance(value, str) or not value.startswith(STATE_MARK):
        return None
    return value[len(STATE_MARK):]


# ── The host and the calls ──────────────────────────────────────────────────


def template_host(template: Template, api_domain: str) -> str:
    """*api_domain* when it is a host of *template*, else :class:`UntrustedHost`.

    The token answer names the API domain, so it is input from outside.
    ``exchange_code`` checks it against the Zoho list already. This check
    binds it to the template too (§2.3, step 7).
    """
    if api_domain not in {origin for _, origin in template.hosts}:
        raise UntrustedHost("The API domain is not a host of the template")
    return api_domain


def organizations_url(host: str) -> str:
    """The URL of the context read on *host*, a checked template host."""
    return f"{host}{ORGANIZATIONS_PATH}"


def call_url(host: str, template: Template, op: str, args: Mapping[str, Any],
             context: Mapping[str, str]) -> str:
    """The URL of *op* on *host*, through the ONE builder (§2.1).

    The builder refuses an argument that names a context key. The gateway then
    sets each context key, after the arguments (§2.3, step 5).
    """
    call = build(template, op, args)
    query = [*call.query, *sorted(context.items())]
    return f"{host}{call.path}" + (f"?{urlencode(query)}" if query else "")


def auth_headers(template: Template, access_token: str) -> dict[str, str]:
    """The headers of one call: the sign-in header and the plain encoding."""
    sign_in = template.sign_in
    value = f"{sign_in.scheme} {access_token}" if sign_in.scheme else access_token
    return {sign_in.header: value, "Accept": "application/json",
            "Accept-Encoding": "identity"}


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ContextUnreadable("the answer is not JSON") from None
    if not isinstance(data, dict):
        raise ContextUnreadable("the answer is not a JSON object")
    return data


def _plain_name(value: object) -> str:
    """A short, printable copy of a name that Zoho sent (third-party text)."""
    spaced = " ".join(str(value or "").split())
    return "".join(ch for ch in spaced if ch.isprintable()).strip()[:MAX_NAME]


def parse_organizations(body: bytes) -> list[tuple[str, str]]:
    """The ``(organization_id, name)`` pairs of a ``/organizations`` answer.

    Raises :class:`ContextUnreadable` for an answer that is not a Zoho
    success, or that names an id that is not :data:`ZOHO_ORG_ID`. One bad id
    refuses the whole answer, so a pick never shows an id that a call cannot
    use. An id named twice counts once.
    """
    data = _json(body)
    if data.get("code") not in (0, "0"):
        raise ContextUnreadable("the answer is not a success")
    rows = data.get("organizations")
    if not isinstance(rows, list) or len(rows) > MAX_ORGANIZATIONS:
        raise ContextUnreadable("the answer has no list of organizations")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ContextUnreadable("an organization is not an object")
        raw = row.get("organization_id")
        if isinstance(raw, bool) or not isinstance(raw, str | int):
            raise ContextUnreadable("an organization has no id")
        org_id = str(raw)
        if not ZOHO_ORG_ID.fullmatch(org_id):
            raise ContextUnreadable("an organization id has the wrong shape")
        if org_id in seen:
            continue
        seen.add(org_id)
        out.append((org_id, _plain_name(row.get("name"))))
    return out


def read_answer_ok(template: Template, op: str, body: bytes) -> bool:
    """True when *body* is a JSON object that holds the list of *op*.

    The list key is the ``items`` key of the op's total mark. An op with no
    mark needs only a JSON object.
    """
    try:
        data = _json(body)
    except ContextUnreadable:
        return False
    row = template.op(op)
    if row is None:
        return False
    if row.total is not None and not isinstance(data.get(row.total.items), list):
        return False
    return data.get("code") in (None, 0, "0")


# ── The row ─────────────────────────────────────────────────────────────────


def ops_hash(template: Template) -> str:
    """The hash of the approved operations of *template* (§2.2).

    For a T1 template, each op of the reviewed file is approved. A change of
    an op changes the hash, so a later slice can ask the admin again (§2.2,
    the rule of STU-B1b-3).
    """
    ops = [
        {"name": op.name, "method": op.method, "path": op.path, "cls": op.cls,
         "path_shapes": [list(p) for p in op.path_shapes],
         "query": [list(q) for q in op.query]}
        for op in sorted(template.operations, key=lambda o: o.name)
    ]
    digest = hashlib.sha256(
        json.dumps(ops, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def provider_meta(template: Template, credential_meta: Mapping[str, str], *,
                  organization_id: str) -> dict[str, str]:
    """The ``provider_meta`` of the row: the public facts of §2.2 only.

    The data centre comes from the credential that the code exchange made.
    Each other key of *credential_meta* is dropped. The grant is
    :data:`GRANT_ADMIN`.
    """
    if not ZOHO_ORG_ID.fullmatch(organization_id or ""):
        raise ContextUnreadable("the organization id has the wrong shape")
    meta = {
        key: str(credential_meta[key])
        for key in (META_LOCATION, META_API_DOMAIN, META_ACCOUNTS_SERVER)
        if credential_meta.get(key)
    }
    meta.update({
        META_ORGANIZATION_ID: organization_id,
        META_TEMPLATE_VERSION: str(template.version),
        META_OPS_HASH: ops_hash(template),
        META_GRANT: GRANT_ADMIN,
    })
    return {k: meta[k] for k in PUBLIC_META_KEYS if k in meta}


# ── The pending pick ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PendingPick:
    """A consent that found several organizations, until the admin picks one."""

    member: str
    credential: SourceCredential
    choices: tuple[tuple[str, str], ...]


def credential_text(credential: SourceCredential) -> str:
    """The plain JSON of *credential*. The caller seals it before it stores it."""
    return json.dumps({
        "access_token": credential.access_token,
        "refresh_token": credential.refresh_token,
        "expires_at": credential.expires_at.isoformat(),
        "scopes": list(credential.scopes),
        "provider_meta": dict(credential.provider_meta),
    }, separators=(",", ":"))


def credential_from_text(text: str) -> SourceCredential | None:
    """The credential of :func:`credential_text`, or ``None`` for a bad shape."""
    try:
        data = json.loads(text)
        return SourceCredential(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
            scopes=tuple(str(s) for s in data.get("scopes") or ()),
            provider_meta={str(k): str(v) for k, v in (data.get("provider_meta") or {}).items()},
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def pending_record(member: str, sealed_credential: str,
                   choices: list[tuple[str, str]]) -> str:
    """The JSON of a pending pick. *sealed_credential* is already sealed."""
    return json.dumps({
        "member": member, "credential": sealed_credential,
        "choices": [[i, n] for i, n in choices],
    }, separators=(",", ":"))


def pending_from(raw: Any, open_value: Any) -> PendingPick | None:
    """The pending pick of *raw*, or ``None`` for a bad or unopened record.

    *open_value* opens the sealed credential (``store.open_value``).
    """
    try:
        data = json.loads(raw)
        plain = open_value(str(data["credential"]))
        credential = credential_from_text(plain) if plain else None
        choices = tuple((str(i), str(n)) for i, n in data["choices"])
        if credential is None or not choices:
            return None
        if not all(ZOHO_ORG_ID.fullmatch(i) for i, _ in choices):
            return None
        return PendingPick(member=str(data["member"]), credential=credential,
                           choices=choices)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


__all__ = [
    "GRANT_ADMIN",
    "MAX_ORGANIZATIONS",
    "META_GRANT",
    "META_OPS_HASH",
    "META_ORGANIZATION_ID",
    "META_TEMPLATE_VERSION",
    "ORGANIZATIONS_PATH",
    "PUBLIC_META_KEYS",
    "STATE_MARK",
    "STATE_PURPOSE",
    "TEST_READ_ARGS",
    "TEST_READ_OP",
    "ZOHO_BOOKS_SCOPES",
    "ZOHO_ORG_ID",
    "ContextUnreadable",
    "PendingPick",
    "auth_headers",
    "call_url",
    "credential_from_text",
    "credential_text",
    "mark_state",
    "ops_hash",
    "organizations_url",
    "parse_organizations",
    "pending_from",
    "pending_record",
    "provider_meta",
    "template_host",
    "read_answer_ok",
    "unmark_state",
]
