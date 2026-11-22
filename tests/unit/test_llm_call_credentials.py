"""Each model call carries its own organization's key (D58.2, task 625).

Spec: ``project-docs/work_plan.md`` §3 D58.1 and D58.2,
``project-docs/specs/customer_console.md`` §4 and §6B.

The defect (H-43). ``acb_llm.client._ensure_keys_loaded`` was a
once-per-process latch. It called ``key_store.configure_litellm()``, which
copied the stored provider keys into ``litellm.<provider>_api_key`` and into
``os.environ``. Every later call in the process then sent THOSE keys, for
whatever organization it served. The Router passes ``api_key`` per call, with
no shared state. This file holds the direct arm to that shape.

How the leak happened with real entry points:

1. The gateway starts while one organization exists. The latch loads that
   organization's key into the process globals.
2. A second organization signs up. The latch never runs again.
3. A call for the second organization sends the first organization's key.

The Settings save door (``POST /settings/llm/key``, BYOK only) wrote
``os.environ`` and the ONE env file of the box. systemd loads that file into
every process at the next start, so a saved key reached every organization.

The rule now (``key_store.ProviderKeyStore.provider_key``):

* **BYOK off** (``settings.byok_enabled``, the default): every direct call
  sends the operator's platform env key, and no row is read. That is what
  production did before task 625.
* **BYOK on**: the stored key of the bound organization wins, else the
  platform key. With no bound organization, the sole organization's key, or
  none once a second one exists. Never another organization's row.
* **The save** writes the store of the caller's organization, and awaits it.
  It never writes ``os.environ`` or the env file.

What this file holds (R7):

* ``TestByokOffIsTodaysBehaviour``: each direct site sends the platform key
  with BYOK off, also when the org holds a row, and reads no row.
* ``TestEveryDirectSiteCarriesItsOwnKey``: with BYOK on, each direct site
  sends org A's key, then org B's, then A's, in one process. The platform
  env key is set to a third value, so an env fallback fails.
* ``TestTheUntenantedSites``: the ``/v1`` door and the prewarm bind no
  tenant. One org gives the sole org's key, two give the platform key.
* ``TestTheSaveDoor``: the save writes only the caller's store, awaits it,
  answers 503 when it does not land, and refuses ``github`` and ``vllm``.
* ``TestNoProcessGlobalProviderKeyWrite`` and ``TestEveryLitellmCallNamesItsKey``:
  source fences over ``apps/``, ``packages/`` and ``scripts/``, aware of
  import aliases. A self-test holds each shape.

Mutations this file catches (each one red):

* m1. Restore ``os.environ[env_var] = key`` in ``key_store.py``.
* m2. Drop the per-call ``api_key`` from ``client.complete``.
* m3. Read the stored row with BYOK off (drop the ``byok_reads_store`` gate).
* m4. Restore the env file write in ``set_provider_key``.
* m5. ``live._issue_live_token`` reads the env keys, not ``_stt_keys()``.
* m6. ``AssemblyAISTT.transcribe`` uses ``api_key()``, not ``resolve_api_key()``.
* m7. The ``/v1`` door drops ``call_credentials``, or reads the env key.
* m8. The prewarm drops ``call_credentials``, or reads the env key.
"""
from __future__ import annotations

import ast
import base64
import os
import sys
import types
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from acb_llm.key_store import ProviderKeyStore

ROOT = Path(__file__).resolve().parents[2]

_ORG_A = "aaaaaaaa-0000-0000-0000-00000000000a"
_ORG_B = "bbbbbbbb-0000-0000-0000-00000000000b"

_KEY_A = "sk-test-org-a-0001"
_KEY_B = "sk-test-org-b-0002"
_PLATFORM = "sk-test-platform-0003"
_DG_A, _DG_B = "dg-test-org-a-0004", "dg-test-org-b-0005"
_AAI_A, _AAI_B = "aai-test-org-a-0006", "aai-test-org-b-0007"
_ANT_A, _ANT_B = "ant-test-org-a-0008", "ant-test-org-b-0009"

_MODEL = "deepseek/deepseek-chat"
_ANT_MODEL = "anthropic/claude-sonnet-4-5"
_ENV = "DEEPSEEK_API_KEY"
_ATTR = "deepseek_api_key"

#: Every provider env var the key store knows. A test clears each one.
_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "TOGETHER_API_KEY", "OPENROUTER_API_KEY",
    "DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY",
)
_LITELLM_ATTRS = (
    "api_key", "anthropic_api_key", "gemini_api_key", "deepseek_api_key",
    "groq_api_key", "mistral_api_key", "together_api_key", "openrouter_api_key",
    "deepgram_api_key", "assemblyai_api_key",
)


class _TwoOrgStore(ProviderKeyStore):
    """A real key store over an in-memory ``provider_keys`` table.

    ``sole`` is the organization that ``_resolve_org(None)`` answers while
    ``org_count`` is 1. At 2 it answers nothing, as the real SQL does.
    ``executed`` counts every statement, so a test can prove that a call read
    no row. ``fail_put`` makes the INSERT raise, as a store outage does.
    """

    def __init__(self, rows: dict[tuple[str, str], str], *, sole: str) -> None:
        super().__init__()
        from cryptography.fernet import Fernet

        self._fernet = Fernet(Fernet.generate_key())
        self.rows = rows
        self.sole = sole
        self.org_count = 1
        self.executed = 0
        self.fail_put = False

    def _enc(self, plain: str) -> str:
        return base64.urlsafe_b64encode(self._f.encrypt(plain.encode())).decode("ascii")

    async def _execute(self, sql: str, **params: Any) -> list[dict[str, Any]]:  # type: ignore[override]
        self.executed += 1
        norm = " ".join(sql.split())
        if norm.startswith("SELECT id FROM organization"):
            return [{"id": self.sole}] if self.org_count == 1 else []
        if norm.startswith("SELECT encrypted FROM provider_keys"):
            plain = self.rows.get((params["org_id"], params["provider"]))
            return [{"encrypted": self._enc(plain)}] if plain is not None else []
        if norm.startswith("SELECT provider, encrypted FROM provider_keys"):
            return [
                {"provider": p, "encrypted": self._enc(v)}
                for (o, p), v in self.rows.items() if o == params["org_id"]
            ]
        if norm.startswith("INSERT INTO provider_keys"):
            if self.fail_put:
                raise ConnectionError("connection refused (postgresql://u:secret@db)")
            plain = self._f.decrypt(base64.urlsafe_b64decode(params["encrypted"])).decode()
            self.rows[(params["org_id"], params["provider"])] = plain
            return []
        raise AssertionError(f"unexpected SQL: {norm[:80]}")


