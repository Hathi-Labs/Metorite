"""Live streaming transcription + speaking, for the meeting-bot worker.

Two optional, config-gated capabilities layered on top of the batch recording
(which stays the archival source of truth):

1. **Stream** the call audio to a streaming ASR (self-hosted WhisperLive-style
   WebSocket, or any compatible endpoint) and forward each recognised segment to
   the gateway's live bus (``POST {LIVE_CALLBACK_URL}``) as it arrives — so the
   UI shows live captions and agents can act mid-meeting. When ``EMBED_CMD`` is
   set, each segment also carries a per-utterance speaker **embedding** (formed
   by a local pause endpointer, ``endpointing.py``) so the gateway can keep
   speaker identity consistent across chunks.
2. **Speak** a line back into the call: render text to audio (a pluggable
   ``TTS_CMD``) and play it into the bot's virtual microphone so participants
   hear it — the actuator for agent interjections.

Everything degrades gracefully: with no ``LIVE_ASR_URL`` there are simply no
live captions; with no ``TTS_CMD`` a "say" request is logged, not spoken. Config
is read from the environment (see README). This is plumbing — the streaming ASR
service and a TTS voice are wired here but must be verified on the box.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import subprocess
import time

log = logging.getLogger("meeting_bot.live")

PULSE_MONITOR = os.environ.get("PULSE_MONITOR", "meet.monitor")
# The virtual PulseAudio source Chrome uses as its microphone; TTS is played
# into its sink so the meeting hears the bot.
VIRTUAL_MIC_SINK = os.environ.get("VIRTUAL_MIC_SINK", "vmic")
LIVE_ASR_URL = os.environ.get("LIVE_ASR_URL", "").strip()
# Where to ask Metorite for streaming-ASR credentials when no self-hosted
# LIVE_ASR_URL is set. This is the normal path: the key lives in Settings ->
# Models, the gateway mints a short-lived token, and the worker never holds the
# master key. Unset AND no LIVE_ASR_URL -> the bot records but has no captions.
LIVE_TOKEN_URL = os.environ.get("LIVE_TOKEN_URL", "").strip()
_AAI_WS = "wss://streaming.assemblyai.com/v3/ws"
# How often to ask the gateway whether this meeting should be streaming. The
# answer changes when the copilot is toggled mid-meeting, and streaming is
# billed per minute — so this interval is both the delay before captions start
# and the most you can overpay after switching the copilot off.
try:
    _LIVE_RECHECK_S = float(os.environ.get("LIVE_RECHECK_SECONDS", "30"))
except ValueError:
    _LIVE_RECHECK_S = 30.0
# The per-meeting callback URL comes from the join request; this token authents
# the worker → gateway callback (defaults to the shared bot token).
LIVE_CALLBACK_TOKEN = os.environ.get(
    "LIVE_CALLBACK_TOKEN", os.environ.get("MEETING_BOT_TOKEN", "")
).strip()
# TTS command template: receives the text on stdin (or via {text}) and must
# write WAV/PCM audio to the path given as {out}. e.g. a piper invocation.
TTS_CMD = os.environ.get("TTS_CMD", "").strip()
# Optional per-utterance speaker embedding. EMBED_CMD is a shell template that
# reads PCM (s16le 16 kHz mono) from {in} and writes a JSON float array to {out}
# — e.g. a small onnx CAM++/pyannote embedder. Unset → no embeddings, and the
# gateway falls back to label passthrough. Attaching an embedding per utterance
# is what lets the gateway keep speaker identity CONSISTENT across chunks
# (voiceprint gallery — live_speakers.py). See README.
EMBED_CMD = os.environ.get("EMBED_CMD", "").strip()
# Energy-VAD threshold (RMS over s16le) used only to group audio into utterance
# windows for embedding — crude but dependency-free; tune per room/mic on the box.
try:
    _VAD_RMS = float(os.environ.get("LIVE_VAD_RMS", "300"))
except ValueError:
    _VAD_RMS = 300.0

# How long a recognised segment may wait for its utterance to close (and so for
# its speaker embedding) before being forwarded untagged. Bounds live latency.
try:
    _SEGMENT_MAX_WAIT = float(os.environ.get("LIVE_SEGMENT_MAX_WAIT", "2.5"))
except ValueError:
    _SEGMENT_MAX_WAIT = 2.5

_SAMPLE_RATE = 16000
_FRAME_BYTES = 3200  # 100 ms of 16 kHz s16le mono
_FRAME_MS = _FRAME_BYTES / (2 * _SAMPLE_RATE) * 1000.0  # 100.0


def _rms(frame: bytes) -> float:
    """Root-mean-square amplitude of an s16le PCM frame (no numpy/audioop)."""
    n = len(frame) // 2
    if n == 0:
        return 0.0
    total = 0
    for (s,) in struct.iter_unpack("<h", frame[: n * 2]):
        total += s * s
    return (total / n) ** 0.5


def _is_speech(frame: bytes) -> bool:
    return _rms(frame) >= _VAD_RMS


async def _embed_pcm(pcm: bytes) -> list[float] | None:
    """Compute a speaker embedding for one utterance's PCM via ``EMBED_CMD``.
    Fail-safe: returns None on any problem (missing cmd, bad output, error)."""
    if not EMBED_CMD or not pcm:
        return None
    tag = abs(hash(pcm)) % 10**8
    inp, outp = f"/tmp/utt-{tag}.pcm", f"/tmp/utt-{tag}.json"
    try:
        with open(inp, "wb") as f:
            f.write(pcm)
        cmd = EMBED_CMD.replace("{in}", inp).replace("{out}", outp)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if os.path.isfile(outp):
            with open(outp) as f:
                data = json.load(f)
            if isinstance(data, list) and data and all(
                isinstance(x, (int, float)) for x in data
            ):
                return [float(x) for x in data]
    except Exception as exc:
        log.warning("embed failed: %s", str(exc)[:200])
    finally:
        for p in (inp, outp):
            with contextlib.suppress(OSError):
                os.remove(p)
    return None


def _wanted_url(meeting_callback: str) -> str:
    """The gateway's "should I be streaming?" endpoint for this meeting.

    Derived from the per-meeting callback the gateway already hands us at join
    (.../meetings/{id}/live/segment), so no new field has to be threaded
    through the join contract."""
    return meeting_callback.rsplit("/live/segment", 1)[0] + "/live/wanted"


async def _live_wanted(meeting_callback: str) -> bool:
    """Whether anything is reading the stream right now. Fail-open: if the
    gateway can't be reached we keep streaming, because losing captions is
    worse than one meeting's spend — and the gateway itself fails open too."""
    if not meeting_callback:
        return True
    try:
        import httpx

        headers = {}
        if LIVE_CALLBACK_TOKEN:
            headers["Authorization"] = f"Bearer {LIVE_CALLBACK_TOKEN}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(_wanted_url(meeting_callback), headers=headers)
            r.raise_for_status()
            data = r.json()
        wanted = bool(data.get("wanted", True))
        if not wanted:
            log.info("live not needed: %s", str(data.get("reason", ""))[:160])
        return wanted
    except Exception as exc:
        log.warning("live-wanted check failed (assuming yes): %s", str(exc)[:160])
        return True


