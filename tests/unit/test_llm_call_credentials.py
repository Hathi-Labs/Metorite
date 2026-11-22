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
``os.environ`` too. A second organization that saved its own key overwrote
the env value that litellm reads for every caller.

What this file holds (R7):

* ``TestTwoOrganizations``: each direct call carries the key of the bound
  organization, and ``os.environ`` and the litellm globals hold no provider
  key after the load.
* ``TestNoTenant``: a call with no bound organization never gets a tenant
  key once two organizations exist. It gets the named platform key or none.
* ``TestTheSaveDoor``: a BYOK save writes the store of its own organization,
  and never ``os.environ``.
* ``TestNoProcessGlobalProviderKeyWrite``: a source fence over ``apps/``,
  ``packages/`` and ``scripts/``. Each ``os.environ`` write site is named
  with its reason, so a new one fails. No module sets a ``litellm`` key
  attribute.

Mutations this file catches:

* Restore ``os.environ[env_var] = key`` in ``key_store.py``: the fence and
  ``test_the_load_leaves_no_provider_key_in_the_process`` fail.
* Drop the per-call ``api_key`` from ``client.complete``: the two-org tests
  fail.
"""
from __future__ import annotations

import ast
import base64
import os
import re
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

_MODEL = "deepseek/deepseek-chat"
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
    """

    def __init__(self, rows: dict[tuple[str, str], str], *, sole: str) -> None:
        super().__init__()
        from cryptography.fernet import Fernet

        self._fernet = Fernet(Fernet.generate_key())
        self.rows = rows
        self.sole = sole
        self.org_count = 1

    def _enc(self, plain: str) -> str:
        return base64.urlsafe_b64encode(self._f.encrypt(plain.encode())).decode("ascii")

    async def _execute(self, sql: str, **params: Any) -> list[dict[str, Any]]:  # type: ignore[override]
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
            self.rows[(params["org_id"], params["provider"])] = "stored"
            return []
        raise AssertionError(f"unexpected SQL: {norm[:80]}")


