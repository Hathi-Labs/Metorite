"""The front door — turning a Supabase sign-in into a verified identity.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §4.1 · **F8** ·
**D64.1**. Board: WS-31 **CP-12f2**.

⚠️ **This module closes F8.** CP-12a wrote the three admission checks, and
CP-12b wrote the session they protect, but nothing ever called them: the
Console declared no sign-in route, so every operator route answered to the
shared break-glass token alone. This is the exchange that was missing.

**The token is verified by Supabase, not by us.** :func:`introspect` presents
the access token to the issuer and reads back the user. We do not parse the
JWT and we do not check its signature ourselves. Three reasons, in order:

1. **A hand-rolled JWT verifier is the wrong thing to hand-roll.** Algorithm
   confusion, ``alg=none`` and key-id handling are the classic ways this is got
   wrong, and getting it wrong admits anybody.
2. **Revocation is immediate.** A locally verified JWT stays valid until it
   expires, so a person removed at the directory keeps working for the rest of
   the token's life. Asking the issuer closes that window.
3. **It adds no dependency.** ``httpx`` is already a direct dependency of this
   service, and its own pin says that adding a package to a CROSS-TENANT
   service is a supply-chain decision rather than a convenience.

The cost is one HTTP call **at sign-in only**. Every request after that carries
our own ``cc_sess_`` session, which :mod:`customer_console.operator_sessions`
verifies against our own database with no network hop.

⚠️ **The claim shape below is not yet confirmed against a live project.**
Configuring the Microsoft provider is owner work (**H-54**), so no Azure
identity exists to read yet. Every unknown here FAILS CLOSED: a payload this
module does not recognise yields no ``tid``, and ``operators.admit`` refuses.
Confirm the shape when H-54 lands, and change :func:`extract_identity` alone.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("platform.auth")

__all__ = [
    "AZURE_PROVIDER",
    "DEFAULT_TIMEOUT_SECONDS",
    "SigninRejected",
    "SigninUnconfigured",
    "VerifiedIdentity",
    "extract_identity",
    "introspect",
    "safe_ip",
    "supabase_anon_key",
    "supabase_url",
]

#: What Supabase calls the Microsoft Entra provider. It is ``azure``, not
#: ``microsoft`` and not ``entra`` — a named constant because a typo here
#: refuses every operator with a message about the directory, which sends the
#: reader to Entra rather than to this line.
AZURE_PROVIDER = "azure"

#: Sign-in waits this long for the issuer. Short on purpose: a hung sign-in is
#: indistinguishable from a broken one to the person in front of it.
DEFAULT_TIMEOUT_SECONDS = 10

#: ⚠️ ONE message for every rejection, exactly as ``operators._REFUSAL`` does.
#: A door that says *"good token, wrong directory"* has told an attacker which
#: half to work on.
_REFUSAL = "not a platform operator"


class SigninUnconfigured(Exception):
    """Supabase is not configured. Callers map this to **503**.

    Fails CLOSED, the same way ``operators.OperatorUnconfigured`` and the
    interim ``staff.ts`` gate do (D33.1). A box with no issuer pinned must
    refuse everybody rather than admit anybody.
    """


class SigninRejected(Exception):
    """The presented token did not yield an identity we trust. **401**."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """What the issuer told us, reduced to the three things `admit` needs."""

    #: The Microsoft Entra tenant id. ``None`` when the payload carried none,
    #: which ``operators._check_directory`` refuses.
    tid: str | None
    email: str
    #: Supabase's stable user id. Stored as ``operator.directory_subject`` so a
    #: later email change does not silently create a second person.
    subject: str


def safe_ip(value: str | None) -> str | None:
    """An address Postgres will accept as ``INET``, or ``None``.

    ⚠️ **A real bug, caught at build.** The sign-in route passes the caller
    address into an ``INET`` column for the audit record. Anything that is
    not an address makes the cast raise, and the whole sign-in answers 500.
    A test client sends the literal ``testclient``. A misconfigured reverse
    proxy can send a hostname. Neither is a reason to refuse a valid
    operator, so an unreadable value is recorded as nothing at all.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _trimmed(source: dict[str, str], name: str) -> str | None:
    value = (source.get(name) or "").strip()
    return value or None


