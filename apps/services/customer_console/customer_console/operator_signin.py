"""The front door — turning a Supabase sign-in into a verified identity.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §4.1 · **F8** ·
**D64.1**, amended by **D70.1**. Board: WS-31 **CP-12f2** and **CP-12h**.

⚠️ **TWO directories, one switch** (**D70**, 2026-09-01). ``azure`` reads the
Entra ``tid``. ``google`` reads the Google Workspace ``hd`` hosted domain.
:func:`customer_console.operators.signin_provider` picks one, and it defaults
to ``azure``.

⚠️ **The SWITCH ships dark. This module does NOT.** An unset variable keeps
check 1 on the Entra ``tid``, exactly as before. It does not keep
:func:`_email_is_verified` as it was. That function dropped the top-level
``email_confirmed_at`` and now reads the sign-in provider's identity alone
(spec §8.1 done-when 31), which tightens the ``azure`` path too.

**Measured 2026-09-01**, on one Entra payload with a top-level
``email_confirmed_at`` and no ``email_verified`` on its identity: the parent
commit returned a :class:`VerifiedIdentity`, and this one raises
:class:`SigninRejected` (a **401**). The change is deliberate, and D70 asks
for it. It is safe today because D70 records that we hold no Entra directory
and H-54 is unfinished, so nobody signs in on that path. ⚠️ **If that stops
being true, this tightening bites `azure` first.**

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
Configuring the provider is owner work (**H-54**), so no staff identity exists
to read yet. **Nobody has measured whether Supabase copies ``hd`` into
``identities[].identity_data``**, and H-54 item 3 records that as unmeasured.
Every unknown here FAILS CLOSED: a payload this module does not recognise
yields no directory claim, and ``operators.admit`` refuses. So a wrong guess
refuses everybody rather than admitting anybody. Confirm the shape when H-54
lands, and change :func:`_google_hd` alone.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from customer_console import operators

_log = logging.getLogger("platform.auth")

__all__ = [
    "AZURE_PROVIDER",
    "DEFAULT_TIMEOUT_SECONDS",
    "GOOGLE_PROVIDER",
    "SigninRejected",
    "SigninUnconfigured",
    "VerifiedIdentity",
    "extract_identity",
    "introspect",
    "safe_ip",
    "supabase_anon_key",
    "supabase_url",
]

#: The provider vocabulary lives in :mod:`customer_console.operators`, beside
#: the env variables each provider pins. These two names are bound here so a
#: payload reader reads one word rather than a dotted path. They are the SAME
#: objects, never a second definition.
AZURE_PROVIDER = operators.AZURE_PROVIDER
GOOGLE_PROVIDER = operators.GOOGLE_PROVIDER

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

    #: The DIRECTORY claim — the Entra ``tid`` on the ``azure`` path, and the
    #: Google Workspace ``hd`` hosted domain on the ``google`` path (**D70**).
    #: ``None`` when the payload carried none, which
    #: ``operators._check_directory`` refuses. The field keeps the name ``tid``
    #: so every call site and every test of the built code reads unchanged.
    tid: str | None
    email: str
    #: Supabase's stable user id. Stored as ``operator.directory_subject`` so a
    #: later email change does not silently create a second person.
    subject: str
    #: HOW this sign-in proved itself — the provider name, or
    #: :data:`operators.EMAIL_METHOD` for a code to the inbox (**D71.3**).
    #: ``operators.admit`` matches it against the box's method set and against
    #: the operator's own ``allowed_methods`` (**D71.4**).
    #:
    #: ⚠️ **The default is ``None``, meaning "not stated", and NOT a method
    #: name.** Every construction in this module sets it. A test or an older
    #: call site that omits it gets the pre-D71 behaviour, where ``admit``
    #: runs no method check at all. Defaulting to a real name would instead
    #: make such a caller ASSERT something it never measured, and the two ways
    #: that could be wrong are opposite — a default of ``email`` would claim
    #: the weakest method, and a default of ``google`` would let a row pinned
    #: to Google admit a code.
    method: str | None = None


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

    ⚠️ **The remaining hole, measured 2026-09-01, and NOT closed here.** This
    reads one provider NAME. It cannot tell two identities apart when both
    carry that name. An operator who links a personal Google account to their
    own Supabase user therefore holds two ``google`` identities, and
    :func:`_google_hd` reads the ``hd`` off whichever one has it. Nobody
    outside can reach this, because it needs the operator to link the second
    account themselves. Turning identity linking OFF closes it, and H-54 asks
    the owner for exactly that.
    """
    meta = payload.get("app_metadata") or {}
    if not isinstance(meta, dict):
        return None
    one = meta.get("provider")
    if isinstance(one, str) and one.strip():
        return one.strip().lower()
    return None


