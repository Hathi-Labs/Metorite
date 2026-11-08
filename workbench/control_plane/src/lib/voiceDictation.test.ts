/**
 * The chat's voice input (VOX-1, `projects_ai_chat.md` §31). The vitest rig
 * runs in node with no DOM, so these cases drive the state machine and the
 * rules of `lib/voiceDictation.ts`, and read the wiring of the composer, the
 * button and the BFF route from their source.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { voiceLabel } from "@/components/VoiceInputButton";
import {
  answerSentence,
  availabilityOf,
  elapsedSeconds,
  formatClock,
  HIDDEN,
  IDLE,
  insertAtCaret,
  MAX_SECONDS,
  micRefusalSentence,
  mustStop,
  NO_ANSWER,
  TOO_LONG,
  voiceReducer,
  type VoiceEvent,
  type VoiceState,
} from "./voiceDictation";

const read = (f: string) => readFileSync(join(process.cwd(), "src", f), "utf-8");
const run = (events: VoiceEvent[], from: VoiceState = IDLE) => events.reduce(voiceReducer, from);

describe("the recorder states", () => {
  it("a press asks for the mic, a grant records, a stop transcribes, an answer ends it", () => {
    const asking = run([{ type: "press" }]);
    expect(asking.phase).toBe("asking");
    const recording = voiceReducer(asking, { type: "granted", at: 1_000 });
    expect(recording).toEqual({ phase: "recording", startedAt: 1_000, level: 0 });
    const loud = voiceReducer(recording, { type: "level", level: 0.7 });
    expect(loud.level).toBe(0.7);
    const transcribing = voiceReducer(loud, { type: "stop" });
    expect(transcribing.phase).toBe("transcribing");
    expect(voiceReducer(transcribing, { type: "done" })).toEqual(IDLE);
  });

  it("a refused mic goes back to idle", () => {
    expect(run([{ type: "press" }, { type: "refused" }])).toEqual(IDLE);
  });

  it("Escape cancels a recording and keeps nothing", () => {
    expect(run([{ type: "press" }, { type: "granted", at: 0 }, { type: "cancel" }])).toEqual(IDLE);
    expect(run([{ type: "press" }, { type: "cancel" }])).toEqual(IDLE);
  });

  it("a second press while busy records once (M3)", () => {
    const recording = run([{ type: "press" }, { type: "granted", at: 5 }]);
    expect(voiceReducer(recording, { type: "press" })).toBe(recording);
    const transcribing = voiceReducer(recording, { type: "stop" });
    expect(voiceReducer(transcribing, { type: "stop" })).toBe(transcribing);
    expect(voiceReducer(transcribing, { type: "cancel" })).toBe(transcribing);
    expect(voiceReducer(transcribing, { type: "press" })).toBe(transcribing);
  });

  it("an event that does not fit the phase changes nothing", () => {
    expect(voiceReducer(IDLE, { type: "stop" })).toBe(IDLE);
    expect(voiceReducer(IDLE, { type: "level", level: 1 })).toBe(IDLE);
    expect(voiceReducer(IDLE, { type: "granted", at: 1 })).toBe(IDLE);
    expect(voiceReducer(IDLE, { type: "done" })).toBe(IDLE);
  });

  it("the clock counts whole seconds, and the recording stops itself at 120 s", () => {
    const rec = run([{ type: "press" }, { type: "granted", at: 10_000 }]);
    expect(elapsedSeconds(rec, 10_999)).toBe(0);
    expect(elapsedSeconds(rec, 17_400)).toBe(7);
    expect(mustStop(rec, 10_000 + (MAX_SECONDS - 1) * 1000)).toBe(false);
    expect(mustStop(rec, 10_000 + MAX_SECONDS * 1000)).toBe(true);
    expect(mustStop(IDLE, 10_000_000)).toBe(false);
    expect(MAX_SECONDS).toBe(120);
  });

  it("the timer reads m:ss", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(7)).toBe("0:07");
    expect(formatClock(119)).toBe("1:59");
    expect(formatClock(120)).toBe("2:00");
  });

  it("each phase has an accessible name", () => {
    expect(voiceLabel("idle")).toBe("Dictate a message");
    expect(voiceLabel("asking")).toBe("Waiting for the microphone");
    expect(voiceLabel("recording")).toBe("Stop recording and transcribe");
    expect(voiceLabel("transcribing")).toBe("Transcribing");
  });
});

describe("the text goes in at the caret", () => {
  it("an empty box takes the text as it is", () => {
    expect(insertAtCaret("", 0, 0, "  kal ki meeting  ")).toEqual({ value: "kal ki meeting", caret: 14 });
  });

  it("a space goes before the text when the box holds words", () => {
    expect(insertAtCaret("Hello", 5, 5, "there")).toEqual({ value: "Hello there", caret: 11 });
    expect(insertAtCaret("Hello ", 6, 6, "there")).toEqual({ value: "Hello there", caret: 11 });
  });

  it("text in the middle keeps a space on each side", () => {
    expect(insertAtCaret("ab", 1, 1, "X")).toEqual({ value: "a X b", caret: 3 });
  });

  it("a selection is replaced", () => {
    expect(insertAtCaret("one two three", 4, 7, "2")).toEqual({ value: "one 2 three", caret: 5 });
  });

  it("an empty transcript changes nothing, and a caret past the end is clamped", () => {
    expect(insertAtCaret("keep", 2, 2, "  ")).toEqual({ value: "keep", caret: 2 });
    expect(insertAtCaret("ok", 99, 99, "go")).toEqual({ value: "ok go", caret: 5 });
  });
});

describe("the words", () => {
  it("a refused mic says how to allow it", () => {
    const denied = micRefusalSentence(new DOMException("no", "NotAllowedError"));
    expect(denied).toMatch(/Allow the microphone for this site/);
    expect(micRefusalSentence({ name: "NotFoundError" })).toMatch(/found no microphone/);
    expect(micRefusalSentence({ name: "NotReadableError" })).toMatch(/Another app/);
    expect(micRefusalSentence(new Error("odd"))).toMatch(/cannot record sound here/);
    expect(micRefusalSentence(null)).toMatch(/cannot record sound here/);
  });

  it("the gateway's sentence wins, and a bare status never shows", () => {
    const body = { detail: { code: "no_model", message: "No speech model is set up yet." } };
    expect(answerSentence(503, body)).toBe("No speech model is set up yet.");
    expect(answerSentence(413, null)).toBe(TOO_LONG);
    expect(answerSentence(500, { detail: "Internal Server Error" })).toBe(NO_ANSWER);
    expect(answerSentence(502, "html")).toBe(NO_ANSWER);
  });

  it("the flag off hides the mic, and routing off shows its sentence", () => {
    expect(availabilityOf(404, { detail: "Not Found" })).toEqual(HIDDEN);
    expect(availabilityOf(500, null)).toEqual(HIDDEN);
    expect(availabilityOf(200, { ready: true, message: null })).toEqual({ shown: true, ready: true, message: null });
    expect(availabilityOf(200, { ready: false, message: "Voice input is not available here." })).toEqual({
      shown: true,
      ready: false,
      message: "Voice input is not available here.",
    });
  });
});

describe("source fences", () => {
  const chat = read("components/AgentChat.tsx");

  it("the composer puts the transcript in the box and never sends it", () => {
    const start = chat.indexOf("const putDictation = useCallback(");
    const end = chat.indexOf("}, []);", start);
    const body = chat.slice(start, end);
    expect(start).toBeGreaterThan(0);
    expect(body).toContain("insertAtCaret(");
    expect(body).toContain("setInput(next.value)");
    expect(body).toContain("box.focus()");
    expect(body).not.toMatch(/submitText\(|sendMessage\(|steerMessage\(|handleSubmit\(/);
    // D-PZ-37: a program put the words in the box, so they carry no typed mark.
    expect(body).toContain("boxMarkRef.current = filled(boxMarkRef.current, false);");
  });

  it("the mic shows only when the flag read says so", () => {
    expect(chat).toMatch(/\{voiceAvailability\.shown && \(\s*<VoiceInputButton/);
  });

  it("Enter stops a recording and Escape cancels it, before Enter can send", () => {
    const start = chat.indexOf("const handleKeyDown = ");
    const body = chat.slice(start, chat.indexOf("};", start));
    const stop = body.indexOf("voiceRef.current?.stop()");
    const cancel = body.indexOf("voiceRef.current?.cancel()");
    const send = body.indexOf("enterSends(");
    expect(stop).toBeGreaterThan(0);
    expect(cancel).toBeGreaterThan(0);
    expect(stop).toBeLessThan(send);
    expect(cancel).toBeLessThan(send);
  });

  it("the button hands the transcript to the composer, and the hook posts one clip", () => {
    const hook = read("hooks/useVoiceDictation.ts");
    expect(hook).toContain('fetch("/api/agent/transcribe", { method: "POST", body: form })');
    expect(hook).toContain('form.append("file", clip, "clip.wav")');
    expect(hook).not.toMatch(/\/api\/agent\/chat|submitText|sendMessage/);
  });

  it("the BFF route mirrors the upload route and checks the size before the proxy", () => {
    const route = read("app/api/agent/transcribe/route.ts");
    for (const name of ["requireIdentity()", "gatewayHeaders()", "gatewayFetch(UPSTREAM", "new FormData()"]) {
      expect(route, name).toContain(name);
    }
    expect(route).not.toMatch(/["']Content-Type["']\s*:/);
    const sizeCheck = route.indexOf("clip.size > MAX_CLIP_BYTES");
    const proxy = route.indexOf('method: "POST"');
    expect(sizeCheck).toBeGreaterThan(0);
    expect(sizeCheck).toBeLessThan(proxy);
    expect(route).toContain("/chat/transcribe");
  });
});
