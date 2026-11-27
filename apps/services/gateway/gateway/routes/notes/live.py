"""Live transcription — mint a short-lived streaming token for the browser.

Live captions stream over a WebSocket. To keep the gateway out of the audio path
(spec §7 D7: "gateway stays SSE-only; it mints tokens, the WS terminates
elsewhere"), the browser connects to the STT provider directly — but with an
EPHEMERAL, short-lived token minted here from the long-lived API key, never the
real key. The authoritative transcript is still the batch re-pass on stop; live
is a fast draft.

Two providers, chosen automatically:

- **AssemblyAI** (preferred): diarizes on the live stream too, handles Hindi/
  English code-switching, and is the cheaper of the two per hour. Token:
  ``GET /v3/token`` on the streaming host, authed with the raw API key.
- **Deepgram** (fallback): mints a usage-scoped ephemeral key via its
  key-management API — which means the master key needs that scope.

Selection follows the configured STT tier when it names a provider we can stream
(so "everything via AssemblyAI" needs no second setting), otherwise it falls back
to whichever key is present. The response tells the browser which protocol to
speak, so adding a provider later doesn't change the endpoint's shape.

Everything is best-effort and gated: with no usable key this returns 503 and the
recorder silently falls back to chunked-upload → batch — live captions just don't
appear. Spec: note_taker_app.md §5.2 / §6 (live captions).
"""

from __future__ import annotations

import os

import httpx
from acb_auth import UserContext, get_current_user
from fastapi import Depends, Header, HTTPException
from gateway.routes.notes.core import _log, _tenant_session, load_owned_meeting, router
from pydantic import BaseModel

_DG_API = "https://api.deepgram.com/v1"
_AAI_STREAM = "https://streaming.assemblyai.com/v3"
_TOKEN_TTL_S = 60  # short — the browser refreshes if a recording runs long


def _deepgram_key() -> str:
    """The long-lived Deepgram master key. Both the startup key-load
    (``key_store.configure_litellm``) and the Settings key-save
    (``_inject_env_into_litellm``) write it to ``os.environ``, so that's the
    single canonical source."""
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def _assemblyai_key() -> str:
    return os.environ.get("ASSEMBLYAI_API_KEY", "").strip()


def _configured_stt_model() -> str:
    """The concrete model behind the ``tier-stt`` alias ('' if unresolvable)."""
    try:
        from acb_llm.context import resolve_underlying_model

        return (resolve_underlying_model("tier-stt") or "").lower()
    except Exception:
        return ""


def choose_provider(model: str, has_aai: bool, has_dg: bool) -> str | None:
    """Which provider should serve live captions (pure, so it's testable).

    Follow the configured STT tier when that provider can stream and is keyed —
    so picking an AssemblyAI model for batch gets you AssemblyAI live too, with
    no second setting. Otherwise use whichever key exists, preferring AssemblyAI
    (cheaper, and it diarizes live). ``None`` → live isn't available.
    """
    if model.startswith("assemblyai/") and has_aai:
        return "assemblyai"
    if model.startswith("deepgram/") and has_dg:
        return "deepgram"
    if has_aai:
        return "assemblyai"
    if has_dg:
        return "deepgram"
    return None


def _live_model(provider: str) -> str:
    """The streaming model id the browser should request."""
    if provider == "assemblyai":
        # AssemblyAI's streaming models are a SEPARATE family from the batch
        # ones — the valid ids are `universal-streaming-english`,
        # `universal-streaming-multilingual` and `u3-rt-pro`. A batch id like
        # "universal-2" is not a streaming model at all, so picking it in
        # Settings -> Models never affected live transcription.
        #
        # Default to u3-rt-pro (Universal-3 Pro Streaming) because it is the
        # model that supports live speaker diarization; the plain
        # english-streaming model does not, and the symptom is a working
        # transcript with every voice merged into one speaker rather than an
        # error. Override per-deployment if the cost tradeoff isn't worth it —
        # live captions still work on the others, just without speakers.
        return os.environ.get("ASSEMBLYAI_LIVE_MODEL", "u3-rt-pro").strip()
    resolved = _configured_stt_model()
    if resolved.startswith("deepgram/"):
        return resolved.split("/", 1)[1]
    # Whisper can't stream, so live always uses a Deepgram model regardless of
    # what the batch tier is set to.
    return "nova-3"


