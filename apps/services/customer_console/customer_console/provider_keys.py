"""Installing OUR provider credentials — the admission rules, and nothing else.

Spec: ``project-docs/specs/customer_console.md`` **CP-10 slice 1** · §3.4 ·
§6A. Board: WS-31. Handoff: **H-40**.

⚠️ **This unblocks the AI product.** ``router.provider_credential()`` reads a
table that no migration seeds, no route writes and no script populates. On a
fresh Console database it returns ``None``, so there is no way to put our
DeepSeek, Anthropic or Groq key in at all. Metering being unpriced is a
separate and later problem. This one stops the first call.

**Policy only. No SQL, and no crypto.** ``store.py`` owns the statements and
``router.encrypt_secret`` owns the Fernet seam — the one that already exists.
Building a second encryption path here would be exactly the "second
implementation of an existing seam" CLAUDE.md calls a defect.

⚠️ **Read ``infra/customer_console/004_provider_keys.sql`` before changing
this.** Its header argues why this store is NOT ``acb_llm.key_store``: that one
reads the TENANT database, and putting our provider accounts on a customer's
box is the precise thing D32.1 moved metering here to avoid.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

__all__ = [
    "MIN_SECRET_CHARS",
    "CredentialRefused",
    "check_api_base",
    "check_provider",
    "check_secret",
    "normalise_provider",
]

#: A floor, not a format. Provider key formats differ and change, so validating
#: a shape would refuse a valid key the day a vendor changes theirs. What this
#: catches is the real mistake: an empty box, a placeholder, or half a key
#: pasted from a terminal that wrapped it.
MIN_SECRET_CHARS = 16

#: Deliberately NOT a closed list. 004's own comment reads "'deepseek',
#: 'anthropic', 'groq', …", and litellm reaches dozens more. An allowlist here
#: would mean a code change and a deploy every time we signed up to a vendor,
#: which is exactly the hand-run-SQL problem this slice exists to end.
_PROVIDER_SHAPE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,39}$")


class CredentialRefused(Exception):
    """The credential cannot be installed. Callers map this to **400**.

    ⚠️ Unlike an auth refusal, this one SAYS WHY. The caller is an admin who
    has already passed the role matrix and an elevation window, and the thing
    they got wrong is their own input. A single opaque message here would only
    make them guess at a form they are entitled to complete.
    """


def normalise_provider(name: str | None) -> str:
    """Lower-cased and trimmed, so ``Anthropic`` and ``anthropic`` are one row.

    ⚠️ Load-bearing for the partial unique index. It is over the literal
    ``provider`` column, so two spellings would both be "the one live
    credential" and ``provider_credential()`` would return whichever the sort
    happened to reach.
    """
    return (name or "").strip().lower()


def check_provider(name: str | None) -> str:
    """Return the normalised provider name, or raise."""
    provider = normalise_provider(name)
    if not provider:
        raise CredentialRefused("provider is required")
    if not _PROVIDER_SHAPE.match(provider):
        raise CredentialRefused(
            "provider must be 2 to 40 characters of a-z, 0-9, dot, dash or "
            "underscore, and start with a letter or digit"
        )
    return provider


def check_secret(secret: str | None) -> str:
    """Return the secret unchanged, or raise.

    ⚠️ **Returned unchanged, and never normalised.** Trimming looks harmless
    and is not: a provider key is an opaque string, and silently altering one
    produces a credential that fails at the provider with an authentication
    error nobody can trace back to this line. Leading or trailing whitespace is
    REFUSED instead, so the operator fixes their paste.
    """
    if secret is None or secret == "":
        raise CredentialRefused("secret is required")
    if secret != secret.strip():
        raise CredentialRefused(
            "the secret has leading or trailing whitespace — paste it again "
            "rather than let this store a key the provider will reject"
        )
    if len(secret) < MIN_SECRET_CHARS:
        raise CredentialRefused(
            f"the secret is shorter than {MIN_SECRET_CHARS} characters, which "
            "is almost always a placeholder or a truncated paste"
        )
    if any(ch.isspace() for ch in secret):
        raise CredentialRefused("the secret contains whitespace")
    return secret


def check_api_base(api_base: str | None) -> str | None:
    """An absolute http(s) URL, or ``None``.

    ⚠️ **This value decides where our provider key is SENT**, so it is worth
    knowing why it is not a bigger hole than it looks.

    An admin who could re-point an existing credential's ``api_base`` at a host
    they control would receive our provider key on the next call — and read a
    plaintext secret that **no route ever returns**. That escalation is closed
    by a rule taken for a different reason: the spec forbids an UPDATE path on
    this table, so ``api_base`` can only be set by the same request that
    supplies the secret. You cannot repoint a key you did not install.

    ``test_provider_keys.py`` pins the no-UPDATE property, because it is now
    load-bearing for two things rather than one.
    """
    if api_base is None:
        return None
    value = api_base.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CredentialRefused(
            "api_base must be an absolute http or https URL, or be omitted"
        )
    return value