def live_enabled() -> bool:
    """Live streaming is possible when *some* streaming ASR is reachable —
    either a self-hosted WebSocket, or Metorite's token endpoint (which
    fronts whichever provider is keyed in Settings -> Models). The per-meeting
    callback (where segments go) is supplied at join time."""
    return bool(LIVE_ASR_URL or LIVE_TOKEN_URL)


async def _resolve_asr() -> tuple[str, str] | None:
    """(websocket_url, protocol) for this call, or None if live isn't possible.

    A self-hosted ``LIVE_ASR_URL`` wins when set — it costs nothing per minute.
    Otherwise ask the gateway for a short-lived provider token. Returns the
    protocol so the reader knows which message shape to expect; they differ
    enough that guessing from the payload would be fragile.
    """
    if LIVE_ASR_URL:
        return LIVE_ASR_URL, "whisperlive"
    if not LIVE_TOKEN_URL:
        return None
    try:
        import httpx

        headers = {}
        if LIVE_CALLBACK_TOKEN:
            headers["Authorization"] = f"Bearer {LIVE_CALLBACK_TOKEN}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(LIVE_TOKEN_URL, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("live token fetch failed: %s", str(exc)[:200])
        return None

    provider = (data.get("provider") or "").strip()
    token = (data.get("token") or "").strip()
    if not token:
        log.warning("live token response carried no token")
        return None
    if provider != "assemblyai":
        # Deepgram streaming from the worker isn't wired yet; say so plainly
        # rather than opening a socket that will never produce a transcript.
        log.warning(
            "live captions need an AssemblyAI key (got provider=%r) — "
            "the bot will still record and transcribe after the call",
            provider,
        )
        return None
    # Ephemeral tokens go in the query string (the browser can't set headers on
    # a WebSocket, and AssemblyAI kept one scheme for both callers).
    # speaker_labels must be asked for explicitly — streaming does not diarize
    # by default, and omitting it is why live captions had no speakers.
    #
    # The model matters as much as the flag: diarization needs Universal-3 Pro
    # Streaming (`u3-rt-pro`) or a multilingual streaming model. The gateway
    # decides which and passes it here; dropping it on the floor (as this did)
    # meant the worker silently used AssemblyAI's default whatever the
    # deployment had configured.
    model = (data.get("model") or "").strip()
    url = (
        f"{_AAI_WS}?sample_rate={_SAMPLE_RATE}"
        f"&encoding=pcm_s16le&format_turns=true&speaker_labels=true"
        f"&token={token}"
    )
    if model:
        url += f"&speech_model={model}"
    return url, "assemblyai"