class LiveToken(BaseModel):
    provider: str       # 'assemblyai' | 'deepgram' — which protocol to speak
    token: str          # ephemeral credential (never the master key)
    model: str          # provider model id ('' → provider default)
    expires_in: int     # seconds


async def _mint_assemblyai_token(api_key: str) -> str:
    """Short-lived AssemblyAI streaming token. The API key goes in a raw
    ``authorization`` header (NOT ``Bearer``)."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(
            f"{_AAI_STREAM}/token",
            params={"expires_in_seconds": _TOKEN_TTL_S},
            headers={"authorization": api_key},
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise RuntimeError("AssemblyAI returned no token")
        return str(token)


async def _mint_ephemeral_key(api_key: str) -> str:
    """Create a ~1-minute usage-scoped Deepgram key from the master key.

    Deepgram keys are project-scoped, so we discover the project first (or use
    DEEPGRAM_PROJECT_ID). Requires the master key to have key-management scope;
    if it doesn't, Deepgram returns 401/403 and we surface a 503.
    """
    headers = {"Authorization": f"Token {api_key}"}
    async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
        project_id = os.environ.get("DEEPGRAM_PROJECT_ID", "").strip()
        if not project_id:
            r = await client.get(f"{_DG_API}/projects")
            r.raise_for_status()
            projects = r.json().get("projects", [])
            if not projects:
                raise RuntimeError("Deepgram account has no projects")
            project_id = projects[0]["project_id"]

        r = await client.post(
            f"{_DG_API}/projects/{project_id}/keys",
            json={
                "comment": "metorite-notes-live (ephemeral)",
                "scopes": ["usage:write"],
                "time_to_live_in_seconds": _TOKEN_TTL_S,
            },
        )
        r.raise_for_status()
        key = r.json().get("key")
        if not key:
            raise RuntimeError("Deepgram returned no key")
        return str(key)


async def _issue_live_token() -> LiveToken:
    """Mint streaming credentials for whichever provider is configured.

    Shared by the browser recorder and the meeting-bot worker so both get live
    captions from the same key you set in Settings -> Models. The worker can't
    read that key (it runs in its own container), and shipping the master key
    into the container would be both a duplicate source of truth and a wider
    blast radius — so it asks for a short-lived token instead."""
    aai, dg = _assemblyai_key(), _deepgram_key()
    provider = choose_provider(_configured_stt_model(), bool(aai), bool(dg))
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="Live captions need a streaming speech-to-text key — add an "
            "AssemblyAI (recommended) or Deepgram key in Settings → Models. "
            "Recording still works without it.",
        )
    try:
        token = (
            await _mint_assemblyai_token(aai)
            if provider == "assemblyai"
            else await _mint_ephemeral_key(dg)
        )
    except Exception as exc:
        _log.warning(
            "notes.live_token_failed", provider=provider, error=str(exc)[:200]
        )
        hint = (
            "the AssemblyAI key may be invalid"
            if provider == "assemblyai"
            else "the Deepgram key may lack key-management scope"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Could not start live captions ({hint}). Recording continues "
            "without them.",
        ) from exc
    model = _live_model(provider)
    _log.info("notes.live_token_minted", provider=provider, model=model or "default")
    return LiveToken(
        provider=provider, token=token, model=model, expires_in=_TOKEN_TTL_S
    )


_LIVE_COPILOT_SQL = (
    "SELECT copilot_enabled FROM live_session "
    "WHERE meeting_id = CAST(:m AS UUID) AND status = 'live' "
    "ORDER BY started_at DESC LIMIT 1"
)


async def live_wanted(meeting_id: str, db=None) -> tuple[bool, str]:
    """Should this meeting be streaming right now, and why/why not.

    Streaming ASR is billed per minute for the whole meeting; the archival
    recording produces the same transcript afterwards at batch prices. So with
    nothing reading the stream in real time, it is pure spend. Never raises —
    if we can't tell, allow it, because breaking live captions is worse than an
    unexpected bill on one meeting.

    The member route passes its tenant-bound session ``db`` (task 3966). The
    bot-token route passes none."""
    try:
        from gateway.routes.notes.core import _get_db
        from gateway.routes.notes.settings import (
            load_for_meeting,
            wants_live_transcription,
        )
        from sqlalchemy import text as _text

        settings, _ = await load_for_meeting(meeting_id, db)
        if db is not None:
            row = (
                await db.execute(_text(_LIVE_COPILOT_SQL), {"m": meeting_id})
            ).fetchone()
        else:
            # H4/H6: service-identity route — `read_live_wanted` is called by
            # the meeting-bot worker (MEETING_BOT_TOKEN, `system`-shaped
            # identity, no ambient tenant); derive the tenant from the
            # live_session/meeting row.
            async with await _get_db() as unbound:
                row = (
                    await unbound.execute(_text(_LIVE_COPILOT_SQL), {"m": meeting_id})
                ).fetchone()
        copilot_on = bool(row.copilot_enabled) if row is not None else False
        if wants_live_transcription(settings.live_transcription, copilot_on):
            return True, "wanted"
        return False, (
            "the copilot is off for this meeting — recording continues and the "
            "transcript is written when it ends"
            if settings.live_transcription == "auto"
            else "live transcription is switched off in Note Taker settings"
        )
    except Exception as exc:
        _log.warning("notes.live_wanted_failed", error=str(exc)[:200])
        return True, "unknown"


class LiveWanted(BaseModel):
    wanted: bool
    reason: str


@router.get("/meetings/{meeting_id}/live/wanted", response_model=LiveWanted)
async def read_live_wanted(
    meeting_id: str,
    authorization: str | None = Header(default=None),
) -> LiveWanted:
    """Should the meeting-bot worker stream this meeting. One boolean and a
    settings-derived sentence, behind the bot token.

    ⚠️ **No caller reaches this route today** (task 4120). It is in neither
    ``main.PUBLIC_ROUTES`` nor ``core.router``'s ``exempt`` list. So
    ``require_authenticated`` and then the feature gate answer 401 to the
    worker before ``_check_bot_auth`` runs. The worker stopped calling it in
    task 4120, because its fail-open default turned each 401 into "wanted".

    The route stays gated on purpose. With no member there is no bound
    tenant, so ``live_wanted`` reads with no ``db``, in an unbound session.
    FORCE row level security then hides ``meeting``, ``copilot_config`` and
    ``live_session``. On a fresh connection the read sees zero rows, and the
    answer is the defaults ("auto", copilot off). On a pooled connection the
    tenant setting is ``''``, the uuid cast fails, and the answer is
    ``(True, "unknown")``. Neither is the setting of the meeting (R8 case in
    ``test_notes_meeting_owner_r8.py``). To open the route, first resolve the
    tenant of the meeting through a SECURITY DEFINER read, then read in
    ``tenant_session(org)``.

    **Not member-owner-scoped** (N1, 2026-08-04). The caller is a machine
    with ``MEETING_BOT_TOKEN`` and no member identity. An owner predicate
    would need an owner it does not have. ``live_wanted`` never raises, and
    gives the same answer for a meeting id that does not exist."""
    from gateway.routes.notes.live_transcript import _check_bot_auth

    _check_bot_auth(authorization)
    wanted, reason = await live_wanted(meeting_id)
    return LiveWanted(wanted=wanted, reason=reason)


@router.post("/stt/live-token", response_model=LiveToken)
async def live_token(
    meeting_id: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> LiveToken:
    """Streaming credentials for the browser recorder (user-authenticated).

    With a meeting_id, refuses when nothing is listening — the recorder then
    falls back to record-only, which is exactly the same end result minus the
    per-minute bill.

    The meeting_id is caller-supplied and reads per-meeting state, so it is
    owner-scoped: without the check the 409 reason distinguished "that
    meeting's copilot is off" from "that meeting is live", which is a
    presence oracle over a colleague's calendar. The parameter stays optional
    — a token minted with no meeting names nothing to scope."""
    if meeting_id:
        async with _tenant_session() as db:
            await load_owned_meeting(
                db, meeting_id, user.email, columns="m.id"
            )
            wanted, reason = await live_wanted(meeting_id, db)
        if not wanted:
            raise HTTPException(status_code=409, detail=reason)
    return await _issue_live_token()


@router.post("/stt/bot-live-token", response_model=LiveToken)
async def bot_live_token(
    authorization: str | None = Header(default=None),
) -> LiveToken:
    """The same credentials for the meeting-bot worker (machine-authenticated).

    Separate endpoint rather than a shared one because the callers authenticate
    differently — a browser has a user session, the worker has the shared bot
    token — and a re-fetchable endpoint (rather than a token handed over at
    join time) is what lets a worker reconnect mid-meeting: these tokens live
    about a minute, meetings run hours."""
    from gateway.routes.notes.live_transcript import _check_bot_auth

    _check_bot_auth(authorization)
    return await _issue_live_token()