def _set_byok(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    """Set the flag in the env. The env wins over any env file of the box."""
    from acb_common.settings import get_settings

    monkeypatch.setenv("BYOK_ENABLED", "true" if on else "false")
    get_settings.cache_clear()


@pytest.fixture()
def clean_process(monkeypatch: pytest.MonkeyPatch):
    """No provider key in ``os.environ`` or in litellm, before and after."""
    import acb_llm.client as client
    import litellm
    from acb_common.settings import get_settings

    for env in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(env, raising=False)
    for attr in _LITELLM_ATTRS:
        monkeypatch.setattr(litellm, attr, None, raising=False)
    monkeypatch.setattr(client, "_keys_loaded", False)
    saved = dict(os.environ)
    yield
    # A leaked write must not reach the next test.
    os.environ.clear()
    os.environ.update(saved)
    get_settings.cache_clear()


@pytest.fixture()
def byok_on(monkeypatch: pytest.MonkeyPatch, clean_process) -> None:
    _set_byok(monkeypatch, True)


@pytest.fixture()
def byok_off(monkeypatch: pytest.MonkeyPatch, clean_process) -> None:
    _set_byok(monkeypatch, False)


@pytest.fixture()
def platform(monkeypatch: pytest.MonkeyPatch, clean_process) -> str:
    """The operator's env key for every provider: a THIRD value, not A or B."""
    for env in _PROVIDER_ENV_VARS:
        monkeypatch.setenv(env, _PLATFORM)
    return _PLATFORM


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch, clean_process) -> _TwoOrgStore:
    import acb_llm.key_store as key_store

    fake = _TwoOrgStore(
        {
            (_ORG_A, "deepseek"): _KEY_A, (_ORG_B, "deepseek"): _KEY_B,
            (_ORG_A, "deepgram"): _DG_A, (_ORG_B, "deepgram"): _DG_B,
            (_ORG_A, "assemblyai"): _AAI_A, (_ORG_B, "assemblyai"): _AAI_B,
            (_ORG_A, "anthropic"): _ANT_A, (_ORG_B, "anthropic"): _ANT_B,
        },
        sole=_ORG_B,
    )
    monkeypatch.setattr(key_store, "_store", fake)
    return fake


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The direct arm of ``client.complete``, with a fake litellm call."""
    import acb_llm.client as client
    import acb_llm.routed as routed
    import litellm

    seen: list[dict[str, Any]] = []

    async def fake_acompletion(**kw: Any) -> Any:
        seen.append(kw)
        return litellm.ModelResponse(
            model=kw["model"],
            choices=[{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": "ok"}}],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(routed, "routing_is_on", lambda: False)
    monkeypatch.setattr(client, "_emit_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(client, "acompletion", fake_acompletion)
    monkeypatch.setitem(client._TIER_MODEL, "tier1", _MODEL)
    return seen


def _sent_key(call: dict[str, Any]) -> str | None:
    """The key litellm sends: the kwarg, else the module global, else the env."""
    import litellm

    return (
        call.get("api_key")
        or getattr(litellm, _ATTR, None)
        or os.environ.get(_ENV)
        or None
    )


async def _complete_as(org: str | None) -> None:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from acb_llm.client import LLMTier, complete

    token = bind_tenant(org) if org else clear_tenant()
    try:
        await complete(tier=LLMTier.TIER_1, messages=[{"role": "user", "content": "hi"}])
    finally:
        release_tenant(token)


async def _start_with_one_org_then_add_a_second(store: _TwoOrgStore) -> None:
    """Steps 1 and 2 of the module docstring, through the real latch."""
    from acb_llm.client import _ensure_keys_loaded

    await _ensure_keys_loaded()  # the box starts while org B is the sole org
    store.org_count = 2          # org A signs up


class TestTwoOrganizations:
    async def test_a_call_for_org_a_sends_org_a_key(self, store, calls, byok_on) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        assert _sent_key(calls[-1]) == _KEY_A, (
            "a call for org A sent another organization's key: the process "
            "globals that the startup latch filled still answer it"
        )

    async def test_each_call_carries_its_own_key_in_one_process(
        self, store, calls, byok_on, platform,
    ) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        await _complete_as(_ORG_B)
        await _complete_as(_ORG_A)
        assert [c.get("api_key") for c in calls] == [_KEY_A, _KEY_B, _KEY_A]

    async def test_the_load_leaves_no_provider_key_in_the_process(
        self, store, calls, byok_on,
    ) -> None:
        import litellm

        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        leaked_env = sorted(e for e in _PROVIDER_ENV_VARS if os.environ.get(e))
        leaked_attr = sorted(a for a in _LITELLM_ATTRS if getattr(litellm, a, None))
        assert leaked_env == [], f"os.environ holds provider keys: {leaked_env}"
        assert leaked_attr == [], f"litellm globals hold provider keys: {leaked_attr}"


class TestNoTenant:
    async def test_no_tenant_key_reaches_an_untenanted_call(self, store, calls, byok_on) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(None)
        assert _sent_key(calls[-1]) is None, (
            "an untenanted call sent a tenant key once two organizations exist"
        )

    async def test_an_untenanted_call_sends_the_named_platform_key(
        self, store, calls, byok_on, platform,
    ) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(None)
        assert calls[-1].get("api_key") == _PLATFORM

    async def test_org_key_wins_over_the_platform_key(
        self, store, calls, byok_on, platform,
    ) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        assert calls[-1].get("api_key") == _KEY_A


# ── Every direct site ───────────────────────────────────────────────────────


class _Captured(Exception):
    """A fake call raises this after it records the key it was given."""


class _Recorder:
    """The key that each faked outbound call carried, in order."""

    def __init__(self) -> None:
        self.keys: list[str | None] = []


@pytest.fixture()
def rec(monkeypatch: pytest.MonkeyPatch, store: _TwoOrgStore) -> _Recorder:
    """Each outbound door of a direct site records its key and stops.

    litellm verbs, the AssemblyAI HTTP client and the two live-token mints.
    No call leaves the process. Two orgs exist unless a test says otherwise.
    """
    import acb_llm.client as client
    import acb_llm.context as context
    import acb_llm.routed as routed
    import httpx
    import litellm
    from gateway.routes import v1_compat
    from gateway.routes.notes import live

    r = _Recorder()

    async def fake_litellm(**kw: Any) -> Any:
        r.keys.append(kw.get("api_key"))
        raise _Captured()

    class _FakeHttpx:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeHttpx:
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def post(self, _url: str, **kw: Any) -> Any:
            r.keys.append(kw.get("headers", {}).get("authorization"))
            raise _Captured()

        get = post

    async def fake_mint(api_key: str) -> str:
        r.keys.append(api_key)
        return "short-lived-token"

    monkeypatch.setattr(routed, "routing_is_on", lambda: False)
    monkeypatch.setattr(client, "acompletion", fake_litellm)
    monkeypatch.setattr(litellm, "acompletion", fake_litellm)
    monkeypatch.setattr(litellm, "atranscription", fake_litellm)
    monkeypatch.setattr(client, "_emit_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(context, "resolve_underlying_model", lambda m: m)
    # tier1 for the completion sites, and one Anthropic tier for the prewarm.
    monkeypatch.setattr(client, "_TIER_MODEL", {"tier1": _MODEL, "tier3": _ANT_MODEL})
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpx)
    monkeypatch.setattr(live, "_mint_assemblyai_token", fake_mint)
    monkeypatch.setattr(live, "_mint_ephemeral_key", fake_mint)
    monkeypatch.setattr(live, "_configured_stt_model", lambda: "")
    monkeypatch.setattr(v1_compat, "_router_should_serve", lambda _body: False)
    monkeypatch.setattr(v1_compat, "_refuse_unrouted", lambda: None)
    monkeypatch.setenv("PROMPT_CACHE_PREWARM", "1")
    fake_agents = types.ModuleType("orchestrator.agents")
    fake_agents.build_orchestrator_agent = lambda **_k: types.SimpleNamespace(  # type: ignore[attr-defined]
        default_options={"instructions": "stable prefix " * 60},
    )
    monkeypatch.setitem(sys.modules, "orchestrator.agents", fake_agents)
    store.org_count = 2
    return r


async def _site_complete() -> None:
    from acb_llm.client import LLMTier, complete

    await complete(tier=LLMTier.TIER_1, messages=[{"role": "user", "content": "hi"}])


async def _site_complete_with_tools() -> None:
    from acb_llm.client import LLMTier, complete_with_tools

    tool = {"type": "function", "function": {
        "name": "noop", "parameters": {"type": "object", "properties": {}}}}
    await complete_with_tools(
        tier=LLMTier.TIER_1, messages=[{"role": "user", "content": "hi"}], tools=[tool],
    )


async def _site_acompletion_with_fallback() -> None:
    from acb_llm.context import acompletion_with_fallback

    await acompletion_with_fallback(
        model=_MODEL, messages=[{"role": "user", "content": "hi"}], max_tokens=50,
    )


async def _site_acompletion_stream_text() -> None:
    from acb_llm.context import acompletion_stream_text

    await acompletion_stream_text(
        model=_MODEL, messages=[{"role": "user", "content": "hi"}], max_tokens=50,
    )


async def _site_litellm_stt() -> None:
    from acb_stt.litellm_provider import LiteLLMSTT
    from acb_stt.types import AudioInput, SttOptions

    await LiteLLMSTT("deepgram/nova-3").transcribe(
        AudioInput(data=b"\x00" * 16), SttOptions(model="deepgram/nova-3"),
    )


async def _site_assemblyai_transcribe() -> None:
    """LIVE: Note Taker batch transcripts and WhatsApp voice notes."""
    from acb_stt.assemblyai_provider import AssemblyAISTT
    from acb_stt.types import AudioInput, SttOptions

    await AssemblyAISTT("assemblyai/universal-3-pro").transcribe(
        AudioInput(data=b"\x00" * 16), SttOptions(),
    )


async def _site_live_token_assemblyai() -> None:
    """LIVE: the live-caption token, AssemblyAI arm."""
    from gateway.routes.notes import live

    await live._issue_live_token()


async def _site_live_token_deepgram() -> None:
    """LIVE: the live-caption token, Deepgram arm (the tier names Deepgram)."""
    from gateway.routes.notes import live

    original = live._configured_stt_model
    live._configured_stt_model = lambda: "deepgram/nova-3"  # type: ignore[assignment]
    try:
        await live._issue_live_token()
    finally:
        live._configured_stt_model = original  # type: ignore[assignment]


class _FakeRequest:
    """The two things ``_handle_chat_completions`` reads off a Request."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.headers: dict[str, str] = {}

    async def json(self) -> dict[str, Any]:
        return self._body


