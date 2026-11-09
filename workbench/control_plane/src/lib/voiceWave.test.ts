/**
 * The picture of a recording (VOX-7, `projects_ai_chat.md` §31). The vitest rig
 * has no DOM, so these cases drive the pure rules of `lib/voiceWave.ts`, render
 * the wave to a string, and read the wiring of the button from its source.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import VoiceWave from "@/components/VoiceWave";
import { insertAtCaret, MAX_SECONDS } from "./voiceDictation";
import {
  HINT_KEY,
  hintSeen,
  gainLevel,
  litSegments,
  markHintSeen,
  ringOf,
  secondsLeft,
  smoothLevel,
  WAVE_H,
  WAVE_LAYERS,
  wavePath,
} from "./voiceWave";

const read = (f: string) => readFileSync(join(process.cwd(), "src", f), "utf-8");
const ys = (d: string) => d.split(/[ML]/).filter(Boolean).map((p) => Number(p.split(",")[1]));
const swing = (d: string) => Math.max(...ys(d)) - Math.min(...ys(d));

describe("the three layered lines", () => {
  it("there are three lines, the front one strongest, all in the one accent", () => {
    expect(WAVE_LAYERS).toHaveLength(3);
    const opacity = WAVE_LAYERS.map((l) => l.opacity);
    const weight = WAVE_LAYERS.map((l) => l.weight);
    expect([...opacity].sort((a, b) => a - b)).toEqual(opacity);
    expect([...weight].sort((a, b) => a - b)).toEqual(weight);
    expect(new Set(opacity).size).toBe(3);
  });

  it("the swing follows the level: quiet is nearly flat, loud is wide", () => {
    const front = WAVE_LAYERS[2];
    const quiet = swing(wavePath(front, 0.37, 0));
    const loud = swing(wavePath(front, 0.37, 1));
    expect(loud).toBeGreaterThan(quiet * 3);
    expect(loud).toBeLessThanOrEqual(WAVE_H);
  });

  it("the three layers differ in frequency, speed and strength", () => {
    for (const k of ["freq", "amp", "weight", "opacity"] as const) {
      expect(new Set(WAVE_LAYERS.map((l) => l[k])).size).toBe(3);
    }
    expect(new Set(WAVE_LAYERS.map((l) => Math.abs(l.speed))).size).toBe(3);
    expect(WAVE_LAYERS.map((l) => l.opacity)).toEqual([0.35, 0.6, 1]);
  });

  it("silence still shows three soft lines that breathe", () => {
    for (const layer of WAVE_LAYERS) {
      const a = swing(wavePath(layer, 0.2, 0));
      const b = swing(wavePath(layer, 1.7, 0));
      expect(a).toBeGreaterThan(1);
      expect(Math.abs(a - b) + Math.abs(swing(wavePath(layer, 3.1, 0)) - a)).toBeGreaterThan(0);
    }
  });

  it("a gain lifts a normal-speech level to fill 60 to 80 per cent of the band", () => {
    expect(gainLevel(0)).toBe(0);
    expect(gainLevel(0.2)).toBeGreaterThanOrEqual(0.5);
    expect(gainLevel(0.35)).toBeGreaterThanOrEqual(0.6);
    expect(gainLevel(0.35)).toBeLessThanOrEqual(0.9);
    expect(gainLevel(5)).toBe(1);
  });

  it("the line starts and ends on the middle, so it never touches the edge", () => {
    const y = ys(wavePath(WAVE_LAYERS[1], 1.2, 1));
    expect(y[0]).toBeCloseTo(WAVE_H / 2, 1);
    expect(y[y.length - 1]).toBeCloseTo(WAVE_H / 2, 1);
  });

  it("the level shown rises fast and falls slowly, and a bad level reads as silence", () => {
    expect(smoothLevel(0, 1)).toBeGreaterThan(1 - smoothLevel(1, 0));
    expect(smoothLevel(0.5, Number.NaN)).toBeLessThan(0.5);
    expect(ringOf(0).scale).toBeLessThan(ringOf(1).scale);
    expect(ringOf(2)).toEqual(ringOf(1));
  });
});

describe("the timer", () => {
  it("warns in the last 15 s of the limit, and says how many are left", () => {
    expect(secondsLeft(0)).toBeNull();
    expect(secondsLeft(MAX_SECONDS - 16)).toBeNull();
    expect(secondsLeft(MAX_SECONDS - 15)).toBe(15);
    expect(secondsLeft(MAX_SECONDS - 1)).toBe(1);
    expect(secondsLeft(MAX_SECONDS + 3)).toBe(0);
  });
});

describe("the first-recording hint", () => {
  const fake = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
  };

  it("shows once: a second read finds the mark", () => {
    const store = fake();
    expect(hintSeen(store)).toBe(false);
    markHintSeen(store);
    expect(hintSeen(store)).toBe(true);
    expect(store.getItem(HINT_KEY)).toBe("1");
  });

  it("a blocked store throws nowhere", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(hintSeen(blocked)).toBe(false);
    expect(() => markHintSeen(blocked)).not.toThrow();
    expect(hintSeen(null)).toBe(false);
  });
});

describe("reduced motion", () => {
  const ring = { current: null };
  const level = { current: 0.5 };

  it("draws a static meter and no animated line", () => {
    const html = renderToStaticMarkup(createElement(VoiceWave, { levelRef: level, reduced: true, ringRef: ring }));
    expect(html).toContain('data-chat-voice-wave="meter"');
    expect(html).not.toContain("<path");
    expect(html).not.toContain("<svg");
    expect(litSegments(0, 10)).toBe(0);
    expect(litSegments(1, 10)).toBe(10);
  });

  it("with motion allowed, it draws the three lines", () => {
    const html = renderToStaticMarkup(createElement(VoiceWave, { levelRef: level, reduced: false, ringRef: ring }));
    expect(html).toContain('data-chat-voice-wave="lines"');
    expect(html.match(/<path/g)).toHaveLength(3);
  });

  it("the source runs no frame loop in the reduced branch", () => {
    const src = read("components/VoiceWave.tsx");
    const branch = src.slice(src.indexOf("if (reduced) {"), src.indexOf("const t0 = performance.now()"));
    expect(branch).not.toContain("requestAnimationFrame");
    expect(src).toContain("prefers-reduced-motion: reduce");
  });
});

describe("source fences", () => {
  const wave = read("components/VoiceWave.tsx");
  const button = read("components/VoiceInputButton.tsx");
  const hook = read("hooks/useVoiceDictation.ts");

  it("the level reaches the drawing in a ref, with no render per audio block", () => {
    expect(hook).toContain("levelRef.current = level;");
    expect(hook).not.toMatch(/dispatch\(\{ type: "level"/);
    expect(wave).toContain("levelRef.current");
    expect(wave).not.toMatch(/useState|setState/);
  });

  it("the loop stops when recording ends, and nothing leaks", () => {
    expect(wave).toContain("requestAnimationFrame(draw)");
    expect(wave).toContain("return () => cancelAnimationFrame(frame);");
    expect(wave).toContain("return () => clearInterval(id);");
    expect(wave).toContain("removeEventListener");
  });

  it("the colours come from the one look: no hex, no status token", () => {
    for (const src of [wave, button, read("lib/voiceWave.ts")]) {
      const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
      expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(code).not.toMatch(/--info|--violet|text-info|text-violet|stroke-info|stroke-violet/);
    }
    expect(wave).toContain("text-primary");
    expect(wave).toContain('stroke="currentColor"');
  });

  it("the stop control: label, ring that takes no click, discard, timer warning", () => {
    expect(button).toContain('variant={recording ? "primary" : "ghost"}');
    expect(button).toContain('aria-label="Discard recording"');
    expect(button).toContain("pointer-events-none absolute -inset-1 rounded-full");
    expect(button).toContain("text-warning");
    expect(button).toContain("s left");
    expect(button).toContain("onClick={() => voice.cancel()}");
    expect(button).toContain("HINT_MS");
  });

  it("the cluster takes room with basis-0, so the bar keeps its height", () => {
    expect(button).toContain("flex min-w-fit flex-[3] basis-0 items-center");
  });

  it("the pickers give the row to the wave, and the box says what the mic does", () => {
    const chat = read("components/AgentChat.tsx");
    expect(chat).toContain('const voiceBusy = voicePhase === "recording" || voicePhase === "transcribing";');
    expect(chat).toContain('style={voiceBusy ? { display: "none" } : undefined}');
    expect(chat).toContain("Listening… tap ■ to finish");
    expect(chat).toMatch(/voicePhase === "transcribing"\s*\? "Transcribing…"/);
    expect(button).toContain("data-chat-composer");
    expect(chat).toContain("minHeight: barLock.current");
  });

  it("the composer keeps the box mounted while the mic records", () => {
    const chat = read("components/AgentChat.tsx");
    expect(chat).not.toMatch(/voicePhase\s*(===|!==)\s*"recording"\s*(&&|\?)\s*\(?\s*</);
    expect(chat).toContain("<textarea ref={inputRef}");
  });
});

describe("append: dictate, type, dictate again", () => {
  type Box = { value: string; start: number; end: number };
  /** What the composer does: read the caret of the box and put the words there. */
  const dictate = (box: Box, words: string): Box => {
    const next = insertAtCaret(box.value, box.start, box.end, words);
    return { value: next.value, start: next.caret, end: next.caret };
  };
  const type = (box: Box, chars: string): Box => ({
    value: box.value.slice(0, box.start) + chars + box.value.slice(box.end),
    start: box.start + chars.length,
    end: box.start + chars.length,
  });

  it("hello, then world typed, then again at the end", () => {
    let box: Box = { value: "", start: 0, end: 0 };
    box = dictate(box, "hello");
    expect(box.value).toBe("hello");
    box = type(box, " world");
    expect(box.value).toBe("hello world");
    box = dictate(box, "again");
    expect(box.value).toBe("hello world again");
    expect(box.start).toBe("hello world again".length);
  });

  it("with the caret moved after hello, again lands there and the draft stays whole", () => {
    let box: Box = { value: "", start: 0, end: 0 };
    box = dictate(box, "hello");
    box = type(box, " world");
    box = { ...box, start: 5, end: 5 };
    box = dictate(box, "again");
    expect(box.value).toBe("hello again world");
    expect(box.start).toBe("hello again".length);
    expect(box.value.indexOf("hello")).toBeLessThan(box.value.indexOf("again"));
    expect(box.value.indexOf("again")).toBeLessThan(box.value.indexOf("world"));
    box = dictate(box, "and more");
    expect(box.value).toBe("hello again and more world");
  });
});
