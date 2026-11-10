"""``/chat/transcribe`` — the chat's voice input (``projects_ai_chat.md`` §31, VOX-1).

The member presses the mic, talks, and presses stop. The browser sends one
16 kHz mono WAV clip. This route sends the clip to the Console's metered
transcribe door, and answers ``{"text": ...}``. The composer puts the text in
the message box at the caret. The chat never sends a transcript by itself.

Two routes:

* ``GET /chat/transcribe`` — whether the composer shows the mic. 404 while
  ``CHAT_VOICE_ORGS`` leaves the org out. Else 200 ``{ready, message,
  max_seconds}``. ``ready`` is false while the Console route is off, and
  ``message`` then holds the sentence the mic shows.
* ``POST /chat/transcribe`` — one clip, as the multipart field ``file``.

**The checks, in this order.** The member and the flag (404). The routing
flag: with ``routing_is_on()`` false the route refuses, because a call off
the Console would not bill. The body cap (10 MB of clip). The kind: WAV only,
a WAV MIME type and a ``.wav`` name (415 for the rest). The length, counted
from the bytes present and not from the header (120 s, with a small
allowance, 413). No frame: 400. The cap of 30 clips for each member in each hour. Then the one Console call.

**Nothing goes to disk.** The route reads the body itself, with a hard cap,
and parses the multipart body in memory. Starlette's own form parser spools
a part above 1 MB to a temporary file, so this route never asks FastAPI for
an ``UploadFile``. The clip goes to the Console from memory, and the route
keeps no copy and logs no text.

**Who pays.** The member is the session's (``user.email``), never a request
field (R11). It is PROVEN when the session branch bound it as verified
(``acb_auth.deps._bind_session_member``), which is the run context that
``acb_llm.routed.run_attribution`` reads. ``transcribe_on_console`` takes both
values explicitly, because a gateway route is not an agent run.

**The answers.** ``transcribe_on_console`` hands back every status of the
Console, and :func:`console_answer` maps each one to ONE sentence for the
member. It does not use the outage rule of the chat client, which turns
each 5xx into "unavailable".

Fence: ``tests/unit/test_chat_transcribe.py``.
"""

from __future__ import annotations

import struct
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from acb_common.chat_voice_flag import voice_allowed
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

_log = get_logger("gateway.chat_transcribe")

router = APIRouter(
    prefix="/chat", tags=["chat"],
    dependencies=[require_feature_router("chat")],
)

# ── The limits ───────────────────────────────────────────────────────────────

#: The largest clip the route takes. Two minutes of 16 kHz mono WAV are about
#: 3.8 MB, so 10 MB leaves room for a browser that records at a higher rate.
#: The Console's own door takes 25 MB, so this cap is the tighter one.
MAX_CLIP_BYTES = 10 * 1024 * 1024

#: The multipart framing around the clip: the boundaries and the part headers.
_FRAMING_BYTES = 64 * 1024

#: The whole body. A read stops at this count, so a large body costs no more.
MAX_BODY_BYTES = MAX_CLIP_BYTES + _FRAMING_BYTES

#: The longest clip. The composer stops by itself at 120 s.
MAX_SECONDS = 120

#: The allowance above :data:`MAX_SECONDS` for the time the browser takes to stop.
_SECONDS_SLACK = 5

#: The clips of one member in one window.
CLIPS_PER_HOUR = 30

#: The window of the cap, in seconds.
CAP_WINDOW_S = 3600

#: The namespace of the cap counter in the tenant Redis wrapper.
CAP_NAMESPACE = "chat_voice"

#: The one format the route takes: the composer always records a WAV clip.
#: The parameters of a MIME type do not count.
WAV_MIMES = frozenset({"audio/wav", "audio/x-wav", "audio/wave"})

# ── The words the member reads ───────────────────────────────────────────────
#
# One sentence for each answer (§31.5 VOX-1, Do 8). The composer shows the
# sentence as it comes, so a change here changes the screen.

NO_MODEL = "No speech model is set up yet."
#: The credits sentence of the chat (`whatsapp_channel/bot_run.py`).
NO_CREDITS = (
    "Your organization is out of AI credits. An admin can add credits in "
    "Settings, Billing."
)
TOO_LONG = "The clip is too long."
NO_ANSWER = "The speech service did not answer. Try again."
NOT_HERE = "Voice input is not available here."
WRONG_KIND = "Voice input takes a WAV clip. Record it again."
NO_CLIP = "The clip did not arrive. Record it again."