async def _site_v1_door() -> None:
    from gateway.routes import v1_compat

    await v1_compat._handle_chat_completions(  # type: ignore[arg-type]
        _FakeRequest({"model": _MODEL, "messages": [{"role": "user", "content": "hi"}]}),
    )


async def _site_prewarm() -> None:
    from gateway import main as gateway_main

    await gateway_main._prewarm_prompt_cache()


#: name → (driver, org A's key, org B's key). Each site binds the caller's org.
_BOUND_SITES = {
    "complete": (_site_complete, _KEY_A, _KEY_B),
    "complete_with_tools": (_site_complete_with_tools, _KEY_A, _KEY_B),
    "acompletion_with_fallback": (_site_acompletion_with_fallback, _KEY_A, _KEY_B),
    "acompletion_stream_text": (_site_acompletion_stream_text, _KEY_A, _KEY_B),
    "litellm_stt": (_site_litellm_stt, _DG_A, _DG_B),
    "assemblyai_transcribe": (_site_assemblyai_transcribe, _AAI_A, _AAI_B),
    "live_token_assemblyai": (_site_live_token_assemblyai, _AAI_A, _AAI_B),
    "live_token_deepgram": (_site_live_token_deepgram, _DG_A, _DG_B),
}

#: H4 sites. They bind no tenant: name → (driver, the sole org B's key).
_UNBOUND_SITES = {
    "v1_door": (_site_v1_door, _KEY_B),
    "prewarm": (_site_prewarm, _ANT_B),
}


