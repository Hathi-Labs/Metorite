"""The provider registry: the vendors and the models the Console offers (MS-1).

Spec: ``project-docs/specs/customer_console.md`` §6A.18 "Model setup in one
step". Fence: ``tests/unit/test_provider_registry.py``.

🔴 **One registry, in the Console.** The operator console reads it through
``GET /catalog/providers``. No second copy exists in TypeScript.
``providerGuides.ts`` folds into it in MS-4.

🔴 **An alias applies only in the Console's own write paths** (the connect id,
the H2 fix and H6). The Router never applies an alias. A model id and its key
carry the same exact provider string, so an alias cannot make the key of one
provider serve the model of another. Do not call :func:`normalise` from
``router.py``.

⚠️ **(K) items.** A model or a key check that came from model knowledge, and
not from a document the build read, carries ``verified=False``. The live list
at connect time (MS-2) confirms or hides it. No test here makes a network call.

⚠️ **No ``api_base`` for Sarvam.** The speech handler adds ``/speech-to-text``
to the host, so a stored ``/v1`` base breaks speech. This module holds no
``api_base`` for any provider. The key-check host comes from here only.

**Pure data and pure functions.** No SQL, no network, no secret.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any

from customer_console.stt_sarvam import DEFAULT_MODE as SARVAM_DEFAULT_MODE

__all__ = [
    "ALIASES",
    "JOB_LABELS",
    "JOB_VERBS",
    "PROVIDERS",
    "RETIRES_WITHIN_DAYS",
    "TRY_JOBS",
    "KeyCheck",
    "RegistryModel",
    "RegistryProvider",
    "alias_target",
    "closest",
    "default_model",
    "extension_of",
    "is_callable_provider",
    "is_retiring",
    "litellm_providers",
    "native_vendors",
    "normalise",
    "offer_refusal",
    "provider",
    "registry_model",
    "single_litellm_verb",
    "vendor_id",
    "wire",
]

#: The words on screen for each job (rule 5). The key is the ``task`` slug.
#: Image understanding is the task ``vision`` on ``tier-vision`` (migration
#: 016), never ``chat``.
JOB_LABELS: dict[str, str] = {
    "chat": "Chat",
    "vision": "Image understanding",
    "transcribe": "Speech to text",
    "speak": "Speech out",
    "image": "Image creation",
    "embed": "Embeddings",
    "decide": "Decisions",
}

#: The verbs each job may take: the jobs table of §6A.18. Rule H1 reads it.
#: Each verb is in ``catalog.KNOWN_INVOCATIONS`` and passes
#: ``catalog.check_invocation_for_task`` for its job (the fence checks both).
JOB_VERBS: dict[str, frozenset[str]] = {
    "chat": frozenset({"acompletion"}),
    "vision": frozenset({"acompletion"}),
    "transcribe": frozenset({"atranscription", "native_sarvam", "native_assemblyai"}),
    "speak": frozenset({"aspeech"}),
    "image": frozenset({"aimage_generation"}),
    "embed": frozenset({"aembedding"}),
    "decide": frozenset({"native_typesafe", "native_aimlapi"}),
}

#: The prefix of every native verb (``catalog.NATIVE_INVOCATION_PREFIX``).
_NATIVE = "native_"


@dataclass(frozen=True)
class KeyCheck:
    """How the connect flow (MS-2) proves a key works.

    ``kind`` is ``list`` for a list-models call, or ``call`` for one cheap
    request. ``url`` is the full address. No operator input changes it.
    ``customer_console.key_check`` reads it, and nothing else sends a key
    to this address.
    """

    kind: str
    url: str
    verified: bool
    #: How the key travels: ``bearer`` (``Authorization: Bearer``), ``token``
    #: (``Authorization: Token``), or ``header:<name>`` (the bare key in that
    #: header).
    auth: str = "bearer"
    #: Fixed headers the vendor needs, for example the Anthropic version.
    headers: tuple[tuple[str, str], ...] = ()
    #: ``GET``, or ``POST`` for a call that sends the bundled clip.
    method: str = "GET"
    #: The form fields of a ``POST`` that sends the clip.
    form: tuple[tuple[str, str], ...] = ()
    #: Where a list call keeps the model ids: ``data.id`` (the OpenAI shape),
    #: ``models.name`` (Gemini, with ``models/`` removed), ``deepgram`` or
    #: ``elevenlabs``.
    ids: str = "data.id"


@dataclass(frozen=True)
class RegistryModel:
    """One curated model for one job."""

    #: The exact Console id, for example ``sarvam/saaras:v4/translit``.
    id: str
    label: str
    #: A ``task`` slug. ``vision`` is Image understanding.
    job: str
    #: A member of ``catalog.KNOWN_INVOCATIONS``.
    invocation: str
    streams: bool = False
    languages: tuple[str, ...] = ()
    price_hint: str = ""
    #: The jobs this model is the default for, inside its own provider.
    recommended_for: tuple[str, ...] = ()
    retires: date | None = None
    #: False for a (K) item the build could not confirm. Never a badge.
    verified: bool = True
    #: The registry id that takes over when this model retires (rule H7).
    successor: str | None = None


@dataclass(frozen=True)
class RegistryProvider:
    """One vendor: its id, the other spellings of the id, and its models."""

    #: The litellm provider id, or a native vendor id. The Router finds the
    #: key by this exact string.
    id: str
    label: str
    aliases: tuple[str, ...]
    key_url: str
    key_check: KeyCheck
    models: tuple[RegistryModel, ...] = field(default_factory=tuple)
    #: True when the vendor publishes no model list (Sarvam, AssemblyAI).
    no_list: bool = False
    #: The mode a handler adds to an id that names none, or None. Sarvam
    #: reads the mode from the id (`stt_sarvam.DEFAULT_MODE`). Rule H4 writes
    #: the curated id that names it.
    default_mode: str | None = None


def _both(model_id: str, label: str, *, price: str = "", recommended: bool = False,
          verified: bool = True, retires: date | None = None,
          successor: str | None = None) -> tuple[RegistryModel, RegistryModel]:
    """One model offered for Chat and for Image understanding.

    Chat streams. ``vision`` does not (``catalog.STREAMABLE_TASKS``).
    """
    return (
        RegistryModel(model_id, label, "chat", "acompletion", streams=True, price_hint=price,
                      recommended_for=("chat",) if recommended else (), verified=verified,
                      retires=retires, successor=successor),
        RegistryModel(model_id, label, "vision", "acompletion", streams=False, price_hint=price,
                      recommended_for=("vision",) if recommended else (), verified=verified,
                      retires=retires, successor=successor),
    )


#: The day the Gemini 2.5 ids retire (§6A.18 "The first providers").
_GEMINI_25_RETIRES = date(2026, 12, 1)


_SARVAM_PRICE = "about INR 0.49 per minute"

#: The first providers and models (§6A.18 "The first providers and models").
#: The key pages come from ``providerGuides.ts``, which MS-4 folds in here.
PROVIDERS: tuple[RegistryProvider, ...] = (
    RegistryProvider(
        id="sarvam",
        label="Sarvam AI",
        aliases=("sarvam.ai",),
        key_url="https://indus.sarvam.ai/key-management",
        # No list call. One short request proves the key (MS-2). The host is
        # the speech handler's own (`stt_sarvam.SARVAM_API_BASE`).
        key_check=KeyCheck(
            "call", "https://api.sarvam.ai/speech-to-text", verified=True,
            auth="header:api-subscription-key", method="POST",
            form=(("model", "saaras:v4"), ("mode", SARVAM_DEFAULT_MODE)),
        ),
        no_list=True,
        default_mode=SARVAM_DEFAULT_MODE,
        models=(
            RegistryModel(
                "sarvam/saaras:v4/translit", "Saaras v4, Hinglish in Roman script",
                "transcribe", "native_sarvam", languages=("hi", "en"),
                price_hint=_SARVAM_PRICE, recommended_for=("transcribe",),
            ),
            RegistryModel(
                "sarvam/saaras:v4/codemix", "Saaras v4, mixed script",
                "transcribe", "native_sarvam", languages=("hi", "en"),
                price_hint=_SARVAM_PRICE,
            ),
            RegistryModel(
                "sarvam/saaras:v3/translit", "Saaras v3, Hinglish in Roman script",
                "transcribe", "native_sarvam", languages=("hi", "en"),
                price_hint=_SARVAM_PRICE,
            ),
            # (K). litellm knows `sarvam` as an OpenAI-like chat provider.
            RegistryModel(
                "sarvam/sarvam-105b", "Sarvam 105B", "chat", "acompletion",
                streams=True, verified=False,
            ),
        ),
    ),
    RegistryProvider(
        id="assemblyai",
        label="AssemblyAI",
        aliases=("assembly.ai",),
        key_url="https://www.assemblyai.com/dashboard/signup",
        key_check=KeyCheck(
            "call", "https://api.assemblyai.com/v2/transcript?limit=1", verified=False,
            auth="header:authorization",
        ),
        no_list=True,
        models=(
            # The async v2 models (`nano`, `best`) are not offered: the door
            # cannot call them.
            #
            # 🔴 **`/en` is the default, not `hi,en`** (production, 2026-11-09,
            # about 07:02 UTC). `/catalog/transcribe/try` sent an 8 s ENGLISH
            # clip. `hi,en` answered in Devanagari script. `/en` and the id
            # with no languages answered in clean English, in about 1.2 s.
            # Production `tier-stt` is `[assemblyai/universal-3-5-pro/en]`.
            RegistryModel(
                "assemblyai/universal-3-5-pro/en", "Universal 3.5 Pro, English",
                "transcribe", "native_assemblyai", languages=("en",),
                recommended_for=("transcribe",),
            ),
            RegistryModel(
                "assemblyai/universal-3-5-pro/hi,en",
                "Universal 3.5 Pro, Hindi and English (writes Hindi in Devanagari script)",
                "transcribe", "native_assemblyai", languages=("hi", "en"),
            ),
        ),
    ),
    RegistryProvider(
        id="anthropic",
        label="Anthropic",
        aliases=("claude",),
        key_url="https://console.anthropic.com/settings/keys",
        key_check=KeyCheck(
            "list", "https://api.anthropic.com/v1/models?limit=1000", verified=False,
            auth="header:x-api-key", headers=(("anthropic-version", "2023-06-01"),),
        ),
        models=(
            *_both("anthropic/claude-haiku-5-5", "Claude Haiku 5.5",
                   price="about $0.10 in and $0.50 out per million tokens",
                   recommended=True),
            *_both("anthropic/claude-sonnet-5-5", "Claude Sonnet 5.5",
                   price="about $2 in and $10 out per million tokens"),
            *_both("anthropic/claude-opus-5-5", "Claude Opus 5.5",
                   price="about $4 in and $20 out per million tokens"),
        ),
    ),
    RegistryProvider(
        id="gemini",
        label="Google Gemini",
        aliases=("google",),
        key_url="https://aistudio.google.com/apikey",
        key_check=KeyCheck(
            "list", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
            verified=False, auth="header:x-goog-api-key", ids="models.name",
        ),
        models=(
            *_both("gemini/gemini-3.6-flash", "Gemini 3.6 Flash", verified=False),
            *_both("gemini/gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", verified=False),
            *_both("gemini/gemini-3.1-pro-preview", "Gemini 3.1 Pro preview", verified=False),
            # Rule H7 (MS-2). The spec table says the 2.5 ids retire on
            # 2026-12-01. They stay here so a chain that still names one gets
            # an entry and a fix. The connect flow never offers them.
            *_both("gemini/gemini-2.5-flash", "Gemini 2.5 Flash", verified=False,
                   retires=_GEMINI_25_RETIRES, successor="gemini/gemini-3.6-flash"),
            *_both("gemini/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", verified=False,
                   retires=_GEMINI_25_RETIRES, successor="gemini/gemini-3.5-flash-lite"),
            *_both("gemini/gemini-2.5-pro", "Gemini 2.5 Pro", verified=False,
                   retires=_GEMINI_25_RETIRES, successor="gemini/gemini-3.1-pro-preview"),
            RegistryModel("gemini/gemini-3.1-flash-image", "Gemini 3.1 Flash Image",
                          "image", "aimage_generation", verified=False),
            RegistryModel("gemini/gemini-embedding-2", "Gemini Embedding 2",
                          "embed", "aembedding", verified=False),
            RegistryModel("gemini/gemini-3.8-flash-tts", "Gemini 3.8 Flash speech",
                          "speak", "aspeech", verified=False),
        ),
    ),
    RegistryProvider(
        id="openai",
        label="OpenAI",
        aliases=(),
        key_url="https://platform.openai.com/api-keys",
        key_check=KeyCheck("list", "https://api.openai.com/v1/models", verified=False),
        models=(
            RegistryModel("openai/gpt-4o-transcribe", "GPT-4o Transcribe",
                          "transcribe", "atranscription", verified=False),
            RegistryModel("openai/whisper-1", "Whisper 1",
                          "transcribe", "atranscription", verified=False),
            RegistryModel("openai/tts-1", "TTS 1", "speak", "aspeech", verified=False),
        ),
    ),
    RegistryProvider(
        id="groq",
        label="Groq",
        aliases=(),
        key_url="https://console.groq.com/keys",
        key_check=KeyCheck("list", "https://api.groq.com/openai/v1/models", verified=False),
        models=(
            RegistryModel("groq/whisper-large-v3-turbo", "Whisper Large v3 Turbo",
                          "transcribe", "atranscription",
                          price_hint="about $0.04 per hour", verified=False),
            # The id has two slashes. The registry removes the need to type it.
            RegistryModel("groq/openai/gpt-oss-120b", "GPT-OSS 120B",
                          "chat", "acompletion", streams=True, verified=False),
        ),
    ),
    RegistryProvider(
        id="deepseek",
        label="DeepSeek",
        aliases=(),
        key_url="https://platform.deepseek.com/api-keys",
        key_check=KeyCheck("list", "https://api.deepseek.com/models", verified=False),
        models=(
            # No stable vision model, so Chat only.
            RegistryModel("deepseek/deepseek-flash", "DeepSeek Flash",
                          "chat", "acompletion", streams=True, verified=False),
            RegistryModel("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro",
                          "chat", "acompletion", streams=True, verified=False),
        ),
    ),
    RegistryProvider(
        id="elevenlabs",
        label="ElevenLabs",
        aliases=(),
        key_url="https://elevenlabs.io/app/settings/api-keys",
        key_check=KeyCheck(
            "list", "https://api.elevenlabs.io/v1/models", verified=False,
            auth="header:xi-api-key", ids="elevenlabs",
        ),
        models=(
            # Speech out needs a voice id. Not offered until a voice setting exists.
            RegistryModel("elevenlabs/scribe_v2", "Scribe v2",
                          "transcribe", "atranscription", verified=False),
        ),
    ),
    RegistryProvider(
        id="deepgram",
        label="Deepgram",
        aliases=(),
        key_url="https://console.deepgram.com/signup",
        key_check=KeyCheck(
            "list", "https://api.deepgram.com/v1/models", verified=False,
            auth="token", ids="deepgram",
        ),
        models=(
            RegistryModel("deepgram/nova-3", "Nova 3",
                          "transcribe", "atranscription", verified=False),
        ),
    ),
    # Chat (K). The spec names no model id for these two. The live list at
    # connect time (MS-2) shows the models, so none is typed here.
    RegistryProvider(
        id="mistral",
        label="Mistral",
        aliases=(),
        key_url="https://console.mistral.ai/api-keys/",
        key_check=KeyCheck("list", "https://api.mistral.ai/v1/models", verified=False),
    ),
    RegistryProvider(
        id="openrouter",
        label="OpenRouter",
        aliases=(),
        key_url="https://openrouter.ai/settings/keys",
        # The model list is public, so it proves no key. This call reads the key.
        key_check=KeyCheck("call", "https://openrouter.ai/api/v1/key", verified=False),
    ),
)

_BY_ID: dict[str, RegistryProvider] = {p.id: p for p in PROVIDERS}

#: Each alias and the provider id it names. Read from :data:`PROVIDERS`, so
#: an alias is typed once. The fence checks that no alias collides.
ALIASES: dict[str, str] = {alias: p.id for p in PROVIDERS for alias in p.aliases}


def normalise(provider_id: str | None) -> str:
    """Lowercase, strip every space, then look the text up in :data:`ALIASES`.

    ``sarvam.ai`` becomes ``sarvam``. An unknown value comes back cleaned and
    otherwise unchanged, so the "Something else" card still works.
    """
    cleaned = "".join((provider_id or "").split()).lower()
    return ALIASES.get(cleaned, cleaned)


def provider(provider_id: str) -> RegistryProvider | None:
    """The registry entry for this exact id, or None. No alias is applied."""
    return _BY_ID.get(provider_id)


def _prefix(model_id: str) -> str:
    return model_id.partition("/")[0]


def registry_model(model_id: str, job: str) -> RegistryModel | None:
    """The curated model with this exact id for this job, or None."""
    entry = _BY_ID.get(_prefix(model_id))
    if entry is None:
        return None
    return next((m for m in entry.models if m.id == model_id and m.job == job), None)


def default_model(provider_id: str, job: str) -> RegistryModel | None:
    """The default model of one provider for one job, or None.

    The model whose ``recommended_for`` names the job wins. Otherwise the
    first model the provider lists for the job. Rule H4 reads it.
    """
    entry = _BY_ID.get(provider_id)
    if entry is None:
        return None
    offered = [m for m in entry.models if m.job == job]
    return next((m for m in offered if job in m.recommended_for), offered[0] if offered else None)


def extension_of(model_id: str, job: str) -> RegistryModel | None:
    """The curated model that names the default mode of *model_id*, or None.

    ``sarvam/saaras:v4`` names no mode, and the handler adds ``translit``. So
    the curated id is ``sarvam/saaras:v4/translit``. Only a provider with a
    ``default_mode`` has one. An AssemblyAI id with no languages is a
    different model (no steering), so it has none.
    """
    entry = _BY_ID.get(_prefix(model_id))
    if entry is None or entry.default_mode is None:
        return None
    return registry_model(f"{model_id}/{entry.default_mode}", job)


@lru_cache(maxsize=1)
def litellm_providers() -> frozenset[str]:
    """Every provider id litellm answers to, read from litellm itself.

    Two sources: ``litellm.provider_list``, and the OpenAI-like providers that
    litellm loads from ``providers.json`` (``sarvam`` is one of them).
    Imported here, on the first call, because the import is slow.
    """
    import litellm

    known = {getattr(p, "value", p) for p in litellm.provider_list}
    try:
        from litellm.llms.openai_like.json_loader import JSONProviderRegistry
    except ImportError:  # pragma: no cover - an older litellm has no JSON registry
        return frozenset(str(p) for p in known)
    known |= set(JSONProviderRegistry.list_providers())
    return frozenset(str(p) for p in known)


def native_vendors() -> frozenset[str]:
    """The credential prefixes the native handlers answer to.

    Read from ``handlers.NATIVE_HANDLERS``, so no second vendor list exists.
    """
    from customer_console import handlers

    return handlers.native_vendors()


def is_callable_provider(provider_id: str) -> bool:
    """True when a native handler or litellm answers to this exact id (rule H2)."""
    return provider_id in native_vendors() or provider_id in litellm_providers()


#: The jobs the connect flow offers (MS-2). Each one has a single-model Try
#: in ``main.py``: platform key, one audit row, no ``usage_event`` row. A job
#: with no Try is not offered (§6A.18 "The Try helpers"). Image creation,
#: embeddings and speech out have no Try yet, so adopt refuses them.
TRY_JOBS: frozenset[str] = frozenset({"chat", "vision", "transcribe"})

#: How near a ``retires`` date makes a model retiring (rule H7).
RETIRES_WITHIN_DAYS = 30


def is_retiring(model: RegistryModel, today: date) -> bool:
    """True when the model retires within 30 days, or has retired (rule H7)."""
    return model.retires is not None and (model.retires - today).days <= RETIRES_WITHIN_DAYS


def offer_refusal(model: RegistryModel, today: date) -> str | None:
    """Why the connect flow does not offer this model, or None when it does."""
    if model.job not in TRY_JOBS:
        return f"Metorite has no test for {JOB_LABELS.get(model.job, model.job)} yet"
    if is_retiring(model, today):
        return f"This model retires on {model.retires.isoformat()}"
    return None


def alias_target(provider_id: str) -> str | None:
    """The registry provider that *provider_id* is a spelling of, or None (H6).

    ``sarvam.ai`` gives ``sarvam``. An exact registry id, an unknown id, and
    a spelling that a handler or litellm can call give None. A key under a
    callable id is not misplaced, so H6 never moves it.
    """
    target = normalise(provider_id)
    if target == provider_id or target not in _BY_ID:
        return None
    if is_callable_provider(provider_id):
        return None
    return target


def closest(provider_id: str) -> str | None:
    """The registry id nearest to *provider_id*, for a 400 that names it."""
    import difflib

    names = [*_BY_ID, *ALIASES]
    hit = difflib.get_close_matches(normalise(provider_id), names, n=1, cutoff=0.5)
    return None if not hit else ALIASES.get(hit[0], hit[0])


def vendor_id(model_id: str) -> str:
    """The id after the provider prefix: what a vendor's model list names."""
    return model_id.partition("/")[2]