def _claim_name(provider: str) -> str:
    """The payload key that proves the directory, from the ONE table.

    ⚠️ **Read from :data:`operators.DIRECTORY_CLAIM`, never written here.**
    The two readers below hard-coded ``tid`` and ``hd`` until 2026-09-01, so
    the table held only KEYS that were live and VALUES that nothing consumed.
    A reviewer measured it: changing ``GOOGLE_PROVIDER: "hd"`` in that table
    to ``"email"`` left the whole suite green. A constant nothing reads is a
    comment that a future reader will trust as code.

    R7 — the fence is
    ``test_operator_identity.py::test_the_claim_table_is_what_the_readers_read``.
    """
    return operators.DIRECTORY_CLAIM[provider]


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
    name = _claim_name(AZURE_PROVIDER)
    for identity in payload.get("identities") or []:
        if not isinstance(identity, dict):
            continue
        if (identity.get("provider") or "").strip().lower() != AZURE_PROVIDER:
            continue
        data = identity.get("identity_data")
        if isinstance(data, dict):
            tid = data.get(name)
            if isinstance(tid, str) and tid.strip():
                return tid.strip()

    # Some Supabase configurations copy the provider claims up instead of
    # leaving them on the identity. Read those only when Microsoft is the
    # ONLY provider on the account, so the reasoning above still holds.
    if _providers(payload) == {AZURE_PROVIDER}:
        for bag in ("app_metadata", "user_metadata"):
            meta = payload.get(bag)
            if isinstance(meta, dict):
                tid = meta.get(name)
                if isinstance(tid, str) and tid.strip():
                    return tid.strip()
    return None


def _google_hd(payload: dict[str, Any]) -> str | None:
    """The Google Workspace hosted domain, read ONLY from a Google identity.

    ⚠️ **As strict as :func:`_azure_tid`, for the same reason.** A Supabase
    user can carry more than one linked identity. A scan across all of them
    would read an ``hd`` off a GitHub or a Microsoft identity, and that is a
    claim about a different account.

    ⚠️ **What this proves, stated exactly.** It proves *"this account holds an
    identity from our Workspace"*. It does NOT prove *"this sign-in came from
    our Workspace"*. Measured 2026-09-01: an account with two ``google``
    identities, where only the SECOND carries the ``hd``, is admitted. The
    provider filter cannot separate two identities that share one provider
    name, and :func:`_signin_provider` records why. No outsider can reach it,
    because the operator must link the second account themselves. H-54 asks
    the owner to turn identity linking OFF, and that closes it.

    ⚠️ **``hd`` is the whole of check 1 on this path** (**D70.3**). Google
    issues an account on any address it can verify by mail, and such an
    account carries ``email_verified: true`` and **no ``hd`` at all**. So an
    absent claim returns ``None`` here, and ``operators.directory_matches``
    reads ``None`` as a refusal rather than as a match.

    ⚠️ **Unmeasured, and deliberately fail-closed.** No live project has
    confirmed that Supabase copies ``hd`` into ``identity_data`` (H-54 item
    3). A wrong guess refuses everybody. It admits nobody.
    """
    name = _claim_name(GOOGLE_PROVIDER)
    for identity in payload.get("identities") or []:
        if not isinstance(identity, dict):
            continue
        if (identity.get("provider") or "").strip().lower() != GOOGLE_PROVIDER:
            continue
        data = identity.get("identity_data")
        if isinstance(data, dict):
            hd = data.get(name)
            if isinstance(hd, str) and hd.strip():
                return hd.strip()

    # Some Supabase configurations copy the provider claims up instead of
    # leaving them on the identity. Read those only when Google is the ONLY
    # provider on the account, so the reasoning above still holds.
    if _providers(payload) == {GOOGLE_PROVIDER}:
        for bag in ("app_metadata", "user_metadata"):
            meta = payload.get(bag)
            if isinstance(meta, dict):
                hd = meta.get(name)
                if isinstance(hd, str) and hd.strip():
                    return hd.strip()
    return None