async def _run(rec: _Recorder, site: Any, org: str | None) -> list[str | None]:
    """Run *site* once, bound to *org*. Answer the keys its calls carried."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    before = len(rec.keys)
    token = bind_tenant(org) if org else clear_tenant()
    try:
        await site()
    except Exception:  # the fakes raise once they hold the key
        pass
    finally:
        release_tenant(token)
    return rec.keys[before:]


async def _warm_latch() -> None:
    """Run the once-per-process seed now, so a later count sees calls only."""
    from acb_llm.client import _ensure_keys_loaded

    await _ensure_keys_loaded()


class TestEveryDirectSiteCarriesItsOwnKey:
    """BYOK on: A, then B, then A, in one process, at each direct site."""

    @pytest.mark.parametrize("name", sorted(_BOUND_SITES))
    async def test_the_site_sends_the_bound_org_key(
        self, name, rec, byok_on, platform,
    ) -> None:
        site, key_a, key_b = _BOUND_SITES[name]
        got = [
            await _run(rec, site, _ORG_A),
            await _run(rec, site, _ORG_B),
            await _run(rec, site, _ORG_A),
        ]
        assert got == [[key_a], [key_b], [key_a]], (
            f"{name}: the call did not carry the bound org's key. It sent "
            f"{got}. The platform env key is {platform!r}, so that value means "
            "the site fell back to the env, and None means it sent no key."
        )

    async def test_the_assemblyai_provider_resolves_the_bound_org_key(
        self, store, byok_on,
    ) -> None:
        from acb_common.db import bind_tenant, release_tenant
        from acb_stt.assemblyai_provider import resolve_api_key

        store.org_count = 2
        got = []
        for org in (_ORG_A, _ORG_B):
            token = bind_tenant(org)
            try:
                got.append(await resolve_api_key())
            finally:
                release_tenant(token)
        assert got == [_AAI_A, _AAI_B]

    async def test_a_caller_api_key_of_none_is_not_doubled(self, store, byok_on) -> None:
        from acb_llm.client import call_credentials

        assert await call_credentials(_MODEL, {"api_key": None}) == {}
        assert await call_credentials(_MODEL, {"api_base": "http://local"}) == {}


class TestTheUntenantedSites:
    """The ``/v1`` door and the prewarm bind no tenant (H4)."""

    @pytest.mark.parametrize("name", sorted(_UNBOUND_SITES))
    async def test_one_org_sends_the_sole_org_key(
        self, name, rec, store, byok_on, platform,
    ) -> None:
        site, sole_key = _UNBOUND_SITES[name]
        store.org_count = 1
        got = await _run(rec, site, None)
        assert got == [sole_key], f"{name} sent {got}"

    @pytest.mark.parametrize("name", sorted(_UNBOUND_SITES))
    async def test_two_orgs_send_the_platform_key_never_a_tenant_key(
        self, name, rec, store, byok_on, platform,
    ) -> None:
        site, _ = _UNBOUND_SITES[name]
        store.org_count = 2
        got = await _run(rec, site, None)
        assert got == [_PLATFORM], f"{name} sent {got}"


class TestByokOffIsTodaysBehaviour:
    """BYOK off (the default): the platform key, at every site, and no row read.

    Production runs with BYOK off. Old ``llm`` rows there are copies of
    operator keys (migration 158, the first-boot seed). With BYOK off none of
    them may reach a call.
    """

    @pytest.mark.parametrize("name", sorted(_BOUND_SITES))
    async def test_a_bound_site_sends_the_platform_key(
        self, name, rec, store, byok_off, platform,
    ) -> None:
        site, _, _ = _BOUND_SITES[name]
        await _warm_latch()
        store.executed = 0
        got = await _run(rec, site, _ORG_A)
        assert got == [_PLATFORM], (
            f"{name} sent {got} with BYOK off. Org A holds a row, and production "
            "sends the platform key."
        )
        assert store.executed == 0, f"{name} read the store with BYOK off"

    @pytest.mark.parametrize("name", sorted(_UNBOUND_SITES))
    async def test_an_unbound_site_sends_the_platform_key(
        self, name, rec, store, byok_off, platform,
    ) -> None:
        site, _ = _UNBOUND_SITES[name]
        store.org_count = 1  # the sole org holds a row, and it must not win
        await _warm_latch()
        store.executed = 0
        got = await _run(rec, site, None)
        assert got == [_PLATFORM], f"{name} sent {got} with BYOK off"
        assert store.executed == 0, f"{name} read the store with BYOK off"

    async def test_no_platform_key_means_no_key(self, rec, store, byok_off) -> None:
        site, _, _ = _BOUND_SITES["complete"]
        got = await _run(rec, site, _ORG_A)
        assert got == [None]

    async def test_the_live_token_without_a_platform_key_is_503(
        self, rec, store, byok_off,
    ) -> None:
        from fastapi import HTTPException
        from gateway.routes.notes import live

        with pytest.raises(HTTPException) as caught:
            await live._issue_live_token()
        assert caught.value.status_code == 503
        assert rec.keys == []


# ── The save door ───────────────────────────────────────────────────────────


class _User:
    email = "admin@org-b.test"


async def _save_as(org: str, provider: str, key: str) -> dict[str, str]:
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes import settings as settings_routes

    token = bind_tenant(org)
    try:
        req = settings_routes.ProviderKeyRequest(provider=provider, api_key=key)
        return await settings_routes.set_provider_key(req, _user=_User())  # type: ignore[arg-type]
    finally:
        release_tenant(token)


@pytest.fixture()
def env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from gateway.routes import settings as settings_routes

    path = tmp_path / ".env"
    path.write_bytes(b"OTHER=1\n")
    monkeypatch.setattr(settings_routes, "_env_file_path", lambda: path)
    return path


class TestTheSaveDoor:
    async def test_a_byok_save_writes_only_the_callers_store(
        self, store, calls, env_file, byok_on,
    ) -> None:
        store.org_count = 2
        store.rows.pop((_ORG_B, "deepseek"))
        before = env_file.read_bytes()

        out = await _save_as(_ORG_B, "deepseek", "sk-test-saved-by-b")

        assert out["ok"] == "true"
        assert env_file.read_bytes() == before, "the save wrote the env file"
        assert os.environ.get(_ENV) is None, "the save wrote os.environ"
        assert store.rows[(_ORG_B, "deepseek")] == "sk-test-saved-by-b"
        assert store.rows[(_ORG_A, "deepseek")] == _KEY_A
        await _complete_as(_ORG_A)
        await _complete_as(_ORG_B)
        assert [c.get("api_key") for c in calls] == [_KEY_A, "sk-test-saved-by-b"]

    async def test_a_save_that_did_not_land_is_never_a_200(
        self, store, env_file, byok_on,
    ) -> None:
        from fastapi import HTTPException

        store.org_count = 2
        store.fail_put = True
        with pytest.raises(HTTPException) as caught:
            await _save_as(_ORG_B, "deepseek", "sk-test-saved-by-b")
        assert caught.value.status_code == 503
        detail = str(caught.value.detail)
        assert "no key changed" in detail and "Try again" in detail
        assert "secret" not in detail and "postgresql" not in detail
        assert "sk-test-saved-by-b" not in detail

    @pytest.mark.parametrize("provider", ["github", "vllm"])
    async def test_a_provider_the_store_cannot_send_is_refused(
        self, provider, store, env_file, byok_on,
    ) -> None:
        from fastapi import HTTPException

        before = env_file.read_bytes()
        rows = dict(store.rows)
        value = "http://evil.example/v1" if provider == "vllm" else "ghp_test0001"
        with pytest.raises(HTTPException) as caught:
            await _save_as(_ORG_B, provider, value)
        assert caught.value.status_code == 403
        assert env_file.read_bytes() == before
        assert store.rows == rows

    @pytest.mark.parametrize("env_var", _PROVIDER_ENV_VARS)
    def test_the_env_file_writer_refuses_every_provider_key(self, env_var, env_file) -> None:
        from acb_common import env_guard
        from gateway.routes import settings as settings_routes

        before = env_file.read_bytes()
        with pytest.raises(env_guard.EnvWriteRefused) as caught:
            settings_routes._write_env_key(env_var, "sk-test-0001")
        assert caught.value.platform is True
        assert env_file.read_bytes() == before


class TestTheModelsPageReadsTheCallersStore:
    @pytest.fixture()
    def no_settings_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings with BYOK on and no provider field, whatever the env file holds."""
        from gateway.routes import settings as settings_routes

        stub = types.SimpleNamespace(byok_enabled=True)
        monkeypatch.setattr(settings_routes, "get_settings", lambda: stub)

    async def _as(self, org: str, coro_fn: Any) -> Any:
        from acb_common.db import bind_tenant, release_tenant

        token = bind_tenant(org)
        try:
            return await coro_fn()
        finally:
            release_tenant(token)

    async def test_the_badge_reads_only_the_callers_rows(
        self, store, byok_on, no_settings_keys,
    ) -> None:
        from gateway.routes import settings as st

        store.org_count = 2
        store.rows.pop((_ORG_B, "deepseek"))
        keyed_a = await self._as(_ORG_A, st._caller_keyed_providers)
        keyed_b = await self._as(_ORG_B, st._caller_keyed_providers)
        assert st._is_provider_configured("deepseek", keyed_a) is True
        assert st._is_provider_configured("deepseek", keyed_b) is False, (
            "org B's Models page read another organization's saved key"
        )

    async def test_the_refresh_key_is_the_callers(
        self, store, byok_on, no_settings_keys,
    ) -> None:
        from gateway.routes import settings as st

        store.org_count = 2
        store.rows.pop((_ORG_B, "deepseek"))
        assert await self._as(_ORG_A, lambda: st._caller_api_key("deepseek")) == _KEY_A
        assert await self._as(_ORG_B, lambda: st._caller_api_key("deepseek")) is None

    async def test_byok_off_reads_no_row(self, store, byok_off) -> None:
        from gateway.routes import settings as st

        store.org_count = 2
        assert await self._as(_ORG_A, st._caller_keyed_providers) == frozenset()
        assert store.executed == 0