def _pcm_ffmpeg() -> subprocess.Popen:
    """ffmpeg reading the meeting's monitor → 16 kHz mono s16le PCM on stdout."""
    return subprocess.Popen(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-f", "pulse", "-i", PULSE_MONITOR,
            "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "s16le", "-",
        ],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


async def _forward_segment(meeting_callback: str, seg: dict) -> None:
    import httpx

    headers = {"Content-Type": "application/json"}
    if LIVE_CALLBACK_TOKEN:
        headers["Authorization"] = f"Bearer {LIVE_CALLBACK_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(meeting_callback, headers=headers, json=seg)
    except Exception as exc:  # live is best-effort; never break the recording
        log.warning("live forward failed: %s", str(exc)[:200])


def _parse_assemblyai_message(data: dict) -> list[dict]:
    """AssemblyAI streaming v3 ``Turn`` -> our segment shape.

    Only completed turns are emitted. AssemblyAI streams a growing partial for
    every turn, and the live bus appends rather than replaces — forwarding
    partials would stutter the same sentence down the console a word at a time.
    This matches what the browser recorder already relays.

    Word timings are milliseconds from stream start; the bus works in seconds,
    and the spans matter because they drive speaker reconciliation.
    """
    if data.get("type") != "Turn" or not data.get("end_of_turn"):
        return []
    text = (data.get("transcript") or "").strip()
    if not text:
        return []
    words = data.get("words") or []
    start_ms = words[0].get("start", 0) if words else 0
    end_ms = words[-1].get("end", 0) if words else 0
    seg = {
        "text": text,
        "start_s": float(start_ms or 0) / 1000.0,
        "end_s": float(end_ms or 0) / 1000.0,
        "is_final": True,
    }
    # A diarized turn belongs to one speaker, but the label can arrive on the
    # turn or on its words depending on the streaming model. Read whichever is
    # there. Mapped to our S1/S2 space so the gateway's registry, the rename UI
    # and batch reconciliation all see one label space.
    speaker = data.get("speaker")
    if speaker is None:
        speaker = next((w.get("speaker") for w in words if w.get("speaker")), None)
    label = _speaker_label(speaker)
    if label:
        seg["speaker_label"] = label
    return [seg]


def _speaker_label(speaker: object) -> str | None:
    """AssemblyAI labels speakers "A", "B", … — map onto our S1/S2 space.

    Mirrors ``acb_stt.assemblyai_provider._speaker_label`` so the live and batch
    paths agree; the worker is a standalone service and can't import it."""
    if speaker is None:
        return None
    s = str(speaker).strip()
    if not s:
        return None
    if len(s) == 1 and s.isalpha():
        return f"S{ord(s.upper()) - 64}"
    if s.isdigit():
        return f"S{int(s) + 1}"
    return s


