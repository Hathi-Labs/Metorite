/**
 * The one audio seam (`lib/audioCapture.ts`, VOX-1). The vitest rig runs in
 * node with no DOM, so these cases drive the pure parts: the WAV bytes, the
 * resampler and the level meter. The capture itself needs a browser.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { CLIP_RATE, downsampleToInt16, encodeWav, joinBlocks, levelOf } from "./audioCapture";

const read = (f: string) => readFileSync(join(process.cwd(), "src", f), "utf-8");
const ascii = (b: Uint8Array, at: number, n: number) => String.fromCharCode(...b.slice(at, at + n));

describe("the WAV clip", () => {
  it("writes the 44-byte RIFF header of 16 kHz mono 16-bit PCM", () => {
    const pcm = new Int16Array([0, 32767, -32768, 1]);
    const wav = encodeWav(pcm, CLIP_RATE);
    const view = new DataView(wav.buffer);

    expect(wav.length).toBe(44 + 8);
    expect(ascii(wav, 0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(36 + 8);
    expect(ascii(wav, 8, 4)).toBe("WAVE");
    expect(ascii(wav, 12, 4)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint32(28, true)).toBe(32000); // bytes each second
    expect(view.getUint16(32, true)).toBe(2); // bytes each frame
    expect(view.getUint16(34, true)).toBe(16); // bits each sample
    expect(ascii(wav, 36, 4)).toBe("data");
    expect(view.getUint32(40, true)).toBe(8);
  });

  it("writes the samples little-endian after the header", () => {
    const wav = encodeWav(new Int16Array([0, 32767, -32768, 1]), CLIP_RATE);
    expect([...wav.slice(44)]).toEqual([0x00, 0x00, 0xff, 0x7f, 0x00, 0x80, 0x01, 0x00]);
  });

  it("two minutes at 16 kHz are about 3.8 MB", () => {
    const wav = encodeWav(new Int16Array(120 * CLIP_RATE), CLIP_RATE);
    expect(wav.length).toBe(44 + 120 * 32000);
    expect(wav.length).toBeLessThan(10 * 1024 * 1024);
  });
});

describe("the resampler", () => {
  it("takes 48 kHz down to 16 kHz with the mean of each window", () => {
    const input = new Float32Array([0.3, 0.3, 0.3, -0.6, -0.6, -0.6, 1, 1, 1]);
    const out = downsampleToInt16(input, 48000, 16000);
    expect(out.length).toBe(3);
    expect(out[0]).toBe(Math.trunc(0.3 * 0x7fff));
    expect(out[1]).toBe(Math.trunc(-0.6 * 0x8000));
    expect(out[2]).toBe(0x7fff);
  });

  it("clamps a sample past full scale", () => {
    const out = downsampleToInt16(new Float32Array([2, -2]), 16000, 16000);
    expect([...out]).toEqual([0x7fff, -0x8000]);
  });

  it("joins the blocks in order", () => {
    const one = joinBlocks([new Float32Array([1, 2]), new Float32Array([3])]);
    expect([...one]).toEqual([1, 2, 3]);
  });
});

describe("the level meter", () => {
  it("reads silence as 0 and a loud block as 1", () => {
    expect(levelOf(new Float32Array(128))).toBe(0);
    expect(levelOf(new Float32Array(128).fill(0.9))).toBe(1);
    expect(levelOf(new Float32Array(0))).toBe(0);
  });

  it("reads quiet speech as part of the bar", () => {
    const level = levelOf(new Float32Array(128).fill(0.1));
    expect(level).toBeGreaterThan(0.2);
    expect(level).toBeLessThan(0.4);
  });
});

describe("source fences", () => {
  it("the chat records PCM with the Web Audio API, never with MediaRecorder", () => {
    for (const f of ["lib/audioCapture.ts", "hooks/useVoiceDictation.ts", "components/VoiceInputButton.tsx"]) {
      const code = read(f).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
      expect(code, f).not.toMatch(/MediaRecorder/);
    }
    expect(read("lib/audioCapture.ts")).toMatch(/createScriptProcessor\(/);
  });

  it("Notes reads the mic list from the one seam, and keeps no copy", () => {
    const notes = read("app/notes/lib/recorder.ts");
    expect(notes).toContain('export { listMicrophones, type MicDevice } from "@/lib/audioCapture";');
    expect(notes).not.toMatch(/function listMicrophones/);
    expect(read("app/notes/components/MicPicker.tsx")).toMatch(/listMicrophones/);
  });

  it("the seam imports no app", () => {
    const seam = read("lib/audioCapture.ts");
    expect(seam).not.toMatch(/from "@\/app\//);
  });
});