def supabase_url(env: dict[str, str] | None = None) -> str:
    """The Supabase project URL. Raises when unset."""
    source = os.environ if env is None else env
    value = _trimmed(source, "OPERATOR_SUPABASE_URL")
    if not value:
        raise SigninUnconfigured(
            "OPERATOR_SUPABASE_URL is not configured — the operator console "
            "refuses everyone until the staff issuer is pinned (D64.1)"
        )
    return value.rstrip("/")


def supabase_anon_key(env: dict[str, str] | None = None) -> str:
    """The project's anon key, sent as ``apikey``. Raises when unset.

    The anon key is a public value and is NOT what authenticates the call —
    the operator's own access token does that. It is the project selector
    Supabase requires on every request to its auth API.
    """
    source = os.environ if env is None else env
    value = _trimmed(source, "OPERATOR_SUPABASE_ANON_KEY")
    if not value:
        raise SigninUnconfigured(
            "OPERATOR_SUPABASE_ANON_KEY is not configured — the operator "
            "console cannot reach the staff issuer without it"
        )
    return value


def _providers(payload: dict[str, Any]) -> set[str]:
    """Every provider Supabase says is behind this user."""
    meta = payload.get("app_metadata") or {}
    named = set()
    one = meta.get("provider")
    if isinstance(one, str) and one.strip():
        named.add(one.strip().lower())
    many = meta.get("providers")
    if isinstance(many, list):
        named.update(p.strip().lower() for p in many if isinstance(p, str))
    for identity in payload.get("identities") or []:
        if isinstance(identity, dict):
            p = identity.get("provider")
            if isinstance(p, str) and p.strip():
                named.add(p.strip().lower())
    return named


def _signin_provider(payload: dict[str, Any]) -> str | None:
    """Which provider Supabase attributes THIS user to.

    ⚠️ **Not the same question as "which providers are linked", and the
    difference is a real bypass.** A Supabase account can carry several
    linked identities. If the gate only asked whether a Microsoft identity
    was among them, then a colleague who linked a personal account to theirs
    could be signed in through that OTHER provider and still pass — the
    claim would be "this account has touched our directory", not "this
    sign-in came from it". Compromising the linked account would then reach
    a cross-customer console WITHOUT going through Entra, which is the whole
    thing D64.1 pinned the directory to prevent.

    ``app_metadata.provider`` is the strongest signal the user payload
    carries. ⚠️ It is not a per-session claim, so it does not fully answer
    the question. **The durable fix is to disable manual identity linking in
    the Supabase project**, which is owner configuration and rides with
    H-54. Recorded in the spec rather than left in a comment here.
    """
    meta = payload.get("app_metadata") or {}
    if not isinstance(meta, dict):
        return None
    one = meta.get("provider")
    if isinstance(one, str) and one.strip():
        return one.strip().lower()
    return None

def _azure_tid(payload: dict[str, Any]) -> str | None:
    """The Entra tenant id, read ONLY from the Microsoft identity.

    ⚠️ **Never scan every identity for a ``tid``.** A Supabase user can carry
    more than one linked identity. If one of them is a Microsoft identity from
    our directory and the person signs in through a DIFFERENT one, a scan
    across all identities would still find our tenant id and admit them. The
    check would then be "this account has ever been linked to our directory"
    rather than "this sign-in came from our directory", which is a different
    and much weaker claim.
    """
    for identity in payload.get("identities") or []:
        if not isinstance(identity, dict):
            continue
        if (identity.get("provider") or "").strip().lower() != AZURE_PROVIDER:
            continue
        data = identity.get("identity_data")
        if isinstance(data, dict):
            tid = data.get("tid")
            if isinstance(tid, str) and tid.strip():
                return tid.strip()

    # Some Supabase configurations copy the provider claims up instead of
    # leaving them on the identity. Read those only when Microsoft is the
    # ONLY provider on the account, so the reasoning above still holds.
    if _providers(payload) == {AZURE_PROVIDER}:
        for bag in ("app_metadata", "user_metadata"):
            meta = payload.get(bag)
            if isinstance(meta, dict):
                tid = meta.get("tid")
                if isinstance(tid, str) and tid.strip():
                    return tid.strip()
    return None


