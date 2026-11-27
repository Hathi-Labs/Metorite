"""H-165 Slice B: a signed webhook run names no member.

The HMAC proves the sender holds the secret, not a person. So the run payload
loses ``user_email`` and ``user_id`` before the executor binds the member.
"""
from __future__ import annotations

import inspect

import pytest
from gateway.routes import agent as agent_routes


@pytest.mark.parametrize("key", ["user_email", "user_id"])
def test_a_sender_claim_of_a_member_is_removed(key: str) -> None:
    out = agent_routes._webhook_run_payload(
        "zoho", "deal.won", {key: "victim@example.com", "deal": 7},
    )
    assert key not in out
    assert out == {"source": "zoho", "event_type": "deal.won", "deal": 7}


def test_both_claims_go_and_other_keys_stay() -> None:
    out = agent_routes._webhook_run_payload(
        "s", "e", {"user_email": "a@b.co", "user_id": "x", "k": 1},
    )
    assert out == {"source": "s", "event_type": "e", "k": 1}


def test_the_helper_covers_every_identity_claim_key() -> None:
    payload = {k: "v" for k in agent_routes._IDENTITY_CLAIMS}
    out = agent_routes._webhook_run_payload("s", "e", payload)
    assert not set(agent_routes._IDENTITY_CLAIMS) & set(out)


def test_the_receiver_builds_its_run_payload_through_the_helper() -> None:
    src = inspect.getsource(agent_routes.receive_webhook)
    assert "_webhook_run_payload(" in src
    assert "**event.payload" not in src