# ── The fences ──────────────────────────────────────────────────────────────
#
# A write of a provider key into the env is often dynamic
# (``os.environ[env_var] = key``), so a scan for literal key names misses it.
# The fence is a ratchet over EVERY env write site instead. Each one is named
# here with its reason, so a new site fails until a reviewer reads it. The scan
# reads the imports of each module, so an alias does not hide a write.

_SCANNED = ("apps", "packages", "scripts")
_SKIP_DIRS = frozenset({"tests", "node_modules", ".venv", "__pycache__"})

#: (file, function) → the count of env writes there, and why none of them is
#: a provider key. EXACT: a new site fails, and so does a removed one.
_ENV_WRITE_SITES: dict[tuple[str, str], tuple[int, str]] = {
    ("apps/services/gateway/gateway/flag_reload.py", "reload_flags"): (
        1, "re-reads the operator's env file for the `*_ORGS` and `*_MODES` "
           "flags (docs/flag_reload.md). No stored key."),
    ("apps/services/gateway/gateway/main.py", "lifespan"): (
        4, "PYTHONUTF8, PYTHONIOENCODING, VIRTUAL_ENV and PATH for the child "
           "processes of the gateway."),
    ("apps/services/orchestrator/sandbox/data_engine.py", "<module>"): (
        1, "thread-count limits of the sandbox data engine."),
    ("packages/acb_llm/acb_llm/key_store.py", "configure_integrations"): (
        1, "INTEGRATION credentials, not provider keys. Their per-run scope is "
           "task 3176 (IN-3), outside D58.2."),
    ("packages/acb_memory/acb_memory/_gateway_env.py", "gateway_only_env"): (
        1, "restores the operator's own env values after a mem0 or graphiti "
           "call. It copies no stored key."),
    ("packages/acb_skills/acb_skills/web_reader_worker.py", "_limit_self"): (
        1, "thread-count limits of the reader worker process."),
    ("scripts/check_whatsapp_calling.py", "load_env"): (
        1, "an operator script that reads its own env file."),
    ("scripts/pytest_only_selected.py", "pytest_configure"): (
        1, "PYTHONPATH of a test runner."),
}