def _parse_asr_message(raw: str, protocol: str = "whisperlive") -> list[dict]:
    """Normalise an ASR message into our segment shape.
    Defensive: protocols differ, so accept the common shapes and ignore the
    rest."""
    out: list[dict] = []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return out
    if not isinstance(data, dict):
        return out
    if protocol == "assemblyai":
        return _parse_assemblyai_message(data)
    segments = data.get("segments") if isinstance(data, dict) else None
    if isinstance(segments, list):
        for s in segments:
            if not isinstance(s, dict):
                continue
            txt = (s.get("text") or "").strip()
            if txt:
                out.append({
                    "text": txt,
                    "start_s": float(s.get("start", 0) or 0),
                    "end_s": float(s.get("end", 0) or 0),
                    "is_final": bool(s.get("completed", s.get("is_final", True))),
                })
    elif isinstance(data, dict) and (data.get("text") or "").strip():
        out.append({"text": data["text"].strip(), "is_final": True})
    return out


async def _embed_and_store(utt: object, windows: list) -> None:
    """Embed one utterance's PCM and append its (start, end, embedding) window."""
    emb = await _embed_pcm(b"".join(utt.frames))  # type: ignore[attr-defined]
    if emb is not None:
        windows.append((utt.start_s, utt.end_s, emb))  # type: ignore[attr-defined]
        del windows[:-50]  # keep the recent-window list bounded


async def _flush_pending(
    meeting_callback: str, pending: list, windows: list, *, force: bool = False
) -> None:
    """Forward pending segments, attaching an embedding once one is available.

    An utterance's embedding only exists once that utterance CLOSES, but the ASR
    streams segments *during* it — so forwarding immediately would leave most
    segments without a voiceprint. We hold each segment briefly: as soon as a
    covering window lands it goes out tagged, and if none arrives within
    ``LIVE_SEGMENT_MAX_WAIT`` it goes out untagged rather than being delayed
    further (the gateway then falls back to label passthrough). Bounded latency,
    and it lines up with acting on turn boundaries anyway."""
    from . import endpointing

    now = time.monotonic()
    still: list = []
    for seg, queued_at in pending:
        emb = endpointing.pick_embedding(
            float(seg.get("start_s", 0.0)), float(seg.get("end_s", 0.0)), windows
        )
        if emb is not None:
            seg["embedding"] = emb
        elif not force and (now - queued_at) < _SEGMENT_MAX_WAIT:
            still.append((seg, queued_at))
            continue
        await _forward_segment(meeting_callback, seg)
    pending[:] = still


async def _asr_reader(ws, meeting_callback: str, chunking: bool, windows: list,
                      pending: list, protocol: str = "whisperlive") -> None:
    """Read ASR messages and forward them to the gateway live bus — immediately
    when not chunking, else queued so an embedding can be attached (above)."""
    async for message in ws:
        for seg in _parse_asr_message(
            message if isinstance(message, str) else message.decode(), protocol
        ):
            if chunking:
                pending.append((seg, time.monotonic()))
                await _flush_pending(meeting_callback, pending, windows)
            else:
                await _forward_segment(meeting_callback, seg)


async def stream_transcription(meeting_callback: str, stop: asyncio.Event) -> None:
    """Supervise the live stream for the whole call, paying only while needed.

    Streaming ASR bills per minute, so this opens the socket when something is
    actually reading it (the copilot) and closes it when nothing is. Both edges
    matter: switching the copilot ON mid-meeting has to start captions, and
    switching it OFF has to stop the meter — a start-only check would leave a
    four-hour call streaming for an agent that was turned off in minute three.

    The archival recording runs the whole time regardless, so nothing is lost
    while the stream is closed: the batch pipeline transcribes it afterwards.
    """
    if not live_enabled():
        return
    while not stop.is_set():
        if not await _live_wanted(meeting_callback):
            # Wait on `stop` rather than sleeping, so ending the call doesn't
            # have to sit through the rest of the interval.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=_LIVE_RECHECK_S)
            continue
        # Stops when the call ends OR when live stops being wanted.
        session_stop = asyncio.Event()
        watch = asyncio.create_task(
            _watch_wanted(meeting_callback, stop, session_stop)
        )
        try:
            await _stream_once(meeting_callback, session_stop)
        finally:
            session_stop.set()
            watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watch


