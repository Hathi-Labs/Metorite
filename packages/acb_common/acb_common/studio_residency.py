"""Data residency for each org (STU-J10, ``ai_studio.md`` section 14 J10, D202.8).

The owner decided that Metorite hosts health data in India, with a residency
flag for each org. This module is the one place that lists each outbound path
of a health app (a store or a vendor) with its region, and the one guard that
reads the flag.

**The flag.** ``STUDIO_RESIDENCY_ORGS`` is a comma list of org ids. ``*`` opens
every org. Empty opens none. The flag only adds refusals, so an org outside the
list sees no change. The org comes from the session, never from request input
(R5).

**The regions.** ``in`` means the path runs in India and the source says so.
``outside`` means it runs elsewhere. ``unknown`` means the vendor states no
region. With the flag on, only ``in`` passes. A path with no entry fails closed in
:func:`path_allowed`. A row with no region is a defect: :func:`validate_registry`
refuses it.

**What this module does not do.** It moves no store and changes no vendor.
Where a store or a vendor runs is infrastructure, and a move of it is an
OWNER-GATE. A caller that sends a health record out calls :func:`require_path`
first. Fence: ``tests/unit/test_studio_residency.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from acb_common import get_settings

REGION_IN = "in"
REGION_OUTSIDE = "outside"
REGION_UNKNOWN = "unknown"
REGIONS = frozenset({REGION_IN, REGION_OUTSIDE, REGION_UNKNOWN})

Kind = Literal["store", "vendor"]


@dataclass(frozen=True)
class OutboundPath:
    """One place that a record of a health app can go."""

    key: str
    kind: Kind
    region: str | None
    carries_records: bool
    source: str


class ResidencyRefusal(Exception):
    """A path that the residency flag refuses. ``str(exc)`` is one sentence."""


#: Each outbound path of a health app. A new store or vendor path adds a row
#: here in the same pull request, and the fence refuses a row with no region.
HEALTH_OUTBOUND_PATHS: tuple[OutboundPath, ...] = (
    OutboundPath("postgres", "store", REGION_IN, True,
                 "HANDOFF.md (Mumbai is live, pm_tasks through the ap-south-1 pooler); work_plan.md section 2 R-f"),
    OutboundPath("object_store", "store", REGION_UNKNOWN, True,
                 "settings.object_store_region defaults to us-east-1 and the endpoint is free. "
                 "The Mumbai bucket is MT-1g-2, not built. Set to in when it lands"),
    OutboundPath("redis", "store", REGION_UNKNOWN, True,
                 "The box region is not recorded. It holds chat stream entries and attachment bytes"),
    OutboundPath("llm_router_decide", "vendor", REGION_UNKNOWN, True,
                 "work_plan.md D75, D19.6: TypeSafe states no region"),
    OutboundPath("llm_anthropic", "vendor", REGION_OUTSIDE, True,
                 "settings.anthropic_api_key: the public Claude API"),
    OutboundPath("llm_openrouter", "vendor", REGION_OUTSIDE, True,
                 "settings.openrouter_api_key: the public OpenRouter API"),
    OutboundPath("embeddings_openai", "vendor", REGION_OUTSIDE, True,
                 "settings.email_embedding_model: text-embedding-3-small"),
    OutboundPath("llm_gemini", "vendor", REGION_OUTSIDE, True, "settings.gemini_api_key: the public Gemini API"),
    OutboundPath("llm_groq", "vendor", REGION_OUTSIDE, True, "settings.groq_api_key: the public Groq API"),
    OutboundPath("email_smtp", "vendor", REGION_UNKNOWN, True, "settings.smtp_host: the host is free text"),
    OutboundPath("channel_whatsapp", "vendor", REGION_UNKNOWN, True,
                 "settings.whatsapp_assistant_access_token: the Cloud API states no region"),
    OutboundPath("web_push", "vendor", REGION_UNKNOWN, True,
                 "settings.vapid_*: the push service of the browser gets notification text"),
    OutboundPath("llm_openai", "vendor", REGION_OUTSIDE, True, "settings.openai_api_key: the public OpenAI API"),
    OutboundPath("llm_deepseek", "vendor", REGION_UNKNOWN, True, "settings.deepseek_api_key: no region stated"),
    OutboundPath("llm_mistral", "vendor", REGION_UNKNOWN, True, "settings.mistral_api_key: no region stated"),
    OutboundPath("llm_together", "vendor", REGION_UNKNOWN, True, "settings.together_api_key: no region stated"),
    OutboundPath("speech_to_text", "vendor", REGION_UNKNOWN, True,
                 "AssemblyAI: customer_console/stt_assemblyai.py gives no region"),
    OutboundPath("speech_to_text_sarvam", "vendor", REGION_UNKNOWN, True,
                 "customer_console/stt_sarvam.py: no region is recorded here. Set to in only with a source"),
    OutboundPath("speech_deepgram_elevenlabs", "vendor", REGION_UNKNOWN, True,
                 "customer_console/provider_registry.py ids deepgram and elevenlabs: no region recorded"),
    OutboundPath("llm_decide_aimlapi", "vendor", REGION_UNKNOWN, True,
                 "customer_console/provider_registry.py native_aimlapi: no region recorded"),
    OutboundPath("meeting_recall", "vendor", REGION_OUTSIDE, True,
                 "gateway/routes/notes/meeting_bot.py: RECALL_REGION defaults to us-east-1"),
    OutboundPath("mail_zoho_gmail", "vendor", REGION_UNKNOWN, True,
                 "settings.zoho_access_token and the Gmail path: the provider region is the account's"),
    OutboundPath("web_search", "vendor", REGION_UNKNOWN, True,
                 "settings.searxng_url and serpapi_api_key: a query can hold record text"),
)

#: Each vendor secret of ``Settings`` and the registry key that covers it.
VENDOR_SETTING_KEYS: dict[str, str] = {
    "anthropic_api_key": "llm_anthropic", "openrouter_api_key": "llm_openrouter",
    "gemini_api_key": "llm_gemini", "groq_api_key": "llm_groq", "openai_api_key": "llm_openai",
    "deepseek_api_key": "llm_deepseek", "mistral_api_key": "llm_mistral",
    "together_api_key": "llm_together", "whatsapp_assistant_access_token": "channel_whatsapp",
    "zoho_access_token": "mail_zoho_gmail", "zoho_refresh_token": "mail_zoho_gmail",
    "gmail_pubsub_token": "mail_zoho_gmail", "serpapi_api_key": "web_search",
}

#: Secrets that open no path for a health record: internal tokens, code hosts,
#: outbound sales tools and maps. A new secret that fits neither table fails
#: ``test_each_vendor_secret_is_covered``.
NOT_A_RECORD_PATH: frozenset[str] = frozenset({
    "gateway_internal_token", "web_worker_token", "mutation_pr_token", "github_token",
    "github_read_token", "anymailfinder_api_key", "apollo_api_key", "instantly_api_key",
    "apify_api_token", "google_maps_api_key",
})

_BY_KEY: dict[str, OutboundPath] = {p.key: p for p in HEALTH_OUTBOUND_PATHS}


def validate_registry(paths: tuple[OutboundPath, ...] = HEALTH_OUTBOUND_PATHS) -> list[str]:
    """The problems of a registry, one sentence each. Empty means it is sound.

    A path with no region, an unknown region, a repeated key, or no source is
    a problem.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p.key in seen:
            problems.append(f"{p.key}: the key is listed twice")
        seen.add(p.key)
        if not p.region:
            problems.append(f"{p.key}: no region")
        elif p.region not in REGIONS:
            problems.append(f"{p.key}: region {p.region!r} is not one of {sorted(REGIONS)}")
        if p.kind not in ("store", "vendor"):
            problems.append(f"{p.key}: kind must be store or vendor")
        if not p.source.strip():
            problems.append(f"{p.key}: no source for the region")
    return problems


def _listed(raw: str) -> frozenset[str]:
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def residency_on(organization_id: str | None) -> bool:
    """True when ``STUDIO_RESIDENCY_ORGS`` holds the org, or holds ``*``.

    A missing org counts as on when the list is not empty (fail closed).
    """
    listed = _listed(str(getattr(get_settings(), "studio_residency_orgs", "") or ""))
    if not organization_id:
        # No org: the flag only adds refusals, so a caller with no org fails
        # closed whenever any org holds the flag.
        return bool(listed)
    return "*" in listed or str(organization_id).strip().lower() in listed


def path_allowed(organization_id: str | None, key: str) -> bool:
    """True when the org may send a health record down this path.

    Flag off: always true, so an org without the flag sees no change. Flag on:
    only a registered path with the region ``in``. An unregistered key fails
    closed.
    """
    if not residency_on(organization_id):
        return True
    path = _BY_KEY.get(key)
    return path is not None and path.region == REGION_IN


def require_path(organization_id: str | None, key: str) -> None:
    """Raise :class:`ResidencyRefusal` when :func:`path_allowed` is false."""
    if not path_allowed(organization_id, key):
        raise ResidencyRefusal(
            f"This org keeps its health data in India, and the path {key!r} is not recorded as in India."
        )