def _refuse(status: int, code: str, message: str) -> HTTPException:
    """The one shape of a refusal: ``{"detail": {"code", "message"}}``."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def console_answer(status: int, body: dict[str, Any]) -> tuple[int, str, str] | str:
    """The transcript of a 200, or ``(status, code, sentence)`` of a refusal.

    The map of Do 8. A 400 that names no binding for the tier, and a 503 that
    names no credential or no capability, are setup faults. A 402 is the
    Console's spend gate. A 413 is the clip. Every other status is the speech
    service, and the member can try again.
    """
    if status == 200:
        text = body.get("text")
        return text if isinstance(text, str) else ""
    detail = body.get("detail")
    said = detail if isinstance(detail, str) else ""
    if status == 400 and said.startswith("no binding for tier"):
        return 503, "no_model", NO_MODEL
    if status == 503 and said.startswith(("no provider credential", "no capability")):
        return 503, "no_model", NO_MODEL
    if status == 402:
        return 402, "no_credits", NO_CREDITS
    if status == 413:
        return 413, "too_long", TOO_LONG
    return 502, "no_answer", NO_ANSWER


# ── The member and the flag ──────────────────────────────────────────────────


def _member_or_404(user: UserContext) -> tuple[str, str]:
    """The session's member and org, or 404 while the flag leaves the org out."""
    member = (user.email or "").strip()
    org = str(user.organization_id or "").strip()
    if "@" not in member or not org or not voice_allowed(org):
        raise HTTPException(status_code=404, detail="Not Found")
    return member, org


def _member_proven(member: str) -> bool:
    """Whether the session branch bound this member as VERIFIED (H-73).

    ``run_attribution`` reads the run context that ``get_current_user`` bound
    for a browser request (branch 1a). Only that same address counts.
    """
    from acb_llm.routed import run_attribution

    who = run_attribution(module_slug="chat")
    named = str(who.get("member") or "").strip().lower()
    return bool(who.get("member_proven")) and named == member.lower()


def _routing_on() -> bool:
    from acb_llm.routed import routing_is_on

    return routing_is_on()


# ── The body, in memory ──────────────────────────────────────────────────────


class _InMemoryParser(MultiPartParser):
    """Starlette's parser, with a spool that never rolls over to disk.

    The body is capped at :data:`MAX_BODY_BYTES` before it gets here, so a
    file part can never pass the spool size.
    """

    spool_max_size = MAX_BODY_BYTES + 1


async def _read_body(request: Request) -> bytes:
    """The raw body, or 413 past :data:`MAX_BODY_BYTES`. No disk."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _refuse(413, "too_long", TOO_LONG)
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > MAX_BODY_BYTES:
            raise _refuse(413, "too_long", TOO_LONG)
    return bytes(buf)


async def _clip_of(request: Request, raw: bytes) -> tuple[bytes, str, str]:
    """``(audio, filename, content type)`` of the one ``file`` part, or 400."""
    content_type = request.headers.get("content-type") or ""
    if not content_type.lower().startswith("multipart/form-data"):
        raise _refuse(400, "no_clip", NO_CLIP)

    async def _once():
        yield raw

    parser = _InMemoryParser(
        Headers({"content-type": content_type}), _once(), max_files=1, max_fields=4,
    )
    try:
        form = await parser.parse()
    except MultiPartException:
        raise _refuse(400, "no_clip", NO_CLIP) from None
    try:
        part = form.get("file")
        if not isinstance(part, UploadFile):
            raise _refuse(400, "no_clip", NO_CLIP)
        audio = await part.read()
        return audio, part.filename or "", part.content_type or ""
    finally:
        await form.close()


def check_kind(filename: str, content_type: str) -> str:
    """``"wav"`` when the name ends in ``.wav`` and the MIME type is a WAV, else 415."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    mime = content_type.split(";", 1)[0].strip().lower()
    if ext != "wav" or mime not in WAV_MIMES:
        raise _refuse(415, "wrong_kind", WRONG_KIND)
    return ext


#: The most RIFF chunks the walk reads before it gives up (a browser clip has 2).
MAX_WAV_CHUNKS = 64


