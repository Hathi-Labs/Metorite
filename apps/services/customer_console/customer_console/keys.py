"""Per-organization API keys — the thing that resolves the tenant.

Spec: ``project-docs/specs/customer_console.md`` §4.3 · §3.2's first
addition ("the load-bearing change").

**The key resolves the tenant, and nothing else may.** Attribution headers
(``X-CC-Member``, ``X-CC-Agent``, ``X-CC-Module``) refine *within* the
organization the key already pinned; they can never move a call to a different
organization. That is the whole security property, and it is why the org is
never read from request input (``user_management_contract.md`` R11).

Format: ``cc_live_<prefix>_<secret>``

* ``prefix`` is stored in the clear, indexed, and safe to show or log. It is
  what makes lookup a single indexed read instead of a scan-and-compare against
  every hash in the table.
* ``secret`` is never stored. Only its hash is, so a database disclosure does
  not hand over working keys.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

__all__ = [
    "ENV_DEPLOYMENT",
    "ENV_DISCOUNT",
    "MintedKey",
    "hash_secret",
    "is_deployment_key",
    "is_discount_code",
    "mint_key",
    "split_key",
    "verify_secret",
]

_ENV_LIVE = "live"
#: The deployment scheme's env segment — ``cc_depl_<prefix>_<secret>`` (CP-2b).
#: A named constant rather than a literal in three files: the string appears in
#: the minting call, in the dispatch that decides which scheme is at the door,
#: and in the tests, and a typo in any one of them is a silent auth change.
ENV_DEPLOYMENT = "depl"
#: The discount code's env segment — ``cc_disc_<prefix>_<secret>`` (SC-4g (i)).
#:
#: A third **value**, not a third implementation: a discount code is a bearer
#: secret that grants value, so it is stored and verified by exactly this
#: module. The spec first wrote the format as ``disc_<prefix>_<secret>``, which
#: :func:`split_key` **rejects** — it requires the first segment to be exactly
#: ``cc`` — so building it would have meant editing the shared key seam, i.e.
#: doing the opposite of what "a value, not an implementation" asks.
#:
#: ⚠️ A discount code is NOT a credential: it opens no route and authenticates
#: nobody. It is presented *inside* a request already authenticated by an
#: organization key, and what it proves is pre-authorization by an operator.
ENV_DISCOUNT = "disc"
_PREFIX_BYTES = 6   # 12 hex chars — enough to be unique, short enough to show
_SECRET_BYTES = 32  # 256 bits


@dataclass(frozen=True)
class MintedKey:
    """A freshly minted key. ``token`` is the ONLY time the secret exists."""

    prefix: str
    key_hash: str
    #: The full key to hand the caller. Shown once and never recoverable —
    #: storing it anywhere, including a log line or an error message, defeats
    #: the point of hashing.
    token: str


def _canonical_prefix(env: str, prefix: str) -> str:
    return f"cc_{env}_{prefix}"


def mint_key(*, env: str = _ENV_LIVE) -> MintedKey:
    """Mint a new organization key."""
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    stored_prefix = _canonical_prefix(env, prefix)
    return MintedKey(
        prefix=stored_prefix,
        key_hash=hash_secret(secret),
        token=f"{stored_prefix}_{secret}",
    )


def _names_env(stored_prefix: str, env: str) -> bool:
    """True when a canonical prefix carries this env segment.

    Built from :func:`_canonical_prefix` rather than from a literal
    ``"cc_depl_"``, so the key format lives in exactly one place: change the
    separator or the scheme word and every predicate below follows, instead of
    silently answering False for every key and routing deployments into the
    operator arm — which fails as *"invalid operator token"*, a message that
    sends the reader to the wrong half of the system.
    """
    return stored_prefix.startswith(_canonical_prefix(env, ""))


def is_deployment_key(stored_prefix: str) -> bool:
    """True when a canonical prefix names the **deployment** scheme (CP-2b)."""
    return _names_env(stored_prefix, ENV_DEPLOYMENT)


def is_discount_code(stored_prefix: str) -> bool:
    """True when a canonical prefix names a **discount code** (SC-4g (i)).

    A sibling of :func:`is_deployment_key` rather than a generalisation with a
    parameter at the call site: the two answer different questions to different
    readers, and the shared half is :func:`_names_env`.
    """
    return _names_env(stored_prefix, ENV_DISCOUNT)


def hash_secret(secret: str) -> str:
    """Hash a key secret for storage.

    SHA-256 rather than a password KDF (bcrypt/argon2), deliberately: this is a
    **high-entropy machine-generated 256-bit secret**, not a human password. A
    KDF's cost factor buys resistance to guessing a low-entropy input, which is
    not the threat here — and it would be paid on *every LLM call*, on the hot
    path, for no security gain.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret: str, expected_hash: str) -> bool:
    """Constant-time check of a presented secret against the stored hash.

    ``compare_digest`` because a plain ``==`` on a hash leaks its prefix through
    timing, and a leaked hash prefix narrows an offline search. Cheap to do
    right; awkward to notice when done wrong.
    """
    return hmac.compare_digest(hash_secret(secret), expected_hash)


def split_key(token: str) -> tuple[str, str] | None:
    """Split ``cc_<env>_<prefix>_<secret>`` into ``(stored_prefix, secret)``.

    Returns ``None`` for anything malformed, so a caller cannot accidentally
    treat a garbage string as a lookup key.

    **Split from the LEFT with a bounded count, never from the right.** The two
    halves have different shapes: the stored prefix is always exactly three
    underscore-separated segments (``cc``, env, hex prefix), while the secret is
    ``secrets.token_urlsafe`` output, whose alphabet *includes* ``_`` — so it
    contains an unpredictable number of them. Splitting on the last underscore
    therefore cuts somewhere inside the secret for any key whose secret happens
    to contain one, which is most of them.

    That is not hypothetical: this function was written with ``rpartition``
    first, and ``test_a_real_minted_key_with_underscores_in_its_secret_verifies``
    failed on it immediately. The bug presents as *flaky authentication* — a
    fraction of freshly issued keys simply never work — which is far more
    expensive to chase than a parse error.
    """
    if not token:
        return None
    # Four fields: 'cc', env, prefix, and the whole remaining secret.
    parts = token.split("_", 3)
    if len(parts) != 4 or not all(parts):
        return None
    scheme, env, prefix, secret = parts
    if scheme != "cc":
        return None
    return _canonical_prefix(env, prefix), secret
