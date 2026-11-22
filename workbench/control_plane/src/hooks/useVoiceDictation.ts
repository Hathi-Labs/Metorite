"use client";

/**
 * The chat's voice input (`projects_ai_chat.md` §31, VOX-1): the mic's state,
 * the recording and the one request of a clip.
 *
 * `lib/voiceDictation.ts` holds the rules, `lib/audioCapture.ts` records, and
 * `components/VoiceInputButton.tsx` draws the control. The transcript goes to
 * `onText`, and the composer puts it in the box. Nothing here sends a message.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { startPcmCapture, type PcmCapture } from "@/lib/audioCapture";
import { useCachedResource } from "@/lib/useCachedResource";
import {
  answerSentence,
  availabilityOf,
  elapsedSeconds,
  HIDDEN,
  IDLE,
  MAX_CLIP_BYTES,
  micRefusalSentence,
  mustStop,
  NO_ANSWER,
  NO_WORDS,
  TOO_LONG,
  VOICE_PATH,
  voiceReducer,
  type VoiceAvailability,
  type VoiceState,
} from "@/lib/voiceDictation";

/** The size of the RIFF header that `encodeWav` writes. */
const WAV_HEADER_BYTES = 44;

/** Read whether the composer shows the mic. The read cache keeps it, so a
 *  second chat on the page asks nothing. */
async function readAvailability(): Promise<VoiceAvailability> {
  const res = await fetch("/api/agent/transcribe", { cache: "no-store" });
  const body = await res.json().catch(() => null);
  return availabilityOf(res.status, body);
}

/** The mic's state for this member. Hidden until the read answers. */
export function useVoiceAvailability(): VoiceAvailability {
  const res = useCachedResource<VoiceAvailability>(VOICE_PATH, readAvailability, { ttl: 60_000 });
  return res.data ?? HIDDEN;
}

/** Send one clip. The answer is the text, or the sentence of a refusal. */
export async function postClip(clip: Blob): Promise<{ text: string } | { error: string }> {
  if (clip.size > MAX_CLIP_BYTES) return { error: TOO_LONG };
  // A WAV header and no sound: nothing to send, and nothing to bill.
  if (clip.size <= WAV_HEADER_BYTES) return { text: "" };
  const form = new FormData();
  form.append("file", clip, "clip.wav");
  let res: Response;
  try {
    res = await fetch("/api/agent/transcribe", { method: "POST", body: form });
  } catch {
    return { error: NO_ANSWER };
  }
  const body = (await res.json().catch(() => null)) as { text?: unknown } | null;
  if (!res.ok) return { error: answerSentence(res.status, body) };
  return { text: typeof body?.text === "string" ? body.text : "" };
}

export interface VoiceDictation {
  state: VoiceState;
  /** Whole seconds of the recording that runs. */
  elapsed: number;
  start: () => Promise<void>;
  /** Stop and transcribe. A second call does nothing. */
  stop: () => Promise<void>;
  /** Stop and keep nothing. */
  cancel: () => void;
}

export function useVoiceDictation(opts: {
  onText: (text: string) => void;
  onNotice: (sentence: string | null) => void;
}): VoiceDictation {
  const [state, dispatch] = useReducer(voiceReducer, IDLE);
  const [now, setNow] = useState(0);
  const capture = useRef<PcmCapture | null>(null);
  // Each start and each cancel moves this on, so a mic grant that arrives
  // after a cancel is dropped and records nothing.
  const attempt = useRef(0);
  const mounted = useRef(true);
  const stateRef = useRef(state);
  const handlers = useRef(opts);
  useEffect(() => {
    stateRef.current = state;
    handlers.current = opts;
  });

  const start = useCallback(async () => {
    if (stateRef.current.phase !== "idle") return;
    const mine = ++attempt.current;
    stateRef.current = { ...stateRef.current, phase: "asking" };
    dispatch({ type: "press" });
    handlers.current.onNotice(null);
    try {
      const handle = await startPcmCapture({
        onLevel: (level) => dispatch({ type: "level", level }),
      });
      if (!mounted.current || mine !== attempt.current) {
        handle.cancel();
        return;
      }
      capture.current = handle;
      const at = Date.now();
      setNow(at);
      dispatch({ type: "granted", at });
    } catch (err) {
      if (mine !== attempt.current) return;
      dispatch({ type: "refused" });
      handlers.current.onNotice(micRefusalSentence(err));
    }
  }, []);

  const stop = useCallback(async () => {
    const handle = capture.current;
    if (!handle) return;
    capture.current = null;
    const clip = handle.stop();
    dispatch({ type: "stop" });
    const out = await postClip(clip);
    if (!mounted.current) return;
    dispatch({ type: "done" });
    if ("error" in out) handlers.current.onNotice(out.error);
    else if (!out.text.trim()) handlers.current.onNotice(NO_WORDS);
    else handlers.current.onText(out.text);
  }, []);

  const cancel = useCallback(() => {
    attempt.current++;
    capture.current?.cancel();
    capture.current = null;
    dispatch({ type: "cancel" });
  }, []);

  // The clock of a recording, and its stop at 120 s.
  useEffect(() => {
    if (state.phase !== "recording") return;
    const id = setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (mustStop(stateRef.current, t)) void stop();
    }, 250);
    return () => clearInterval(id);
  }, [state.phase, stop]);

  // A chat that unmounts mid-recording keeps nothing and frees the mic.
  useEffect(() => {
    mounted.current = true;
    return () => {
      // A grant that arrives after this reads `mounted` and records nothing.
      mounted.current = false;
      capture.current?.cancel();
      capture.current = null;
    };
  }, []);

  return { state, elapsed: elapsedSeconds(state, now), start, stop, cancel };
}