def _email_is_verified(payload: dict[str, Any]) -> bool:
    """Whether the issuer says it proved the address.

    ⚠️ Defence in depth, not the main gate. ``operators._check_directory``
    already demands our Entra tenant, which no outsider can present. This
    stops the narrower case where a provider hands back an address it never
    proved, and somebody registers a colleague's address elsewhere.
    """
    if payload.get("email_confirmed_at"):
        return True
    if payload.get("confirmed_at"):
        return True
    for identity in payload.get("identities") or []:
        if not isinstance(identity, dict):
            continue
        data = identity.get("identity_data")
        if isinstance(data, dict) and data.get("email_verified") is True:
            return True
    return False


def _reject(why: str) -> SigninRejected:
    _log.warning("operator.signin_refused", extra={"signin_why": why})
    return SigninRejected(_REFUSAL)


def extract_identity(payload: Any) -> VerifiedIdentity:
    """Reduce Supabase's user payload to the identity `admit` needs.

    Raises :class:`SigninRejected` for anything it cannot read. ⚠️ Every branch
    here fails CLOSED, because this runs on a payload shape that no live
    project has confirmed yet (**H-54**).
    """
    if not isinstance(payload, dict):
        raise _reject("payload")

    subject = payload.get("id")
    if not isinstance(subject, str) or not subject.strip():
        raise _reject("subject")

    email = payload.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise _reject("email")

    # ⚠️ The SIGN-IN provider, not the set of linked ones. See
    # :func:`_signin_provider` for why that difference is a bypass.
    # ⚠️ The SIGN-IN provider, not the set of linked ones. See
    # :func:`_signin_provider` for why that difference is a bypass.
    if _signin_provider(payload) != AZURE_PROVIDER:
        # A password or magic-link sign-in reaches here. It is a real Supabase
        # user, and it is not a Microsoft one, so it is not staff.
        raise _reject("provider")

    if not _email_is_verified(payload):
        raise _reject("unverified")

    return VerifiedIdentity(
        tid=_azure_tid(payload),
        email=email.strip().lower(),
        subject=subject.strip(),
    )


def introspect(
    access_token: str | None,
    *,
    env: dict[str, str] | None = None,
    get: Callable[..., Any] | None = None,
) -> VerifiedIdentity:
    """Ask Supabase who this token belongs to, and reduce the answer.

    *get* is injected by the tests so this module is exercised without a live
    project. In production it is ``httpx.get``.
    """
    token = (access_token or "").strip()
    if not token:
        raise _reject("empty")

    url = supabase_url(env)
    key = supabase_anon_key(env)

    if get is None:  # pragma: no cover - the real transport
        import httpx

        get = httpx.get

    try:
        response = get(
            f"{url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": key},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # ⚠️ A refusal, never an admission. An issuer we cannot reach has told
        # us nothing, and "fail open when the network is bad" is how an outage
        # becomes an authentication bypass.
        _log.warning("operator.signin_unreachable")
        raise _reject("unreachable") from exc

    if getattr(response, "status_code", None) != 200:
        raise _reject("issuer")

    try:
        payload = response.json()
    except Exception as exc:
        raise _reject("body") from exc

    return extract_identity(payload)
