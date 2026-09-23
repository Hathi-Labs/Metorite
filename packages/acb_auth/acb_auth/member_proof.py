"""A member identity the capped party cannot choose. H-73.

Spec: ``specs/customer_console.md`` §4.5 / §6 CP-7 · **D32.8** ·
migration ``005_metering_identity.sql``.

🔴 **The cap engine is built and had to stay unwired, because the header it
would read is chosen by the person it caps.** The chain was measured, not
inferred:

1. ``auth.Caller.member`` binds from the ``X-CC-Member`` request header.
2. ``auth.py`` says so out loud — *"Attribution only. Never used to select
   rows, never used to authorise."*
3. ``v1_compat.py`` forwards it **verbatim from the inbound request**.
4. So omit the header and there is no cap row, and no cap row means
   unlimited. A present policy is bypassed by deleting one header.

⚠️ **This is migration 005's defect class, one column over.** 005 moved
``request_id`` server-side because *"the party being invoiced must not control
whether it exists."* The same sentence, two words changed: **the party being
capped must not control which cap applies.**

## What this is, and what it deliberately is not

It is a **signed claim on an existing hop**, not a fifth auth scheme. H-73
proposed "a session-scoped door beside the org key" and flagged that a new
scheme is `work_plan.md` §4's decision to own. This avoids that: nothing new
authenticates, and the credential set is unchanged. One header gains a
signature, and the signature is checked by the party that already decides.

**Who can mint.** Only a holder of ``gateway_session_secret``, which lives in
server environments. The Next server holds it AND holds the member's session,
so it is the one party that both knows who the human is and can prove it. An
ordinary member cannot mint, because the secret never reaches a browser.

**What it does NOT claim.** It says *this address was authenticated by a
server that holds the secret*. It does not say the person is entitled to
anything. Authorisation stays where it is.

⚠️ **Attribution is unchanged and still works without a proof.** A cost report
is not an authorisation decision, and H-73 records that distinction —
*"attribution is good enough to REPORT and not good enough to ENFORCE."* An
unsigned ``X-CC-Member`` keeps naming the caller in ``usage_event``. What it
stops doing is deciding a cap.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

__all__ = ["MEMBER_PROOF_HEADER", "PROOF_TTL_SECONDS", "sign_member", "verify_member"]

#: The header a proof travels in. Deliberately NOT ``X-CC-Member`` — the two
#: carry different claims and collapsing them would make an unsigned header
#: indistinguishable from a verified one at every reader.
MEMBER_PROOF_HEADER = "X-CC-Member-Proof"

#: ⚠️ **Short, because this is a bearer claim.** A proof that leaks is a proof
#: somebody else can spend under, so its value has to expire faster than an
#: investigation. Long enough for a slow completion to be issued and consumed,
#: which is what ``_ROUTER_TIMEOUT_SECONDS`` (120s) bounds.
PROOF_TTL_SECONDS = 300


def _digest(secret: str, msg: str) -> str:
    raw = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _normalise(email: str) -> str:
    """One spelling, so a proof cannot be made to miss its own cap.

    ⚠️ Addresses are compared case-insensitively everywhere else in this tree
    (``deployment_visible_orgs`` uses ``lower(...)``). Signing the raw string
    would let ``Dana@x.com`` and ``dana@x.com`` be two members with two caps,
    which is the bypass this module exists to close, reached by the shift key.
    """
    return email.strip().lower()


def sign_member(email: str, secret: str, *, now: float | None = None) -> str:
    """Mint a proof that ``email`` was authenticated by a holder of ``secret``.

    Raises:
        ValueError: the address or the secret is empty. Minting a proof for
            nobody, or under no secret, would produce a token that verifies
            against a default secret on a box that forgot to set one.
    """
    who = _normalise(email)
    if not who:
        raise ValueError("a member proof needs an address")
    if not secret.strip():
        raise ValueError("a member proof needs a signing secret")

    # ⚠️ The nonce makes two proofs for one member in one second differ. It
    # buys no security on its own — it stops a proof being a stable string
    # that reads like an identifier and gets cached as one.
    nonce = secrets.token_urlsafe(8)
    exp = int((time.time() if now is None else now) + PROOF_TTL_SECONDS)
    msg = f"{who}:{nonce}:{exp}"
    return f"{msg}:{_digest(secret, msg)}"


def verify_member(proof: str | None, secret: str, *, now: float | None = None) -> str | None:
    """The address this proof carries, or ``None``.

    ⚠️ **None for every failure, and the caller must not tell them apart.**
    Expired, forged, malformed and absent all answer the same, because a
    reader that branches on which one would hand an attacker a test rig for
    the signature.

    ⚠️ **An empty secret verifies NOTHING.** A box that forgot to configure one
    must refuse every proof rather than accept every proof, which is what a
    naive ``hmac`` over ``""`` would do.
    """
    if not proof or not secret.strip():
        return None

    # ⚠️ Split from the RIGHT with a bounded count. The address is the part
    # that can legitimately contain a colon, and splitting from the left cuts
    # inside it — the same trap `split_key` records for the underscore.
    parts = proof.rsplit(":", 3)
    if len(parts) != 4:
        return None
    who, nonce, exp_raw, supplied = parts

    try:
        exp = int(exp_raw)
    except ValueError:
        return None
    if (time.time() if now is None else now) > exp:
        return None
    if not who:
        return None

    expected = _digest(secret, f"{who}:{nonce}:{exp_raw}")
    if not hmac.compare_digest(supplied, expected):
        return None
    return who
