/**
 * The rules of the chat's voice input (`projects_ai_chat.md` §31, VOX-1).
 *
 * The member presses the mic, talks, and presses it again (or Enter). The
 * box shows "Transcribing…", then the text goes in at the caret. Escape
 * cancels a recording and keeps nothing. At 120 s the recording stops by
 * itself. The chat never sends a transcript by itself: the member reads it,
 * edits it and sends it.
 *
 * These rules are pure, so `voiceDictation.test.ts` drives them in node.
 * `hooks/useVoiceDictation.ts` runs them, `lib/audioCapture.ts` records, and
 * `components/VoiceInputButton.tsx` draws the control.
 */

/** The BFF path of the voice input. GET reads the mic's state, and POST
 *  sends one clip. */
export const VOICE_PATH = "/api/agent/transcribe";

/** The longest clip. The gateway refuses a longer WAV (`chat_transcribe.py`). */
export const MAX_SECONDS = 120;

/** The largest clip the BFF route sends on, as the gateway's cap. */
export const MAX_CLIP_BYTES = 10 * 1024 * 1024;

export type VoicePhase = "idle" | "asking" | "recording" | "transcribing";

export interface VoiceState {
  phase: VoicePhase;
  /** When the recording started (ms), while it records. */
  startedAt: number | null;
  /** The level of the last block, from 0 to 1. */
  level: number;
}

export const IDLE: VoiceState = { phase: "idle", startedAt: null, level: 0 };

export type VoiceEvent =
  | { type: "press" }
  | { type: "granted"; at: number }
  | { type: "refused" }
  | { type: "level"; level: number }
  | { type: "stop" }
  | { type: "cancel" }
  | { type: "done" };

/**
 * The one state machine of the mic.
 *
 * idle -press-> asking -granted-> recording -stop-> transcribing -done-> idle.
 * A refusal, a cancel and an unmount go back to idle. An event that does not
 * fit the phase changes nothing, so a double press records once (M3).
 */
export function voiceReducer(state: VoiceState, event: VoiceEvent): VoiceState {
  switch (event.type) {
    case "press":
      return state.phase === "idle" ? { phase: "asking", startedAt: null, level: 0 } : state;
    case "granted":
      return state.phase === "asking" ? { phase: "recording", startedAt: event.at, level: 0 } : state;
    case "refused":
      return state.phase === "asking" ? IDLE : state;
    case "level":
      return state.phase === "recording" ? { ...state, level: event.level } : state;
    case "stop":
      return state.phase === "recording" ? { phase: "transcribing", startedAt: null, level: 0 } : state;
    case "cancel":
      return state.phase === "asking" || state.phase === "recording" ? IDLE : state;
    case "done":
      return state.phase === "transcribing" ? IDLE : state;
  }
}

/** The seconds a recording has run, whole. */
export function elapsedSeconds(state: VoiceState, now: number): number {
  if (state.phase !== "recording" || state.startedAt === null) return 0;
  return Math.max(0, Math.floor((now - state.startedAt) / 1000));
}

/** Whether a recording must stop by itself now. */
export function mustStop(state: VoiceState, now: number): boolean {
  return state.phase === "recording" && elapsedSeconds(state, now) >= MAX_SECONDS;
}

/** "0:07", "1:59", "2:00". */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * The box after the transcript goes in at the caret.
 *
 * A space goes before the text when the words before the caret do not end in
 * a space, and after it when a word follows the caret. A selection is
 * replaced. `caret` is the place after the inserted text.
 */
export function insertAtCaret(
  value: string,
  start: number,
  end: number,
  text: string,
): { value: string; caret: number } {
  const words = text.trim();
  const a = Math.max(0, Math.min(start, value.length));
  const b = Math.max(a, Math.min(end, value.length));
  if (!words) return { value, caret: b };
  const before = value.slice(0, a);
  const after = value.slice(b);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const tail = after && !/^\s/.test(after) ? " " : "";
  const inserted = `${lead}${words}${tail}`;
  return { value: before + inserted + after, caret: a + lead.length + words.length };
}

// ── The words the member reads ──────────────────────────────────────────────

export const NO_ANSWER = "The speech service did not answer. Try again.";
export const TOO_LONG = "The clip is too long.";
export const NO_WORDS = "Metorite heard no words in the clip. Try again, closer to the mic.";
export const NO_CLIP = "The clip did not arrive. Record it again.";

/** The one sentence of a refused mic, by the browser's error name. Each says
 *  how to go on. */
export function micRefusalSentence(err: unknown): string {
  const name = err instanceof Error || (typeof err === "object" && err !== null && "name" in err)
    ? String((err as { name?: unknown }).name ?? "")
    : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
    case "PermissionDeniedError":
      return "Metorite cannot use the microphone. Allow the microphone for this site in the browser's address bar, then press the mic again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "Metorite found no microphone. Connect one, then press the mic again.";
    case "NotReadableError":
    case "AbortError":
      return "Another app is using the microphone. Close that app, then press the mic again.";
    default:
      return "This browser cannot record sound here. Type the message instead.";
  }
}

/**
 * The sentence of a refused clip. The gateway sends one in
 * `detail.message`, and that sentence wins. A body with none falls back by
 * status, so the member never reads a raw code.
 */
export function answerSentence(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  if (status === 413) return TOO_LONG;
  return NO_ANSWER;
}

/** What the composer reads before it draws the mic. */
export interface VoiceAvailability {
  /** False while the flag leaves the org out: no mic. */
  shown: boolean;
  /** False while the mic shows but a press can only explain why it waits. */
  ready: boolean;
  /** The sentence a press shows while `ready` is false. */
  message: string | null;
}

export const HIDDEN: VoiceAvailability = { shown: false, ready: false, message: null };

/** The mic's state from the answer of `GET /api/agent/transcribe`. A 404 is
 *  the flag off. Any other failure hides the mic too. */
export function availabilityOf(status: number, body: unknown): VoiceAvailability {
  if (status !== 200 || !body || typeof body !== "object") return HIDDEN;
  const b = body as { ready?: unknown; message?: unknown };
  const ready = b.ready === true;
  const message = typeof b.message === "string" && b.message.trim() ? b.message : null;
  return { shown: true, ready, message: ready ? null : (message ?? NO_ANSWER) };
}