def _files(trees: tuple[str, ...] = _SCANNED) -> list[Path]:
    out: list[Path] = []
    for tree in trees:
        for here, dirs, names in os.walk(ROOT / tree):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            out.extend(Path(here) / n for n in names if n.endswith(".py"))
    return sorted(out)


def _read(path: Path) -> str:
    # utf-8-sig: a few files open with a byte-order mark, and ast refuses it.
    return path.read_text(encoding="utf-8-sig")


#: The litellm call verbs. A ``Router`` holds keys in its model list, so it is
#: a verb too: each one outside the Console Router is a finding.
_LITELLM_VERBS = frozenset({
    "completion", "acompletion", "atranscription", "transcription",
    "embedding", "aembedding", "aresponses", "responses",
    "atext_completion", "text_completion", "image_generation",
    "aimage_generation", "speech", "aspeech", "Router",
})
#: A bare call of one of these names counts even with no litellm import in the
#: module, because the name is litellm's own and a re-export can carry it.
_ASYNC_VERBS = frozenset(v for v in _LITELLM_VERBS if v.startswith("a"))


class _Aliases:
    """The local names that a module binds to ``os``, ``environ`` and litellm."""

    def __init__(self, tree: ast.AST) -> None:
        self.os: set[str] = {"os"}
        self.environ: set[str] = set()
        self.putenv: set[str] = set()
        self.litellm: set[str] = {"litellm"}
        self.verbs: dict[str, str] = {}  # a local name → the litellm verb
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".", 1)[0]
                    if a.name == "os" and a.asname:
                        self.os.add(a.asname)
                    if a.name == "litellm" and a.asname:
                        self.litellm.add(a.asname)
                    if top == "litellm" and not a.asname:
                        self.litellm.add("litellm")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                for a in node.names:
                    local = a.asname or a.name
                    if node.module == "os" and a.name == "environ":
                        self.environ.add(local)
                    if node.module == "os" and a.name == "putenv":
                        self.putenv.add(local)
                    if node.module == "litellm" and a.name in _LITELLM_VERBS:
                        self.verbs[local] = a.name

    def is_environ(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.environ
        return (
            isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id in self.os
        )

    def is_litellm(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self.litellm


def _is_env_write(node: ast.AST, al: _Aliases) -> bool:
    """One env write: a subscript or ``|=`` on ``environ``, a mutating method,
    or ``putenv``, through any alias."""
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets = [node.target]
    for t in targets:
        if al.is_environ(t):  # `os.environ |= d`, or a rebind
            return True
        if isinstance(t, ast.Subscript) and al.is_environ(t.value):
            return True
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute):
            if f.attr in {"update", "setdefault", "__setitem__", "__ior__"} and al.is_environ(f.value):
                return True
            if f.attr == "putenv" and isinstance(f.value, ast.Name) and f.value.id in al.os:
                return True
        if isinstance(f, ast.Name) and f.id in al.putenv:
            return True
    return False


def _owners(tree: ast.AST) -> dict[int, str]:
    """``id(node)`` → the name of its innermost function."""
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):  # outer functions come first, so inner ones win
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                owner[id(node)] = fn.name
    return owner


def _env_write_sites(tree: ast.AST) -> dict[str, int]:
    """The innermost function name (or ``<module>``) → its count of env writes."""
    al = _Aliases(tree)
    owner = _owners(tree)
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if _is_env_write(node, al):
            name = owner.get(id(node), "<module>")
            counts[name] = counts.get(name, 0) + 1
    return counts


def _is_key_attr(attr: str) -> bool:
    return "key" in attr.lower()


def _litellm_key_writes(tree: ast.AST) -> list[int]:
    """``<litellm alias>.<x>key = v`` (any assign form) and ``setattr(<alias>, ...)``."""
    al = _Aliases(tree)
    hits: list[int] = []
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and al.is_litellm(t.value) and _is_key_attr(t.attr):
                hits.append(node.lineno)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and node.args and al.is_litellm(node.args[0])):
            hits.append(node.lineno)
    return sorted(set(hits))


