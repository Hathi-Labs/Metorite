"""Tests for AssemblyAI model discovery in Settings → Models.

AssemblyAI publishes no model-list endpoint, so "Refresh" can't enumerate models
the way it does for OpenAI-compatible providers. What it can do is prove the key
works — and it must NOT imply a bad key is fine. These pin that behaviour.
"""
from __future__ import annotations

import pytest
from gateway.routes import settings as st


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


def _patch_get(monkeypatch, resp) -> None:
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            if isinstance(resp, Exception):
                raise resp
            return resp

    monkeypatch.setattr(st.httpx, "AsyncClient", lambda *a, **k: _Client())


def test_assemblyai_is_catalogued_with_current_model_ids() -> None:
    """The ids must be ones AssemblyAI actually accepts — a stale id fails every
    transcription with a confusing provider error."""
    ids = st._PROVIDER_MODELS["assemblyai"]
    assert "assemblyai/universal-3-pro" in ids
    assert "assemblyai/universal-2" in ids


def test_assemblyai_models_are_flagged_as_transcription() -> None:
    """Otherwise they'd be offered for chat tiers and hidden from the STT tier."""
    for mid in st._PROVIDER_MODELS["assemblyai"]:
        assert st._is_transcription_model(mid)
        assert st._model_info_from_caps(mid, "assemblyai").transcription is True


@pytest.mark.asyncio
async def test_a_working_key_returns_the_curated_list(monkeypatch) -> None:
    _patch_get(monkeypatch, _Resp(200))
    models = await st._fetch_live_models("assemblyai", api_key="k")
    assert [m.id for m in models] == st._PROVIDER_MODELS["assemblyai"]


@pytest.mark.asyncio
async def test_a_rejected_key_returns_nothing(monkeypatch) -> None:
    """Returning the catalogue on a 401 would tell the user their bad key is
    fine — the failure has to be visible."""
    _patch_get(monkeypatch, _Resp(401))
    assert await st._fetch_live_models("assemblyai", api_key="bad") == []


@pytest.mark.asyncio
async def test_a_transient_error_still_shows_the_models(monkeypatch) -> None:
    """A rate limit shouldn't blank the picker — the key may well be fine."""
    _patch_get(monkeypatch, _Resp(429))
    models = await st._fetch_live_models("assemblyai", api_key="k")
    assert len(models) == len(st._PROVIDER_MODELS["assemblyai"])


@pytest.mark.asyncio
async def test_network_failure_is_survivable(monkeypatch) -> None:
    _patch_get(monkeypatch, RuntimeError("dns"))
    assert await st._fetch_live_models("assemblyai", api_key="k") == []


@pytest.mark.asyncio
async def test_no_key_means_no_probe(monkeypatch) -> None:
    assert await st._fetch_live_models("assemblyai", api_key=None) == []


# ── provider registration must be COMPLETE ───────────────────────────────────

def test_assemblyai_models_resolve_to_their_provider() -> None:
    """A missing branch here made the STT tier render "? unknown" and "No key"
    even with a valid key configured — _is_provider_configured("unknown") can't
    find an env var, so a working key looked missing."""
    assert st._provider_from_model("assemblyai/universal-3-pro") == "assemblyai"
    assert st._provider_from_model("assemblyai/universal-2") == "assemblyai"


def test_every_catalogued_stt_provider_is_registered_everywhere() -> None:
    """Registering a provider means touching several maps; missing one fails
    silently in the UI rather than raising. Check them together."""
    for provider in ("assemblyai", "deepgram"):
        assert provider in st._PROVIDER_ENV_MAP, "no env var mapping"
        assert provider in st._PROVIDER_LABELS, "no display label"
        assert provider in st._PROVIDER_MODELS, "no catalogued models"
        for mid in st._PROVIDER_MODELS[provider]:
            assert st._provider_from_model(mid) == provider, f"{mid} misroutes"


def test_a_configured_key_is_reported_as_configured(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.setattr(st, "_env_file_path", lambda: env_file)
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "sk-test")
    assert st._is_provider_configured("assemblyai") is True
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    monkeypatch.setattr(st, "get_settings", lambda: type("S", (), {})())
    assert st._is_provider_configured("assemblyai") is False
    # Task 625 (D58.2): a save writes the env file and no longer os.environ,
    # so a saved key in the file still reads as configured before a restart.
    env_file.write_text("ASSEMBLYAI_API_KEY=sk-test\n", encoding="utf-8")
    assert st._is_provider_configured("assemblyai") is True
    env_file.write_text("ASSEMBLYAI_API_KEY=\n", encoding="utf-8")
    assert st._is_provider_configured("assemblyai") is False
