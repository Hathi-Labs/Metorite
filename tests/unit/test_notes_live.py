"""Tests for the live-transcription token endpoint — the pure, testable parts:
provider selection, model resolution, and the config guard. Minting a real
token needs a real key and isn't exercised here.

The key of each token comes from the key store seam
(``acb_llm.key_store.caller_provider_key``, D58.2). Its two-org proof lives
in ``test_llm_call_credentials.py``. Here an autouse stub answers no key, so
no test opens a database, and each mint raises, so none reaches a real
speech-to-text endpoint.
"""
from __future__ import annotations

import pytest
from gateway.routes.notes import live


@pytest.fixture(autouse=True)
def _no_store_and_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key store, and no outbound call (task 625, verifier F7)."""
    import acb_llm.key_store as key_store

    async def no_key(_provider: str) -> str:
        return ""

    async def no_mint(_api_key: str) -> str:
        raise AssertionError("a test reached a real speech-to-text mint")

    monkeypatch.setattr(key_store, "caller_provider_key", no_key)
    monkeypatch.setattr(live, "_mint_assemblyai_token", no_mint)
    monkeypatch.setattr(live, "_mint_ephemeral_key", no_mint)


# ── provider selection ───────────────────────────────────────────────────────

def test_live_follows_the_configured_stt_provider() -> None:
    """Choosing an AssemblyAI model for batch gets AssemblyAI live too — the
    whole point of "everything via AssemblyAI" needing no second setting."""
    assert live.choose_provider("assemblyai/universal", True, True) == "assemblyai"
    assert live.choose_provider("deepgram/nova-3", True, True) == "deepgram"


def test_prefers_assemblyai_when_the_tier_cant_stream() -> None:
    """Whisper can't stream, so the batch tier gives no signal — prefer the
    cheaper provider that also diarizes live."""
    assert live.choose_provider("groq/whisper-large-v3-turbo", True, True) == "assemblyai"


def test_falls_back_to_whichever_key_exists() -> None:
    assert live.choose_provider("assemblyai/universal", False, True) == "deepgram"
    assert live.choose_provider("deepgram/nova-3", True, False) == "assemblyai"
    assert live.choose_provider("", False, True) == "deepgram"
    assert live.choose_provider("", True, False) == "assemblyai"


def test_no_keys_means_no_live() -> None:
    assert live.choose_provider("assemblyai/universal", False, False) is None


# ── model resolution ─────────────────────────────────────────────────────────

def test_deepgram_live_model_defaults_to_nova3(monkeypatch) -> None:
    # tier-stt resolves to a whisper model → live falls back to nova-3 (whisper
    # can't stream), so the Deepgram path always has a usable model.
    monkeypatch.setattr(
        "acb_llm.context.resolve_underlying_model",
        lambda _alias: "groq/whisper-large-v3-turbo",
    )
    assert live._live_model("deepgram") == "nova-3"


def test_deepgram_live_model_uses_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "acb_llm.context.resolve_underlying_model",
        lambda _alias: "deepgram/nova-2",
    )
    assert live._live_model("deepgram") == "nova-2"


def test_assemblyai_live_model_is_pinned_not_left_to_the_provider(monkeypatch) -> None:
    """This used to send nothing and let AssemblyAI pick. That silently cost us
    live speaker diarization, which only some streaming models support — so the
    model is now pinned, and still overridable per deployment."""
    monkeypatch.delenv("ASSEMBLYAI_LIVE_MODEL", raising=False)
    assert live._live_model("assemblyai") == "u3-rt-pro"
    monkeypatch.setenv("ASSEMBLYAI_LIVE_MODEL", "universal-streaming-english")
    assert live._live_model("assemblyai") == "universal-streaming-english"


# ── guard ────────────────────────────────────────────────────────────────────

async def test_live_token_503_without_any_key(monkeypatch) -> None:
    """No streaming key at all → 503, so the recorder falls back to batch."""
    from fastapi import HTTPException

    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    # No meeting_id, so nothing is owner-scoped and the identity is unused —
    # the scoping added for N1 only engages when a meeting is named.
    with pytest.raises(HTTPException) as ei:
        await live.live_token(user=None)  # type: ignore[arg-type]
    assert ei.value.status_code == 503
    assert "AssemblyAI" in ei.value.detail


# ── the worker's own token endpoint ──────────────────────────────────────────

async def test_bot_live_token_requires_the_shared_bot_token(monkeypatch) -> None:
    """The worker runs in its own container and can't hold a user session, so it
    authenticates with the shared bot token. This endpoint mints real provider
    credentials — an unauthenticated caller must not reach it."""
    from fastapi import HTTPException

    monkeypatch.setenv("MEETING_BOT_TOKEN", "s3cret")
    for bad in (None, "", "Bearer wrong", "s3cret"):
        with pytest.raises(HTTPException) as ei:
            await live.bot_live_token(authorization=bad)
        assert ei.value.status_code == 401


async def test_bot_live_token_shares_the_browser_path(monkeypatch) -> None:
    """Same credentials, same provider choice — live captions must not depend on
    which producer asked, or a bot and a browser would transcribe differently."""
    from fastapi import HTTPException

    monkeypatch.setenv("MEETING_BOT_TOKEN", "s3cret")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    # Authorised, but no key configured → the same 503 the browser gets.
    with pytest.raises(HTTPException) as ei:
        await live.bot_live_token(authorization="Bearer s3cret")
    assert ei.value.status_code == 503


async def test_bot_live_token_open_when_no_shared_secret_is_set(monkeypatch) -> None:
    """Self-hosted LAN default (matches the live-segment callback's rule)."""
    from fastapi import HTTPException

    monkeypatch.delenv("MEETING_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    with pytest.raises(HTTPException) as ei:
        await live.bot_live_token(authorization=None)
    assert ei.value.status_code == 503  # reached the provider check, not 401


# ── streaming model family ───────────────────────────────────────────────────

def test_streaming_defaults_to_the_model_that_diarizes(monkeypatch) -> None:
    """Live diarization needs Universal-3 Pro Streaming. On any other streaming
    model you get a working transcript with every voice merged into one speaker
    — a silent wrong answer, not an error — so the default has to be the one
    that works."""
    monkeypatch.delenv("ASSEMBLYAI_LIVE_MODEL", raising=False)
    assert live._live_model("assemblyai") == "u3-rt-pro"


def test_streaming_model_is_overridable(monkeypatch) -> None:
    monkeypatch.setenv("ASSEMBLYAI_LIVE_MODEL", "universal-streaming-multilingual")
    assert live._live_model("assemblyai") == "universal-streaming-multilingual"


def test_batch_tier_choice_never_leaks_into_streaming(monkeypatch) -> None:
    """AssemblyAI's streaming models are a separate family — a batch id like
    "universal-2" is not a valid streaming model, which is why picking it in
    Settings -> Models never affected live transcription."""
    monkeypatch.delenv("ASSEMBLYAI_LIVE_MODEL", raising=False)
    monkeypatch.setattr(live, "_configured_stt_model", lambda: "assemblyai/universal-2")
    assert live._live_model("assemblyai") == "u3-rt-pro"
