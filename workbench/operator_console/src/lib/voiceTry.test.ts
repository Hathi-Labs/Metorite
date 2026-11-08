// "Try speech to text" and "Try image analysis" — the pure rules (VOX-4).
//
// The panels and the BFF routes are composition. Everything a person can get
// wrong, and every limit the Console owns, is fenced here.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  type FetchLike,
  callConsoleForm,
  readConsoleEnv,
  tryTranscription,
  tryVision,
} from "./console";
import {
  DEFAULT_QUESTION,
  MAX_AUDIO_BYTES,
  MAX_AUDIO_SECONDS,
  MAX_IMAGE_BYTES,
  MAX_QUESTION_CHARS,
  audioForm,
  blockedResult,
  fileBlocker,
  lengthBlocker,
  questionBlocker,
  readTranscribeResult,
  readVisionResult,
  refusalText,
  secondsText,
  visionBlocker,
  visionForm,
} from "./voiceTry";

describe("the limits match the Console", () => {
  // ⚠️ A mirror drifts. This reads `main.py` and fails when a number differs.
  const MAIN = readFileSync(
    join(
      process.cwd(),
      "..",
      "..",
      "apps",
      "services",
      "customer_console",
      "customer_console",
      "main.py",
    ),
    "utf-8",
  );
  const constant = (name: string): string => {
    const m = new RegExp(`^${name} = (.+)$`, "m").exec(MAIN);
    expect(m, `${name} left main.py`).not.toBeNull();
    return (m as RegExpExecArray)[1].trim();
  };

  it("audio bytes, audio seconds, image bytes and question length", () => {
    expect(constant("_TRY_AUDIO_BYTES")).toBe("10 * 1024 * 1024");
    expect(MAX_AUDIO_BYTES).toBe(10 * 1024 * 1024);
    expect(Number(constant("_TRY_AUDIO_SECONDS"))).toBe(MAX_AUDIO_SECONDS);
    expect(constant("_TRY_IMAGE_BYTES")).toBe("4 * 1024 * 1024");
    expect(MAX_IMAGE_BYTES).toBe(4 * 1024 * 1024);
    expect(Number(constant("_TRY_QUESTION_MAX"))).toBe(MAX_QUESTION_CHARS);
    expect(constant("_TRY_DEFAULT_QUESTION")).toBe(`"${DEFAULT_QUESTION}"`);
  });
});

describe("a file that cannot be sent", () => {
  it("refuses no file, an empty file and a file over the cap", () => {
    expect(fileBlocker("audio", null)?.message).toContain("Choose a .wav clip");
    expect(fileBlocker("image", undefined)?.message).toContain("Choose an image");
    expect(fileBlocker("audio", { size: 0 })?.status).toBe(400);
    expect(fileBlocker("audio", { size: MAX_AUDIO_BYTES + 1 })?.status).toBe(413);
    expect(fileBlocker("image", { size: MAX_IMAGE_BYTES + 1 })?.status).toBe(413);
  });

  it("takes a file at the cap exactly", () => {
    expect(fileBlocker("audio", { size: MAX_AUDIO_BYTES })).toBeNull();
    expect(fileBlocker("image", { size: MAX_IMAGE_BYTES })).toBeNull();
  });

  it("a question of 500 characters passes and 501 does not", () => {
    expect(questionBlocker("q".repeat(500))).toBeNull();
    expect(questionBlocker("q".repeat(501))?.status).toBe(400);
    // Spaces around the question do not count: the Console trims it.
    expect(questionBlocker(`  ${"q".repeat(500)}  `)).toBeNull();
    expect(questionBlocker("")).toBeNull();
  });

  it("the vision blocker names the file first", () => {
    expect(visionBlocker(null, "q".repeat(900))?.message).toContain("Choose an image");
    expect(visionBlocker({ size: 10 }, "q".repeat(501))?.message).toContain("500");
    expect(visionBlocker({ size: 10 }, "ok")).toBeNull();
  });

  it("the header check refuses a huge body before it is read", () => {
    expect(lengthBlocker("audio", null)).toBeNull();
    expect(lengthBlocker("audio", "nope")).toBeNull();
    expect(lengthBlocker("audio", String(MAX_AUDIO_BYTES))).toBeNull();
    expect(lengthBlocker("audio", String(MAX_AUDIO_BYTES + 10_000_000))?.status).toBe(413);
    expect(lengthBlocker("image", String(MAX_IMAGE_BYTES + 10_000_000))?.status).toBe(413);
  });

  it("a blocked result has the Console's own shape", () => {
    const r = blockedResult({ status: 413, message: "too big" });
    expect(r.status).toBe(413);
    expect(JSON.parse(r.body)).toEqual({ detail: "too big" });
  });
});