def wav_seconds(audio: bytes) -> float:
    """The length of a WAV clip, counted from the bytes that are present.

    The header's data size is never trusted: a header that says 0.1 s can
    carry 5 MB. The RIFF chunks are walked with ``struct``. ``fmt `` gives the
    rate and the frame size, and the frames are the bytes after the start of
    ``data``, divided by the frame size. Not a RIFF/WAVE file: 415. A
    malformed ``fmt `` or no ``data`` chunk: 400. No frame: 400.
    """
    if len(audio) < 12 or audio[0:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise _refuse(415, "wrong_kind", WRONG_KIND)
    pos = 12
    rate = align = 0
    data_start: int | None = None
    chunks = 0
    while pos + 8 <= len(audio):
        # A browser clip has two chunks. A body of millions of tiny chunks
        # would cost CPU before the hourly cap, so the walk stops early.
        chunks += 1
        if chunks > MAX_WAV_CHUNKS:
            raise _refuse(400, "no_clip", NO_CLIP)
        cid = audio[pos:pos + 4]
        (size,) = struct.unpack_from("<I", audio, pos + 4)
        body = pos + 8
        if cid == b"fmt ":
            if size < 16 or body + 16 > len(audio):
                raise _refuse(400, "no_clip", NO_CLIP)
            _tag, channels, rate, _bps, align, _bits = struct.unpack_from(
                "<HHIIHH", audio, body)
            if channels < 1 or rate < 1 or align < 1:
                raise _refuse(400, "no_clip", NO_CLIP)
        elif cid == b"data":
            data_start = body
            break
        pos = body + size + (size & 1)
    if data_start is None or rate < 1 or align < 1:
        raise _refuse(400, "no_clip", NO_CLIP)
    frames = (len(audio) - data_start) // align
    if frames < 1:
        raise _refuse(400, "no_clip", NO_CLIP)
    return frames / rate


# ── The cap ──────────────────────────────────────────────────────────────────


async def _take_a_slot(org: str, member: str) -> None:
    """Count one clip of the member, or 429 past :data:`CLIPS_PER_HOUR`.

    The counter lives in the tenant Redis wrapper, bound to the session's
    org. Its window starts at the first clip. A counter that lost its expiry
    gets one again. A store that does not answer refuses the clip (502),
    because the cap guards spend.
    """
    from acb_common import tenant_redis

    over: int | None = None
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            counter = tenant_redis.key(CAP_NAMESPACE, "clips", member.lower())
            count = int(await client.incr(counter))
            if count == 1:
                await client.expire(counter, CAP_WINDOW_S)
            if count > CLIPS_PER_HOUR:
                left = int(await client.ttl(counter))
                if left < 0:
                    await client.expire(counter, CAP_WINDOW_S)
                    left = CAP_WINDOW_S
                over = max(left, 1)
    except Exception as exc:
        _log.warning("chat.transcribe_cap_unavailable", error=type(exc).__name__)
        raise _refuse(502, "no_answer", NO_ANSWER) from None
    if over is not None:
        minutes = max(1, -(-over // 60))
        raise _refuse(
            429, "cap",
            f"You recorded {CLIPS_PER_HOUR} clips in the last hour, which is the "
            f"limit. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        )


# ── The routes ───────────────────────────────────────────────────────────────


@router.get("/transcribe", summary="Whether the composer shows the mic")
async def voice_state(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """404 while ``CHAT_VOICE_ORGS`` leaves the org out, else the mic's state."""
    _member_or_404(user)
    ready = _routing_on()
    return {
        "ready": ready,
        "message": None if ready else NOT_HERE,
        "max_seconds": MAX_SECONDS,
    }


@router.post("/transcribe", summary="Turn one spoken clip into text")
async def transcribe(
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Answer ``{"text": ...}`` for one clip. See the module for the order."""
    member, org = _member_or_404(user)
    if not _routing_on():
        raise _refuse(503, "not_here", NOT_HERE)

    raw = await _read_body(request)
    audio, filename, content_type = await _clip_of(request, raw)
    del raw
    if not audio:
        raise _refuse(400, "no_clip", NO_CLIP)
    if len(audio) > MAX_CLIP_BYTES:
        raise _refuse(413, "too_long", TOO_LONG)
    ext = check_kind(filename, content_type)
    seconds = wav_seconds(audio)
    if seconds > MAX_SECONDS + _SECONDS_SLACK:
        raise _refuse(413, "too_long", TOO_LONG)

    await _take_a_slot(org, member)

    from acb_auth.console_resolve import ConsoleRouterUnavailable, transcribe_on_console

    try:
        status, body = await transcribe_on_console(
            audio,
            # The server picks the name, so no member text reaches the vendor.
            filename=f"clip.{ext}",
            content_type=content_type.split(";", 1)[0].strip().lower(),
            member=member,
            member_proven=_member_proven(member),
            agent="chat.voice",
            module_slug="chat",
        )
    except ConsoleRouterUnavailable as exc:
        _log.warning("chat.transcribe_unavailable", error=str(exc)[:120])
        raise _refuse(502, "no_answer", NO_ANSWER) from None

    answer = console_answer(status, body)
    _log.info(
        "chat.transcribe",
        console_status=status,
        clip_bytes=len(audio),
        clip_seconds=round(seconds, 1),
    )
    if isinstance(answer, str):
        return {"text": answer}
    code_status, code, message = answer
    raise _refuse(code_status, code, message)
