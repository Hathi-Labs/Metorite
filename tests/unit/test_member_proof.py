"""The capped party cannot choose which cap applies. H-73.

Spec: `specs/customer_console.md` §4.5 / §6 CP-7 · **D32.8**.

🔴 **The cap engine is built and had to stay unwired**, because
`Caller.member` binds from a header the capped person sends. Omit it and there
is no cap row, and no cap row means unlimited — a present policy bypassed by
deleting one header. This module is what makes the claim unforgeable.

⚠️ Hermetic on purpose. Every subject here is an HMAC, a clock or a string
split. There is no SQL, so R8 has nothing to add.
"""
from __future__ import annotations

import pytest

from acb_auth.member_proof import (
    MEMBER_PROOF_HEADER,
    PROOF_TTL_SECONDS,
    sign_member,
    verify_member,
)

SECRET = "a-server-side-signing-secret"
OTHER = "a-different-secret"
WHO = "dana@example.com"


class TestAProofRoundTrips:
    def test_a_minted_proof_verifies_to_its_member(self):
        assert verify_member(sign_member(WHO, SECRET), SECRET) == WHO

    def test_an_address_with_a_COLON_survives(self):
        """⚠️ The address is the part that can legitimately hold a colon, so
        the split is from the RIGHT. Splitting from the left cuts inside it —
        the trap `split_key` records for the underscore, one character over."""
        odd = "weird:name@example.com"
        assert verify_member(sign_member(odd, SECRET), SECRET) == odd

    def test_two_proofs_for_one_member_DIFFER(self):
        """The nonce. A proof that is a stable string reads like an identifier
        and gets cached as one."""
        assert sign_member(WHO, SECRET) != sign_member(WHO, SECRET)

    def test_case_is_normalised_so_a_cap_cannot_be_DODGED_by_the_shift_key(self):
        """🔴 Addresses compare case-insensitively everywhere else in this tree.

        Signing the raw string would make `Dana@x` and `dana@x` two members
        with two caps — the exact bypass this module closes, reached without
        any forgery at all.
        """
        assert verify_member(sign_member("DANA@Example.COM", SECRET), SECRET) == WHO
        assert verify_member(sign_member("  dana@example.com  ", SECRET), SECRET) == WHO


class TestWhatItRefuses:
    def test_a_proof_signed_with_ANOTHER_secret_is_refused(self):
        """The whole point: a party without the secret cannot mint."""
        assert verify_member(sign_member(WHO, OTHER), SECRET) is None

    def test_a_TAMPERED_member_is_refused(self):
        """🔴 Swapping the address for a colleague's is the attack. Spending
        somebody else's allocation must not be a string edit."""
        proof = sign_member(WHO, SECRET)
        forged = proof.replace(WHO, "victim@example.com")
        assert forged != proof
        assert verify_member(forged, SECRET) is None

    def test_an_EXPIRED_proof_is_refused(self):
        """A bearer claim that outlives an investigation is one somebody else
        can spend under."""
        old = sign_member(WHO, SECRET, now=1_000_000)
        assert verify_member(old, SECRET, now=1_000_000 + 10) == WHO
        assert verify_member(old, SECRET, now=1_000_000 + PROOF_TTL_SECONDS + 1) is None

    def test_a_pushed_out_EXPIRY_is_refused(self):
        """The expiry is inside the signed message, not beside it."""
        proof = sign_member(WHO, SECRET, now=1_000)
        who, nonce, exp, sig = proof.rsplit(":", 3)
        assert verify_member(f"{who}:{nonce}:{int(exp) + 99999}:{sig}", SECRET) is None

    @pytest.mark.parametrize(
        "junk",
        [None, "", "   ", "no-colons-at-all", "a:b:c", "a:b:c:d:e",
         "dana@example.com:n:notanumber:sig"],
    )
    def test_malformed_input_answers_None_and_never_RAISES(self, junk):
        """⚠️ This runs on the serving path. An exception here turns a
        completion into a 500, which is a worse answer than an uncapped one."""
        assert verify_member(junk, SECRET) is None

    def test_an_EMPTY_secret_verifies_nothing(self):
        """🔴 A box that forgot to configure one must refuse every proof.

        A naive `hmac` over `""` would ACCEPT every proof instead, which turns
        a missing setting into a silent total bypass — the fail-open shape CP-0
        exists to remove.
        """
        proof = sign_member(WHO, SECRET)
        assert verify_member(proof, "") is None
        assert verify_member(proof, "   ") is None

    def test_minting_refuses_an_empty_address_or_secret(self):
        """Loudly, because both produce a token that looks real."""
        for bad in ("", "   "):
            with pytest.raises(ValueError):
                sign_member(bad, SECRET)
            with pytest.raises(ValueError):
                sign_member(WHO, bad)