async def _watch_wanted(
    meeting_callback: str, stop: asyncio.Event, session_stop: asyncio.Event
) -> None:
    """Close the stream once nothing is reading it any more."""
    while not stop.is_set() and not session_stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_LIVE_RECHECK_S)
            break                      # the call ended
        except TimeoutError:
            pass
        if not await _live_wanted(meeting_callback):
            log.info("live no longer needed — closing the ASR stream")
            session_stop.set()
            return
    session_stop.set()


async def _stream_once(meeting_callback: str, stop: asyncio.Event) -> None:
    """Pump call audio → ASR WS → gateway live bus until ``stop`` is set.

    The ASR (WhisperLive-style) segments on its own VAD, so its segments are
    already utterance-aligned. When ``EMBED_CMD`` is set we additionally run a
    local energy-VAD endpointer to form utterance windows, compute a speaker
    embedding per window, and attach it to the overlapping ASR segment — giving
    the gateway the voiceprints it needs to keep speaker identity consistent
    across chunks. With no ``EMBED_CMD`` this is exactly the old batch-style
    stream (text only). No-op unless live is configured."""
    if not live_enabled():
        return
    try:
        import websockets
    except Exception:
        log.warning("websockets not installed — live transcription disabled")
        return

    from . import endpointing

    chunking = bool(EMBED_CMD)  # only pay for VAD/embeddings when an embedder is set
    windows: list[tuple[float, float, list[float]]] = []
    pending: list = []          # segments awaiting their utterance's embedding
    endpointer = endpointing.Endpointer(frame_ms=_FRAME_MS)

    async def _embed_then_flush(utt: object) -> None:
        # A closed utterance is exactly what the waiting segments need, so flush
        # as soon as its embedding exists.
        await _embed_and_store(utt, windows)
        await _flush_pending(meeting_callback, pending, windows)

    tasks: set[asyncio.Task] = set()

    def _spawn(coro) -> None:
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    resolved = await _resolve_asr()
    if resolved is None:
        return
    asr_url, protocol = resolved
    log.info("live transcription starting (protocol=%s)", protocol)

    proc = _pcm_ffmpeg()
    try:
        async with websockets.connect(asr_url, max_size=None) as ws:
            reader = asyncio.create_task(
                _asr_reader(
                    ws, meeting_callback, chunking, windows, pending, protocol
                )
            )
            loop = asyncio.get_event_loop()
            while not stop.is_set():
                chunk = await loop.run_in_executor(
                    None, proc.stdout.read, _FRAME_BYTES
                )
                if not chunk:
                    break
                await ws.send(chunk)
                if chunking:
                    utt = endpointer.push(chunk, _is_speech(chunk))
                    if utt is not None:
                        _spawn(_embed_then_flush(utt))
                    # Release anything that has waited long enough.
                    if pending:
                        await _flush_pending(meeting_callback, pending, windows)
            if chunking:
                last = endpointer.flush()
                if last is not None:
                    await _embed_and_store(last, windows)
                # Never strand a segment when the meeting ends.
                await _flush_pending(
                    meeting_callback, pending, windows, force=True
                )
            reader.cancel()
    except Exception as exc:
        log.warning("live transcription stopped: %s", str(exc)[:200])
    finally:
        with contextlib.suppress(Exception):
            proc.kill()


async def speak(text: str) -> None:
    """Render ``text`` to audio and play it into the bot's virtual mic so the
    meeting hears it. No-op-with-log when no TTS_CMD is configured."""
    if not TTS_CMD:
        log.info("say (no TTS configured, not spoken): %s", text[:200])
        return
    out = f"/tmp/say-{abs(hash(text)) % 10**8}.wav"
    cmd = TTS_CMD.replace("{out}", out).replace("{text}", text)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(text.encode())
        if os.path.isfile(out):
            # Play into the virtual mic's sink so Chrome (using vmic as its
            # microphone) transmits it to the call.
            play = await asyncio.create_subprocess_exec(
                "paplay", f"--device={VIRTUAL_MIC_SINK}", out,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await play.wait()
    except Exception as exc:
        log.warning("speak failed: %s", str(exc)[:200])
    finally:
        with contextlib.suppress(OSError):
            os.remove(out)