@pytest.fixture()
def clean_process(monkeypatch: pytest.MonkeyPatch):
    """No provider key in ``os.environ`` or in litellm, before and after."""
    import acb_llm.client as client
    import litellm

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


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch, clean_process) -> _TwoOrgStore:
    import acb_llm.key_store as key_store

    fake = _TwoOrgStore(
        {
            (_ORG_A, "deepseek"): _KEY_A, (_ORG_B, "deepseek"): _KEY_B,
            (_ORG_A, "deepgram"): _DG_A, (_ORG_B, "deepgram"): _DG_B,
            (_ORG_A, "assemblyai"): _AAI_A, (_ORG_B, "assemblyai"): _AAI_B,
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
    async def test_a_call_for_org_a_sends_org_a_key(self, store, calls) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        assert _sent_key(calls[-1]) == _KEY_A, (
            "a call for org A sent another organization's key: the process "
            "globals that the startup latch filled still answer it"
        )

    async def test_each_call_carries_its_own_key_in_one_process(self, store, calls) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        await _complete_as(_ORG_B)
        await _complete_as(_ORG_A)
        assert [c.get("api_key") for c in calls] == [_KEY_A, _KEY_B, _KEY_A]

    async def test_the_load_leaves_no_provider_key_in_the_process(self, store, calls) -> None:
        import litellm

        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(_ORG_A)
        leaked_env = sorted(e for e in _PROVIDER_ENV_VARS if os.environ.get(e))
        leaked_attr = sorted(a for a in _LITELLM_ATTRS if getattr(litellm, a, None))
        assert leaked_env == [], f"os.environ holds provider keys: {leaked_env}"
        assert leaked_attr == [], f"litellm globals hold provider keys: {leaked_attr}"


class TestNoTenant:
    async def test_no_tenant_key_reaches_an_untenanted_call(self, store, calls) -> None:
        await _start_with_one_org_then_add_a_second(store)
        await _complete_as(None)
        assert _sent_key(calls[-1]) is None, (
            "an untenanted call sent a tenant key once two organizations exist"
        )

    async def test_an_untenanted_call_sends_the_named_platform_key(
        self, store, calls, monkeypatch,
    ) -> None:
        await _start_with_one_org_then_add_a_second(store)
        monkeypatch.setenv(_ENV, _PLATFORM)
        await _complete_as(None)
        assert calls[-1].get("api_key") == _PLATFORM

    async def test_org_key_wins_over_the_platform_key(self, store, calls, monkeypatch) -> None:
        await _start_with_one_org_then_add_a_second(store)
        monkeypatch.setenv(_ENV, _PLATFORM)
        await _complete_as(_ORG_A)
        assert calls[-1].get("api_key") == _KEY_A


class TestTheSaveDoor:
    async def test_a_byok_save_never_writes_the_process_env(
        self, store, calls, monkeypatch, tmp_path,
    ) -> None:
        import asyncio

        from acb_common.db import bind_tenant, release_tenant
        from acb_common.settings import get_settings
        from gateway.routes import settings as settings_routes

        env_file = tmp_path / ".env"
        env_file.write_bytes(b"")
        monkeypatch.setattr(settings_routes, "_env_file_path", lambda: env_file)
        monkeypatch.setenv("BYOK_ENABLED", "true")
        get_settings.cache_clear()
        store.org_count = 2
        store.rows.pop((_ORG_B, "deepseek"))

        class _User:
            email = "admin@org-b.test"

        token = bind_tenant(_ORG_B)
        try:
            req = settings_routes.ProviderKeyRequest(provider="deepseek", api_key=_KEY_B)
            await settings_routes.set_provider_key(req, _user=_User())  # type: ignore[arg-type]
            await asyncio.sleep(0)  # the scheduled store write
        finally:
            release_tenant(token)
            get_settings.cache_clear()

        assert os.environ.get(_ENV) is None, "the save door wrote os.environ"
        assert (_ORG_B, "deepseek") in store.rows, "the save missed org B's store"
        await _complete_as(_ORG_A)
        assert _sent_key(calls[-1]) == _KEY_A


# ── Every direct site, as org A and then as org B ───────────────────────────


class _Captured(Exception):
    """The fake litellm call raises this after it records its kwargs."""


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch, store: _TwoOrgStore) -> list[dict[str, Any]]:
    """Each litellm verb records its kwargs and raises. Two orgs exist."""
    import acb_llm.client as client
    import acb_llm.context as context
    import acb_llm.routed as routed
    import litellm

    seen: list[dict[str, Any]] = []

    async def fake(**kw: Any) -> Any:
        seen.append(kw)
        raise _Captured()

    monkeypatch.setattr(routed, "routing_is_on", lambda: False)
    monkeypatch.setattr(client, "acompletion", fake)
    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setattr(litellm, "atranscription", fake)
    monkeypatch.setattr(client, "_emit_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(context, "resolve_underlying_model", lambda m: m)
    monkeypatch.setitem(client._TIER_MODEL, "tier1", _MODEL)
    store.org_count = 2
    return seen


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


_SITES = {
    "complete_with_tools": (_site_complete_with_tools, _KEY_A, _KEY_B),
    "acompletion_with_fallback": (_site_acompletion_with_fallback, _KEY_A, _KEY_B),
    "acompletion_stream_text": (_site_acompletion_stream_text, _KEY_A, _KEY_B),
    "litellm_stt": (_site_litellm_stt, _DG_A, _DG_B),
}


async def _as(org: str, site: Any) -> None:
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        await site()
    except Exception:  # the fake raises once it has the kwargs
        pass
    finally:
        release_tenant(token)


class TestEveryDirectSiteCarriesItsOwnKey:
    """A then B in one process, at each direct site besides ``complete``."""

    @pytest.mark.parametrize("name", sorted(_SITES))
    async def test_the_site_sends_the_bound_org_key(self, name, capture) -> None:
        site, key_a, key_b = _SITES[name]
        await _as(_ORG_A, site)
        await _as(_ORG_B, site)
        await _as(_ORG_A, site)
        assert [c.get("api_key") for c in capture] == [key_a, key_b, key_a], name

    async def test_the_assemblyai_provider_resolves_the_bound_org_key(self, store) -> None:
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

    async def test_the_live_token_resolves_the_bound_org_keys(self, store) -> None:
        from acb_common.db import bind_tenant, release_tenant
        from gateway.routes.notes import live

        store.org_count = 2
        got = []
        for org in (_ORG_A, _ORG_B):
            token = bind_tenant(org)
            try:
                got.append(await live._stt_keys())
            finally:
                release_tenant(token)
        assert got == [(_AAI_A, _DG_A), (_AAI_B, _DG_B)]

    async def test_a_caller_api_key_of_none_is_not_doubled(self, store) -> None:
        from acb_llm.client import call_credentials

        assert await call_credentials(_MODEL, {"api_key": None}) == {}
        assert await call_credentials(_MODEL, {"api_base": "http://local"}) == {}


# ── The fence ───────────────────────────────────────────────────────────────
#
# A write of a provider key into the env is often dynamic
# (``os.environ[env_var] = key``), so a scan for literal key names misses it.
# The fence is a ratchet over EVERY ``os.environ`` write site instead. Each one
# is named here with its reason, so a new site fails until a reviewer reads it.

_SCANNED = ("apps", "packages", "scripts")
_SKIP_DIRS = frozenset({"tests", "node_modules", ".venv", "__pycache__"})

#: (file, function) → the count of ``os.environ`` writes there, and why none of
#: them is a provider key. EXACT: a new site fails, and so does a removed one.
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


def _files() -> list[Path]:
    out: list[Path] = []
    for tree in _SCANNED:
        for here, dirs, names in os.walk(ROOT / tree):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            out.extend(Path(here) / n for n in names if n.endswith(".py"))
    return sorted(out)


def _read(path: Path) -> str:
    # utf-8-sig: a few files open with a byte-order mark, and ast refuses it.
    return path.read_text(encoding="utf-8-sig")


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute) and node.attr == "environ"
        and isinstance(node.value, ast.Name) and node.value.id == "os"
    )