@cache
def _scan() -> tuple[dict[tuple[str, str], int], dict[str, list[int]]]:
    env: dict[tuple[str, str], int] = {}
    lit: dict[str, list[int]] = {}
    for path in _files():
        source = _read(path)
        # Cheap text filters first. They name no alias, so no alias escapes.
        has_env = "environ" in source or "putenv" in source
        has_lit = "litellm" in source
        if not (has_env or has_lit):
            continue
        tree = ast.parse(source)
        rel = path.relative_to(ROOT).as_posix()
        if has_env:
            for fn, n in _env_write_sites(tree).items():
                env[(rel, fn)] = n
        if has_lit:
            lines = _litellm_key_writes(tree)
            if lines:
                lit[rel] = lines
    return env, lit


class TestNoProcessGlobalProviderKeyWrite:
    def test_every_os_environ_write_is_a_named_site(self) -> None:
        env, _ = _scan()
        pinned = {site: n for site, (n, _why) in _ENV_WRITE_SITES.items()}
        assert env == pinned, (
            f"the os.environ write sites moved. Measured {sorted(env.items())}. "
            "D58.2: a provider key travels per call "
            "(acb_llm.client.call_credentials), never through the env. Name a "
            "new site in _ENV_WRITE_SITES only with its reason, and never for "
            "a provider key."
        )

    def test_the_key_store_writes_no_provider_key_into_the_env(self) -> None:
        env, _ = _scan()
        key_store = {fn for (rel, fn) in env if rel.endswith("acb_llm/key_store.py")}
        assert key_store == {"configure_integrations"}, key_store

    def test_no_module_sets_a_litellm_key_global(self) -> None:
        _, lit = _scan()
        assert lit == {}, (
            f"a module sets a litellm key global: {lit}. Every caller in the "
            "process would send that key. Pass api_key per call (D58.2)."
        )

    def test_the_scan_sees_the_original_writes(self) -> None:
        """The two lines that H-43 named. The scan must flag both shapes."""
        source = "\n".join([
            "import os",
            "import litellm as _litellm",
            "def configure_litellm(self):",
            "    _provider_config = {'openai': ('api_key', 'OPENAI_API_KEY')}",
            "    for provider, key in {}.items():",
            "        litellm_attr, env_var = _provider_config[provider]",
            "        setattr(_litellm, litellm_attr, key)",
            "        os.environ[env_var] = key",
        ])
        tree = ast.parse(source)
        assert _env_write_sites(tree) == {"configure_litellm": 1}
        assert _litellm_key_writes(tree) == [7]

    @pytest.mark.parametrize("source", [
        pytest.param("from os import environ\ndef f(k, v):\n    environ[k] = v", id="from-os-import-environ"),
        pytest.param("from os import environ as e\ndef f(d):\n    e.update(d)", id="environ-alias-update"),
        pytest.param("import os as o\ndef f(k, v):\n    o.environ[k] = v", id="import-os-as"),
        pytest.param("import os\ndef f(d):\n    os.environ |= d", id="ior"),
        pytest.param("import os\ndef f(d):\n    os.environ.update(d)", id="update"),
        pytest.param("import os\ndef f(k, v):\n    os.environ.setdefault(k, v)", id="setdefault"),
        pytest.param("import os\ndef f(k, v):\n    os.environ.__setitem__(k, v)", id="setitem"),
        pytest.param("import os\ndef f(k, v):\n    os.putenv(k, v)", id="putenv"),
        pytest.param("from os import putenv\ndef f(k, v):\n    putenv(k, v)", id="from-os-import-putenv"),
        pytest.param("import os.path\ndef f(k, v):\n    os.environ[k] = v", id="import-os-path"),
    ])
    def test_the_env_scan_sees_each_shape(self, source: str) -> None:
        assert "environ" in source or "putenv" in source, "the text filter skips it"
        assert _env_write_sites(ast.parse(source)) == {"f": 1}

    @pytest.mark.parametrize("source", [
        pytest.param("import os\ndef f(k):\n    return os.environ.get(k)", id="a-read"),
        pytest.param("environ = {}\ndef f(k, v):\n    environ[k] = v", id="a-local-dict"),
    ])
    def test_the_env_scan_ignores_a_read(self, source: str) -> None:
        assert _env_write_sites(ast.parse(source)) == {}

    @pytest.mark.parametrize("source", [
        pytest.param("import litellm as ll\nll.api_key = 'x'", id="alias-api-key"),
        pytest.param("import litellm\nlitellm.deepgram_api_key = 'x'", id="provider-api-key"),
        pytest.param("import litellm\nlitellm.anthropic_key = 'x'", id="any-key-attr"),
        pytest.param("import litellm\nlitellm.openai_key += 'x'", id="augassign"),
        pytest.param("import litellm\nsetattr(litellm, 'api_key', 'x')", id="setattr"),
        pytest.param("import litellm as _l\ndef f(a, k):\n    setattr(_l, a, k)", id="setattr-alias"),
    ])
    def test_the_litellm_global_scan_sees_each_shape(self, source: str) -> None:
        assert "litellm" in source, "the text filter skips it"
        assert len(_litellm_key_writes(ast.parse(source))) == 1

    @pytest.mark.parametrize("source", [
        pytest.param("import litellm\nlitellm.drop_params = True", id="drop-params"),
        pytest.param("import litellm\nlitellm.suppress_debug_info = True", id="debug-info"),
        pytest.param("import litellm\nk = litellm.api_key", id="a-read"),
    ])
    def test_the_litellm_global_scan_ignores_a_setting(self, source: str) -> None:
        assert _litellm_key_writes(ast.parse(source)) == []


# ── Each litellm call names its own key ─────────────────────────────────────

_VERB_TREES = ("apps", "packages", "scripts")
#: The Router passes ``call_kwargs["api_key"]`` itself (D58.1). It is the model.
_VERB_EXEMPT = ("apps/services/customer_console/",)
#: The functions whose dict answer a call merges as its credentials.
_CRED_FUNCS = frozenset({"call_credentials"})