def test_the_header_is_NOT_x_cc_member():
    """⚠️ Two different claims, so two different headers.

    Collapsing them would make an unsigned attribution header
    indistinguishable from a verified identity at every reader — and every
    reader would then have to remember which one it was holding.
    """
    assert MEMBER_PROOF_HEADER.lower() != "x-cc-member"
    assert MEMBER_PROOF_HEADER == "X-CC-Member-Proof"


# ── The gateway hop: where the header used to be trusted ────────────────────


class TestTheGatewayPrefersTheProof:
    """🔴 `v1_compat` forwarded `x-cc-member` verbatim from the inbound
    request. That one line is why the cap engine is built and unwired."""

    @staticmethod
    def _request(headers: dict[str, str]):
        from starlette.datastructures import Headers

        class _Req:
            def __init__(self, h):
                self.headers = Headers(h)

        return _Req(headers)

    def _member_for(self, monkeypatch, headers, secret=SECRET):
        from acb_common.settings import get_settings
        from gateway.routes import v1_compat

        monkeypatch.setenv("GATEWAY_SESSION_SECRET", secret)
        get_settings.cache_clear()
        try:
            return v1_compat._member_for(self._request(headers))
        finally:
            get_settings.cache_clear()

    def test_the_proof_header_is_read_case_INSENSITIVELY(self, monkeypatch):
        """🔴 Caught by `test_v1_router_serving`, and it was a real defect.

        The helper looked the header up by its mixed-case constant. A real
        Starlette `Headers` is case-insensitive so that worked in production,
        and it MISSED on a plain-dict request — failing open to "unproven" and
        silently ignoring a valid proof. Both spellings must resolve.
        """
        proof = sign_member(WHO, SECRET)
        for spelling in (MEMBER_PROOF_HEADER, MEMBER_PROOF_HEADER.lower()):
            who, proven = self._member_for(monkeypatch, {spelling: proof})
            assert (who, proven) == (WHO, True), spelling

    def test_a_proof_is_trusted_and_marked_proven(self, monkeypatch):
        who, proven = self._member_for(
            monkeypatch, {MEMBER_PROOF_HEADER: sign_member(WHO, SECRET)}
        )
        assert (who, proven) == (WHO, True)

    def test_a_forged_header_cannot_BEAT_a_proof(self, monkeypatch):
        """The attack, stated plainly: claim to be somebody else and spend
        their allocation. The signature has to win or it is decorative."""
        who, proven = self._member_for(
            monkeypatch,
            {
                MEMBER_PROOF_HEADER: sign_member(WHO, SECRET),
                "x-cc-member": "victim@example.com",
            },
        )
        assert who == WHO
        assert proven is True

    def test_a_bare_header_still_ATTRIBUTES_but_is_not_proven(self, monkeypatch):
        """⚠️ H-73's own distinction, and what makes this safe to land in one
        step: *attribution is good enough to REPORT and not good enough to
        ENFORCE*. Blanking it would break every existing per-member report."""
        who, proven = self._member_for(monkeypatch, {"x-cc-member": WHO})
        assert who == WHO
        assert proven is False

    def test_an_INVALID_proof_falls_back_to_attribution_and_is_not_proven(
        self, monkeypatch
    ):
        """A proof minted under another secret must not authorise, and must
        not blank the report either."""
        who, proven = self._member_for(
            monkeypatch,
            {MEMBER_PROOF_HEADER: sign_member(WHO, OTHER), "x-cc-member": WHO},
        )
        assert who == WHO
        assert proven is False

    def test_no_headers_at_all_is_no_member(self, monkeypatch):
        assert self._member_for(monkeypatch, {}) == (None, False)