def _is_litellm(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in {"litellm", "_litellm"}


def _is_env_write(node: ast.AST) -> bool:
    """``os.environ[x] = v``, ``os.environ.update/setdefault`` or ``os.putenv``."""
    if isinstance(node, ast.Assign | ast.AugAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(isinstance(t, ast.Subscript) and _is_os_environ(t.value) for t in targets)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        f = node.func
        if f.attr in {"update", "setdefault", "__setitem__"} and _is_os_environ(f.value):
            return True
        return f.attr == "putenv" and isinstance(f.value, ast.Name) and f.value.id == "os"
    return False


def _env_write_sites(tree: ast.AST) -> dict[str, int]:
    """The innermost function name (or ``<module>``) → its count of env writes."""
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):  # outer functions come first, so inner ones win
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                owner[id(node)] = fn.name
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if _is_env_write(node):
            name = owner.get(id(node), "<module>")
            counts[name] = counts.get(name, 0) + 1
    return counts


def _litellm_key_writes(tree: ast.AST) -> list[int]:
    """``litellm.<x>api_key = v`` and ``setattr(litellm, ...)`` anywhere."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Attribute) and _is_litellm(t.value)
                        and "api_key" in t.attr):
                    hits.append(node.lineno)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and node.args
                and _is_litellm(node.args[0])):
            hits.append(node.lineno)
    return sorted(set(hits))


#: A text hint of a litellm key write, so only those files are parsed for it.
_LITELLM_WRITE_HINT = re.compile(r"_?litellm\.\w*api_key\s*=(?!=)|setattr\(\s*_?litellm\b")


@cache
def _scan() -> tuple[dict[tuple[str, str], int], dict[str, list[int]]]:
    env: dict[tuple[str, str], int] = {}
    lit: dict[str, list[int]] = {}
    for path in _files():
        source = _read(path)
        # Cheap text filters first. The parse below decides.
        has_env = "os.environ" in source or "os.putenv" in source
        has_lit = _LITELLM_WRITE_HINT.search(source) is not None
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
        assert _LITELLM_WRITE_HINT.search(source), "the text filter skips the file"
        tree = ast.parse(source)
        assert _env_write_sites(tree) == {"configure_litellm": 1}
        assert _litellm_key_writes(tree) == [7]


# ── Each litellm call names its own key ─────────────────────────────────────

_VERB_TREES = ("apps", "packages")
#: The Router passes ``call_kwargs["api_key"]`` itself (D58.1). It is the model.
_VERB_EXEMPT = ("apps/services/customer_console/",)
_LITELLM_VERBS = frozenset({
    "acompletion", "completion", "atranscription", "transcription",
    "aembedding", "embedding", "aspeech", "speech",
    "aimage_generation", "image_generation",
})
#: A call is keyed when an enclosing function names one of these.
_KEY_SOURCES = frozenset({"call_credentials", "caller_provider_key"})


def _verb_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name) and f.id.startswith("a") and f.id in _LITELLM_VERBS:
        return f.id  # a bare name: only the async verbs, which `from litellm` binds
    if isinstance(f, ast.Attribute) and _is_litellm(f.value) and f.attr in _LITELLM_VERBS:
        return f.attr
    return None


def _names_a_key_source(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and n.id in _KEY_SOURCES:
            return True
        if isinstance(n, ast.Attribute) and n.attr in _KEY_SOURCES:
            return True
    return False


def _unkeyed_verb_calls(tree: ast.AST) -> list[int]:
    """Lines of a litellm verb call with no ``api_key=`` and no key source.

    A call is keyed when it passes ``api_key=`` itself, or when a function
    that encloses it calls ``call_credentials`` or ``caller_provider_key``.
    """
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _verb_name(node) is None:
            continue
        if any(k.arg == "api_key" for k in node.keywords):
            continue
        cur: ast.AST = node
        keyed = False
        while id(cur) in parent:
            cur = parent[id(cur)]
            if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef) and _names_a_key_source(cur):
                keyed = True
                break
        if not keyed:
            hits.append(node.lineno)
    return hits


@cache
def _verb_scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for tree_name in _VERB_TREES:
        for here, dirs, names in os.walk(ROOT / tree_name):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for n in names:
                if not n.endswith(".py"):
                    continue
                path = Path(here) / n
                rel = path.relative_to(ROOT).as_posix()
                if rel.startswith(_VERB_EXEMPT):
                    continue
                source = _read(path)
                if not any(v in source for v in _LITELLM_VERBS):
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

    def test_the_scan_sees_an_unkeyed_call(self) -> None:
        source = "\n".join([
            "from litellm import acompletion",
            "async def f(model):",
            "    return await acompletion(model=model, messages=[])",
            "async def g(model):",
            "    creds = await call_credentials(model)",
            "    return await acompletion(model=model, messages=[], **creds)",
            "async def h(model):",
            "    return await acompletion(model=model, messages=[], api_key='k')",
        ])
        assert _unkeyed_verb_calls(ast.parse(source)) == [3]
