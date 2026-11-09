"""MS-1: the provider registry holds only ids, aliases and verbs that work.

Spec: ``project-docs/specs/customer_console.md`` §6A.18 "The registry
schema" and "The first providers and models". Board: task 2481.

🔴 **What this fence proves, one line each.**

* Every registry model's verb is in ``catalog.KNOWN_INVOCATIONS``, and its
  pair passes ``check_invocation_for_task``, ``check_model_for_invocation``
  and ``check_streams``. So the registry never offers a pair the declare
  route refuses.
* Every job's verbs pass the same checks, and every native handler's verb
  is in the jobs table of its task.
* No alias collides: no alias is a provider id, a callable provider, or the
  alias of two providers.
* Every provider id is a litellm provider or a native vendor, so the Router
  can find a key under it.
* The registry stores no ``api_base``, and the module makes no network call.

⚠️ **Hermetic.** It reads Python objects and the ladder text. It needs no
database, so it never skips (``litellm`` aside, which CI installs).
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest
from customer_console import catalog, handlers, stt_sarvam
from customer_console import provider_registry as registry

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/services/customer_console/customer_console/provider_registry.py"
LADDER = ROOT / "infra/customer_console"

_MODELS = [(p, m) for p in registry.PROVIDERS for m in p.models]


def _ids(pairs):
    return [f"{m.id}:{m.job}" for _, m in pairs]


# ── The models ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("entry", "model"), _MODELS, ids=_ids(_MODELS))
def test_every_model_pair_passes_the_declare_checks(entry, model):
    assert model.invocation in catalog.KNOWN_INVOCATIONS
    assert catalog.check_invocation_for_task(model.invocation, model.job) == model.invocation
    catalog.check_model_for_invocation(model.id, model.invocation, task=model.job)
    assert catalog.check_streams(model.job, model.streams) is model.streams
    assert model.invocation in registry.JOB_VERBS[model.job]


@pytest.mark.parametrize(("entry", "model"), _MODELS, ids=_ids(_MODELS))
def test_every_model_id_names_its_own_provider(entry, model):
    # The Router finds the key by the text before the first slash.
    assert model.id.partition("/")[0] == entry.id
    assert set(model.recommended_for) <= {model.job}


def test_a_model_and_a_job_appear_once():
    pairs = [(m.id, m.job) for _, m in _MODELS]
    assert len(pairs) == len(set(pairs))


def test_the_async_assemblyai_models_are_not_offered():
    ids = {m.id for _, m in _MODELS}
    assert "assemblyai/nano" not in ids
    assert "assemblyai/best" not in ids


def test_each_job_has_at_most_one_default_per_provider():
    for entry in registry.PROVIDERS:
        for job in registry.JOB_LABELS:
            defaults = [m for m in entry.models if m.job == job and job in m.recommended_for]
            assert len(defaults) <= 1, (entry.id, job)


def test_the_defaults_the_owner_approved():
    assert registry.default_model("sarvam", "transcribe").id == "sarvam/saaras:v4/translit"
    for job in ("chat", "vision"):
        assert registry.default_model("anthropic", job).id == "anthropic/claude-haiku-5-5"


def test_the_assemblyai_default_is_english_only():
    """Owner call 1 named `hi,en`. Production evidence changed it (2026-11-09).

    An 8 s English clip came back in Devanagari script with `hi,en`, and in
    clean English with `/en`. So `/en` is the default, and `hi,en` stays as a
    model whose label says what it writes.
    """
    default = registry.default_model("assemblyai", "transcribe")
    assert default.id == "assemblyai/universal-3-5-pro/en"
    assert default.languages == ("en",)
    assert default.recommended_for == ("transcribe",)
    both = registry.registry_model("assemblyai/universal-3-5-pro/hi,en", "transcribe")
    assert both is not None
    assert both.recommended_for == ()
    assert "Devanagari" in both.label


def test_a_sarvam_id_with_no_mode_extends_to_the_default_mode():
    longer = registry.extension_of("sarvam/saaras:v4", "transcribe")
    assert longer is not None
    assert longer.id == f"sarvam/saaras:v4/{stt_sarvam.DEFAULT_MODE}"
    # AssemblyAI with no languages is another model (no steering), not a gap.
    assert registry.extension_of("assemblyai/universal-3-5-pro", "transcribe") is None


#: The (K) items of the spec table: model knowledge, not a fetched document.
_K_MODELS = {
    "sarvam/sarvam-105b",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3.1-pro-preview",
    "gemini/gemini-3.1-flash-image",
    "gemini/gemini-embedding-2",
    "gemini/gemini-3.8-flash-tts",
    "openai/gpt-4o-transcribe",
    "openai/whisper-1",
    "openai/tts-1",
    "groq/whisper-large-v3-turbo",
    "groq/openai/gpt-oss-120b",
    "deepseek/deepseek-flash",
    "deepseek/deepseek-v4-pro",
    "elevenlabs/scribe_v2",
    "deepgram/nova-3",
}


def test_a_k_item_carries_verified_false():
    for _, model in _MODELS:
        assert model.verified is (model.id not in _K_MODELS), model.id


# ── The jobs ─────────────────────────────────────────────────────────────────


def test_every_job_is_a_task_the_ladder_seeds():
    ladder = "\n".join(p.read_text(encoding="utf-8") for p in sorted(LADDER.glob("*.sql")))
    for job in registry.JOB_LABELS:
        assert f"('{job}'," in ladder, job
    assert set(registry.JOB_VERBS) == set(registry.JOB_LABELS)


@pytest.mark.parametrize("job", sorted(registry.JOB_VERBS))
def test_every_job_verb_passes_the_declare_checks(job):
    for verb in registry.JOB_VERBS[job]:
        assert verb in catalog.KNOWN_INVOCATIONS
        assert catalog.check_invocation_for_task(verb, job) == verb


def test_every_native_verb_is_in_the_jobs_table_of_its_task():
    for verb in handlers.NATIVE_HANDLERS:
        task = handlers.native_task_of(verb)
        assert task is not None
        assert verb in registry.JOB_VERBS[task], verb


def test_image_understanding_is_the_vision_task():
    assert registry.JOB_LABELS["vision"] == "Image understanding"
    assert registry.JOB_VERBS["vision"] == frozenset({"acompletion"})


# ── The aliases ──────────────────────────────────────────────────────────────


def test_the_spec_aliases():
    assert registry.normalise("sarvam.ai") == "sarvam"
    assert registry.normalise("assembly.ai") == "assemblyai"
    assert registry.normalise("google") == "gemini"
    assert registry.normalise("claude") == "anthropic"


def test_normalise_lowercases_strips_spaces_and_passes_an_unknown_through():
    assert registry.normalise(" Sarvam.AI ") == "sarvam"
    assert registry.normalise("Open AI") == "openai"
    assert registry.normalise("Some Vendor") == "somevendor"
    assert registry.normalise(None) == ""
    for entry in registry.PROVIDERS:
        assert registry.normalise(entry.id) == entry.id
        assert registry.normalise(registry.normalise(entry.id.upper())) == entry.id


def test_no_alias_collides():
    ids = {p.id for p in registry.PROVIDERS}
    declared = [a for p in registry.PROVIDERS for a in p.aliases]
    # Two providers with one alias would lose one in the map in silence.
    assert len(declared) == len(set(declared)) == len(registry.ALIASES)
    assert not set(declared) & ids
    for alias, target in registry.ALIASES.items():
        assert alias == "".join(alias.split()).lower(), alias
        assert target in ids
        assert registry.normalise(alias) == target


ROUTER_SRC = ROOT / "apps/services/customer_console/customer_console/router.py"


def test_the_router_never_applies_an_alias():
    """R7 for the alias rule (§6A.18). The Router finds a key by the EXACT
    prefix of the model id. An alias in the Router would let the key of one
    provider string serve a model of another, so `router.py` imports no part
    of the registry and calls no `normalise`."""
    import ast

    src = ROUTER_SRC.read_text(encoding="utf-8")
    assert "provider_registry" not in src
    tree = ast.parse(src)
    imported: list[str] = []
    called: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported += [node.module or "", *(a.name for a in node.names)]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            called.append(name)
    assert not [m for m in imported if "provider_registry" in m], imported
    assert "normalise" not in called
    # The guard on the guard: the walk does see the Router's own calls.
    assert "provider_credential" in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "text" in called


def test_no_alias_is_a_provider_the_router_can_call():
    pytest.importorskip("litellm")
    for alias in registry.ALIASES:
        assert not registry.is_callable_provider(alias), (
            f"{alias!r} is a callable provider id, so normalising it would move "
            "a working key to another provider"
        )


# ── The provider ids ─────────────────────────────────────────────────────────


def test_provider_ids_are_unique():
    ids = [p.id for p in registry.PROVIDERS]
    assert len(ids) == len(set(ids))


def test_every_provider_id_is_a_litellm_provider_or_a_native_vendor():
    pytest.importorskip("litellm")
    known = registry.litellm_providers() | handlers.native_vendors()
    unknown = [p.id for p in registry.PROVIDERS if p.id not in known]
    assert not unknown, unknown
    assert registry.native_vendors() == handlers.native_vendors()


def test_the_litellm_read_finds_the_openai_like_providers():
    """``sarvam`` is a JSON provider, not in ``provider_list``. A read of the
    list alone would call Sarvam chat uncallable."""
    pytest.importorskip("litellm")
    known = registry.litellm_providers()
    assert {"anthropic", "gemini", "groq", "deepseek", "assemblyai"} <= known
    assert "sarvam" in known
    assert "sarvam.ai" not in known


def test_the_sarvam_and_assemblyai_providers_have_no_list():
    assert registry.provider("sarvam").no_list is True
    assert registry.provider("assemblyai").no_list is True
    assert registry.provider("anthropic").no_list is False


# ── No api_base, no network, no secret ──────────────────────────────────────


def test_the_registry_holds_no_api_base():
    for cls in (registry.RegistryProvider, registry.RegistryModel, registry.KeyCheck):
        assert "api_base" not in {f.name for f in dataclasses.fields(cls)}
    sarvam = registry.provider("sarvam")
    # The key-check host is the speech handler's host, with its own path.
    assert sarvam.key_check.url == stt_sarvam.SARVAM_API_BASE + stt_sarvam.SARVAM_PATH


def test_every_key_check_and_key_page_is_https():
    for entry in registry.PROVIDERS:
        assert entry.key_check.kind in {"list", "call"}
        assert entry.key_check.url.startswith("https://"), entry.id
        assert entry.key_url.startswith("https://"), entry.id


def test_the_module_makes_no_network_call():
    src = SRC.read_text(encoding="utf-8")
    for forbidden in ("import httpx", "import requests", "urllib.request", "socket"):
        assert forbidden not in src, forbidden


def test_the_wire_counts_the_exact_id_only():
    wire = {p["id"]: p for p in registry.wire({"anthropic": "main", "sarvam.ai": "old"})}
    assert wire["anthropic"]["connected"] is True
    assert wire["anthropic"]["credential_label"] == "main"
    # A key under the alias does not make Sarvam connected.
    assert wire["sarvam"]["connected"] is False
    assert wire["sarvam"]["credential_label"] is None
    assert wire["sarvam"]["jobs"] == ["chat", "transcribe"]
    for entry in wire.values():
        assert set(entry) == {
            "id", "label", "aliases", "key_url", "no_list", "connected",
            "credential_label", "jobs", "models",
        }
