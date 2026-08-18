"""Per-organization API keys — minting, splitting, verification.

Spec: ``project-docs/specs/customer_console.md`` §4.3.

The parser case is the one worth having: ``secrets.token_urlsafe`` emits ``_``
as part of its alphabet, so a left-split would truncate the secret and fail
verification for a random fraction of minted keys — which presents as flaky
authentication rather than as a parser bug, and is therefore expensive to chase.
"""
from __future__ import annotations

from customer_console.keys import (
    hash_secret,
    mint_key,
    split_key,
    verify_secret,
)


class TestMinting:
    def test_a_minted_key_round_trips(self):
        k = mint_key()
        parsed = split_key(k.token)

        assert parsed is not None
        prefix, secret = parsed
        assert prefix == k.prefix
        assert verify_secret(secret, k.key_hash) is True

    def test_the_secret_is_never_the_stored_hash(self):
        k = mint_key()
        assert k.key_hash not in k.token

    def test_keys_are_unique(self):
        assert len({mint_key().prefix for _ in range(200)}) == 200

    def test_the_prefix_is_showable_and_namespaced(self):
        assert mint_key().prefix.startswith("cc_live_")

    def test_environments_are_distinguishable(self):
        assert mint_key(env="test").prefix.startswith("cc_test_")


class TestSplitting:
    def test_the_secret_keeps_its_underscores(self):
        # Constructed to be exactly the case an rpartition-based split gets
        # wrong: the prefix is a fixed three segments, the secret is whatever
        # token_urlsafe produced — underscores included.
        prefix, secret = split_key("cc_live_abc123_sec_ret_with_underscores")

        assert prefix == "cc_live_abc123"
        assert secret == "sec_ret_with_underscores"

    def test_a_real_minted_key_with_underscores_in_its_secret_verifies(self):
        # The property that actually matters, over many samples so a secret
        # containing '_' is certain to be exercised.
        for _ in range(200):
            k = mint_key()
            prefix, secret = split_key(k.token)
            assert prefix == k.prefix
            assert verify_secret(secret, k.key_hash) is True

    def test_rejects_malformed_tokens(self):
        for bad in [
            "",
            "nonsense",
            "cc_live",           # no secret
            "cc_live_",          # empty secret
            "bearer cc_live_a_b",  # wrong scheme
            "cc__abc_secret",    # empty env segment
        ]:
            assert split_key(bad) is None, bad


class TestVerification:
    def test_a_wrong_secret_fails(self):
        k = mint_key()
        assert verify_secret("not-the-secret", k.key_hash) is False

    def test_hashing_is_deterministic(self):
        assert hash_secret("abc") == hash_secret("abc")

    def test_different_secrets_hash_differently(self):
        assert hash_secret("abc") != hash_secret("abd")