def _func_name(f: ast.AST) -> str | None:
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_cred_call(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    return isinstance(node, ast.Call) and _func_name(node.func) in _CRED_FUNCS


def _verb_name(call: ast.Call, al: _Aliases) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        if f.id in al.verbs:
            return al.verbs[f.id]
        if f.id in _ASYNC_VERBS:
            return f.id
    if isinstance(f, ast.Attribute) and al.is_litellm(f.value) and f.attr in _LITELLM_VERBS:
        return f.attr
    return None


def _cred_names(fn: ast.AST) -> set[str]:
    """The dicts in *fn* that hold the answer of ``call_credentials``.

    ``creds = await call_credentials(...)`` names ``creds``, and
    ``extra.update(await call_credentials(...))`` names ``extra``.
    """
    names: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and _is_cred_call(n.value):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        if isinstance(n, ast.AnnAssign) and _is_cred_call(n.value) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "update" and isinstance(n.func.value, ast.Name)
                and n.args and _is_cred_call(n.args[0])):
            names.add(n.func.value.id)
    return names


def _unkeyed_verb_calls(tree: ast.AST) -> list[int]:
    """Lines of a litellm verb call that merges no key into the call itself.

    A call is keyed when it passes ``api_key=`` (not ``None``) itself, or when
    it spreads ``**X`` where X is ``call_credentials(...)``, or a name that an
    enclosing function assigned from it or ``.update``-d with it. A function
    that calls ``call_credentials`` and drops the answer is NOT keyed.
    """
    al = _Aliases(tree)
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _verb_name(node, al) is None:
            continue
        if any(
            k.arg == "api_key" and not (isinstance(k.value, ast.Constant) and k.value.value is None)
            for k in node.keywords
        ):
            continue
        spread = [k.value for k in node.keywords if k.arg is None]
        if any(_is_cred_call(v) for v in spread):
            continue
        names: set[str] = set()
        cur: ast.AST = node
        while id(cur) in parent:
            cur = parent[id(cur)]
            if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
                names |= _cred_names(cur)
        if any(isinstance(v, ast.Name) and v.id in names for v in spread):
            continue
        hits.append(node.lineno)
    return hits


@cache
def _verb_scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in _files(_VERB_TREES):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(_VERB_EXEMPT):
            continue
        source = _read(path)
        if "litellm" not in source and not any(v in source for v in _ASYNC_VERBS):
            continue
        lines = _unkeyed_verb_calls(ast.parse(source))
        if lines:
            found[rel] = lines
    return found


class TestEveryLitellmCallNamesItsKey:
    def test_no_litellm_call_leans_on_a_process_global_key(self) -> None:
        found = _verb_scan()
        assert found == {}, (
            f"a litellm call names no key: {found}. With no api_key, litellm "
            "reads a process-global env var for every caller. Merge "
            "`await acb_llm.client.call_credentials(model, kwargs)` into the "
            "call, or pass api_key= yourself (D58.2)."
        )

    @pytest.mark.parametrize("verb", sorted(_LITELLM_VERBS))
    def test_each_verb_is_seen_through_litellm_and_an_alias(self, verb: str) -> None:
        for source in (
            f"import litellm\nasync def f(m):\n    return litellm.{verb}(model=m)",
            f"import litellm as ll\nasync def f(m):\n    return ll.{verb}(model=m)",
            f"from litellm import {verb} as x\nasync def f(m):\n    return x(model=m)",
            f"from litellm import {verb}\nasync def f(m):\n    return {verb}(model=m)",
        ):
            assert _unkeyed_verb_calls(ast.parse(source)) == [3], source

    @pytest.mark.parametrize("source", [
        pytest.param(
            "from litellm import acompletion\nasync def f(m):\n"
            "    await call_credentials(m)\n    return await acompletion(model=m)",
            id="answer-dropped"),
        pytest.param(
            "from litellm import acompletion\nasync def f(m):\n"
            "    creds = await call_credentials(m)\n    return await acompletion(model=m)",
            id="answer-not-merged"),
        pytest.param(
            "from litellm import acompletion\nasync def f(m, extra):\n"
            "    creds = await call_credentials(m)\n    return await acompletion(model=m, **extra)",
            id="another-dict-merged"),
        pytest.param(
            "from litellm import acompletion\nasync def f(m):\n"
            "    return await acompletion(model=m, api_key=None)",
            id="api-key-none"),
    ])
    def test_a_call_that_does_not_merge_its_key_is_seen(self, source: str) -> None:
        assert len(_unkeyed_verb_calls(ast.parse(source))) == 1

    @pytest.mark.parametrize("source", [
        pytest.param(
            "from litellm import acompletion\nasync def f(m):\n"
            "    creds = await call_credentials(m)\n    return await acompletion(model=m, **creds)",
            id="spread-name"),
        pytest.param(
            "from litellm import acompletion\nasync def f(m):\n"
            "    return await acompletion(model=m, **(await call_credentials(m)))",
            id="spread-inline"),
        pytest.param(
            "import litellm\nasync def f(m, extra):\n"
            "    extra.update(await call_credentials(m, extra))\n"
            "    return await litellm.atranscription(model=m, **extra)",
            id="update-then-spread"),
        pytest.param(
            "from litellm import acompletion\nasync def outer(m):\n    common = {'model': m}\n"
            "    common.update(await call_credentials(m, common))\n"
            "    async def gen():\n        return await acompletion(**common)\n    return gen",
            id="nested-function"),
        pytest.param(
            "import litellm\nasync def f(m, k):\n    return await litellm.aembedding(model=m, api_key=k)",
            id="api-key-kwarg"),
    ])
    def test_a_keyed_call_passes(self, source: str) -> None:
        assert _unkeyed_verb_calls(ast.parse(source)) == []