def single_litellm_verb(job: str) -> str | None:
    """The one litellm verb of a job, or None when it has none or several.

    Rule H1 offers it as the fix for a litellm provider that the registry
    does not list.
    """
    verbs = [v for v in JOB_VERBS.get(job, ()) if not v.startswith(_NATIVE)]
    return verbs[0] if len(verbs) == 1 else None


def _model_wire(m: RegistryModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "label": m.label,
        "job": m.job,
        "job_label": JOB_LABELS.get(m.job, m.job),
        # Raw form. The screen shows it under "Advanced" only (MS-3).
        "invocation": m.invocation,
        "streams": m.streams,
        "languages": list(m.languages),
        "price_hint": m.price_hint,
        "recommended_for": list(m.recommended_for),
        "retires": None if m.retires is None else m.retires.isoformat(),
        "successor": m.successor,
        "verified": m.verified,
    }


def wire(labels: dict[str, str | None]) -> list[dict[str, Any]]:
    """The registry as ``GET /catalog/providers`` returns it.

    *labels* maps a provider id to the label of its live PLATFORM key
    (``organization_id IS NULL``). The id must match exactly, so a key under
    an alias does not make the provider connected. No secret is read here.
    """
    return [
        {
            "id": p.id,
            "label": p.label,
            "aliases": list(p.aliases),
            "key_url": p.key_url,
            "no_list": p.no_list,
            "connected": p.id in labels,
            "credential_label": labels.get(p.id),
            "jobs": sorted({m.job for m in p.models}, key=list(JOB_LABELS).index),
            "models": [_model_wire(m) for m in p.models],
        }
        for p in PROVIDERS
    ]