#: Which reader answers check 1 for each provider. One table, so adding a
#: directory is one row rather than a branch somebody forgets.
_CLAIM_READERS = {
    AZURE_PROVIDER: _azure_tid,
    GOOGLE_PROVIDER: _google_hd,
}


def _email_is_verified(payload: dict[str, Any], provider: str) -> bool:
    """Whether the SIGN-IN provider says it proved the address.

    ⚠️ Defence in depth, not the main gate. ``operators._check_directory``
    already demands our directory claim, which no outsider can present. This
    stops the narrower case where a provider hands back an address it never
    proved, and somebody registers a colleague's address elsewhere.

    ⚠️ **It reads ONE identity — the sign-in provider's** (spec §8.1
    done-when 31). The built version accepted a top-level
    ``email_confirmed_at`` or ``confirmed_at``, and then scanned EVERY
    identity for ``email_verified``. So a second linked identity satisfied it,
    which is the same shape as the bypass :func:`_signin_provider` closed. A
    proof that the OTHER account's address was checked says nothing about
    this sign-in.

    🔴 **The PLACEMENT of ``email_verified`` is UNMEASURED, exactly as ``hd``
    is.** Nobody has read a real Supabase payload to confirm that the key sits
    in ``identities[].identity_data``. ⚠️ **If Supabase leaves it out when the
    value is false, this returns ``False`` and nobody signs in on EITHER
    path.** H-54 item 3 now asks the owner to read ``hd`` and
    ``email_verified`` off the SAME payload. It is one read.
    """
    for identity in payload.get("identities") or []:
        if not isinstance(identity, dict):
            continue
        if (identity.get("provider") or "").strip().lower() != provider:
            continue
        data = identity.get("identity_data")
        if isinstance(data, dict) and data.get("email_verified") is True:
            return True
    return False


def _reject(why: str) -> SigninRejected:
    _log.warning("operator.signin_refused", extra={"signin_why": why})
    return SigninRejected(_REFUSAL)


def extract_identity(
    payload: Any, *, env: dict[str, str] | None = None
) -> VerifiedIdentity:
    """Reduce Supabase's user payload to the identity `admit` needs.

    Raises :class:`SigninRejected` for anything it cannot read. ⚠️ Every branch
    here fails CLOSED, because this runs on a payload shape that no live
    project has confirmed yet (**H-54**).

    ⚠️ **The configured directory is read first**, and an unknown value raises
    ``operators.OperatorUnconfigured`` (a **503**). A box nobody configured and
    a person we refuse are different incidents.
    """
    # ⚠️ **Read FIRST, before the payload is even shaped.** This is the one
    # line that keeps "the box is wrong" (503) ahead of "the person is wrong"
    # (403). It raises on an unknown directory name, on an unknown admission
    # mode, and on an `OPERATOR_ALLOW_EMAIL_OTP` that contradicts the mode.
    accepted = operators.accepted_methods(env)

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
    #
    # ⚠️ **D71.3 widened this from ONE name to a SET, and the set is still
    # closed.** It holds the configured directory always, and ``email`` only
    # when the box is in ``registry`` mode with the flag on. A password or
    # magic-link sign-in still reaches here and is still refused, because
    # ``accepted_methods`` never names ``magiclink``, ``phone`` or ``sms``.
    used = _signin_provider(payload)
    if used is None or used not in accepted:
        raise _reject("provider")

    # ⚠️ **Verify against the method ACTUALLY used, never the configured
    # directory.** Passing `provider` here would read the Google identity's
    # `email_verified` for a sign-in that came through the email code, which
    # is the same class of bypass done-when 31 closed.
    if not _email_is_verified(payload, used):
        raise _reject("unverified")

    # An email code carries no directory claim, and there is no reader for one.
    # ``None`` is the honest answer, and `operators.admit` refuses it outright
    # in `directory` mode.
    reader = _CLAIM_READERS.get(used)

    return VerifiedIdentity(
        tid=reader(payload) if reader else None,
        email=email.strip().lower(),
        subject=subject.strip(),
        method=used,
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

    return extract_identity(payload, env=env)