describe("the multipart bodies", () => {
  it("the speech body holds one file", () => {
    const form = audioForm(new File([new Uint8Array([1, 2, 3])], "clip.wav"));
    expect([...form.keys()]).toEqual(["file"]);
    expect((form.get("file") as File).name).toBe("clip.wav");
  });

  it("the vision body holds a file and the trimmed question, or the default", () => {
    const file = new File([new Uint8Array([1])], "a.png");
    expect(visionForm(file, "  What is it?  ").get("question")).toBe("What is it?");
    expect(visionForm(file, "   ").get("question")).toBe(DEFAULT_QUESTION);
  });
});

describe("reading the Console's answers", () => {
  it("reads a transcript", () => {
    expect(
      readTranscribeResult({
        tier: "tier-stt",
        model: "elevenlabs/scribe_v2",
        transcript: "namaste",
        seconds: 3,
        latency_ms: 812,
      }),
    ).toEqual({
      tier: "tier-stt",
      model: "elevenlabs/scribe_v2",
      transcript: "namaste",
      seconds: 3,
      latencyMs: 812,
    });
  });

  it("keeps null seconds as null, and refuses a shape it cannot read", () => {
    expect(
      readTranscribeResult({ model: "m", transcript: "", seconds: null })?.seconds,
    ).toBeNull();
    expect(readTranscribeResult({ model: "m" })).toBeNull();
    expect(readTranscribeResult("text")).toBeNull();
    expect(readTranscribeResult(null)).toBeNull();
  });

  it("reads an image answer with its tokens and cost", () => {
    expect(
      readVisionResult({
        tier: "tier-vision",
        model: "gemini/gemini-3.1-flash-lite",
        text: "A red square.",
        usage: { input_tokens: 11, output_tokens: 4 },
        latency_ms: 900,
        vendor_cost_usd: "0.0000123",
      }),
    ).toEqual({
      tier: "tier-vision",
      model: "gemini/gemini-3.1-flash-lite",
      text: "A red square.",
      inputTokens: 11,
      outputTokens: 4,
      latencyMs: 900,
      vendorCostUsd: "0.0000123",
    });
    expect(readVisionResult({ model: "m", text: "t" })?.vendorCostUsd).toBeNull();
    expect(readVisionResult({ text: "t" })).toBeNull();
  });

  it("says the length in words, and says why when there is none", () => {
    expect(secondsText(3)).toBe("3 seconds");
    expect(secondsText(1)).toBe("1 second");
    expect(secondsText(2.54)).toBe("2.5 seconds");
    expect(secondsText(null)).toContain("not measured");
  });
});

describe("a refusal shows the provider's words", () => {
  it("reads the detail of a JSON refusal", () => {
    expect(
      refusalText(
        502,
        JSON.stringify({
          detail: "upstream provider error (elevenlabs): Invalid API key for scribe_v2",
        }),
      ),
    ).toBe(
      "The Console refused (502): upstream provider error (elevenlabs): Invalid API key for scribe_v2",
    );
  });

  it("reads an error field, and shows a body it cannot parse as it came", () => {
    expect(refusalText(401, JSON.stringify({ error: "operator session required" }))).toContain(
      "operator session required",
    );
    expect(refusalText(500, "<html>boom</html>")).toContain("<html>boom</html>");
    expect(refusalText(500, "")).toContain("no reason given");
  });
});

describe("the Console client sends the multipart call", () => {
  const ENV = readConsoleEnv({
    CUSTOMER_CONSOLE_URL: "https://console.internal",
    CUSTOMER_CONSOLE_OPERATOR_TOKEN: "op-secret-token",
  });

  function capture() {
    const calls: { url: string; init: { method: string; headers: Record<string, string>; body?: unknown } }[] = [];
    const fetchImpl = (async (url: string, init: never) => {
      calls.push({ url, init });
      return { status: 200, text: async () => "{}" };
    }) as unknown as FetchLike;
    return { calls, fetchImpl };
  }

  it("posts a FormData with no JSON content type, and the token only in the header", async () => {
    const { calls, fetchImpl } = capture();
    const form = audioForm(new File([new Uint8Array([1])], "c.wav"));
    await tryTranscription(form, { env: ENV, fetchImpl });
    expect(calls[0].url).toBe("https://console.internal/catalog/transcribe/try");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe(form);
    expect(calls[0].init.headers.Authorization).toBe("Bearer op-secret-token");
    expect(calls[0].init.headers["Content-Type"]).toBeUndefined();
  });

  it("the vision call goes to its own route with the caller's session", async () => {
    const { calls, fetchImpl } = capture();
    await tryVision(visionForm(new File([new Uint8Array([1])], "a.png"), ""), {
      env: ENV,
      fetchImpl,
      authToken: "cc_sess_abc",
    });
    expect(calls[0].url).toBe("https://console.internal/catalog/vision/try");
    expect(calls[0].init.headers.Authorization).toBe("Bearer cc_sess_abc");
  });

  it("fails closed when the Console is not configured", async () => {
    await expect(
      callConsoleForm("/catalog/vision/try", new FormData(), {
        env: readConsoleEnv({}),
        fetchImpl: capture().fetchImpl,
      }),
    ).rejects.toThrow();
  });
});
