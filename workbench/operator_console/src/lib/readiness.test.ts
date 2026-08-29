// Model identity — reading a vendor out of an id, and offering only what works.
//
// 🔴 **The matrix tests that used to fill this file are gone with the matrix.**
// They tested one cell per (tier, task), and a tier now holds an ordered chain
// of models — a list, not a square. `fallback.test.ts` is the fence on that
// judgement, and it is the only one, because two modules answering "what should
// I do next" would disagree inside a month.
//
// What is left is the part that was never about the matrix, and the vendor
// split is the one with real money behind it: a wrong vendor on a chip claims
// a credential we may not hold.

import { describe, expect, it } from "vitest";

import { capableModelsFor, providerOf } from "./readiness";

describe("which vendor a model belongs to", () => {
  it("reads the part before the first slash", () => {
    expect(providerOf("anthropic/claude-sonnet-4")).toBe("anthropic");
  });

  it("🔴 splits on the FIRST slash, not the last", () => {
    // `openrouter/anthropic/claude-3` is ONE OpenRouter model. Attributing it
    // to Anthropic makes the chip claim a credential we may not hold, and the
    // outage simulator then reports a survival that will not happen.
    expect(providerOf("openrouter/anthropic/claude-3")).toBe("openrouter");
  });

  it("treats an id with no slash as its own vendor", () => {
    // `whisper-1` is served by whoever the platform credential belongs to.
    // Guessing would be worse than showing the id back.
    expect(providerOf("whisper-1")).toBe("whisper-1");
  });

  it("survives an empty id and stray whitespace", () => {
    expect(providerOf("")).toBe("");
    expect(providerOf("  openai/gpt-4o  ")).toBe("openai");
  });

  it("does not read a LEADING slash as an empty vendor", () => {
    // ⚠️ `indexOf` returns 0 there, and a naive `> -1` test would yield "".
    expect(providerOf("/gpt-4o")).toBe("/gpt-4o");
  });
});

describe("which models may be offered for a job", () => {
  const CAPS = [
    { model: "openai/gpt-4o", task: "chat" },
    { model: "anthropic/haiku", task: "chat" },
    { model: "openai/whisper", task: "transcribe" },
    { model: "openai/gpt-4o", task: "chat" },
  ];

  it("🔴 offers ONLY models that declared the task", () => {
    // The old form was free text, so a typo produced a tier that looked
    // correct and 500d on the first request. A list of capable models makes
    // that state unreachable by hand.
    expect(capableModelsFor(CAPS, "chat")).toEqual([
      "anthropic/haiku",
      "openai/gpt-4o",
    ]);
  });

  it("de-duplicates, so one model is offered once", () => {
    expect(capableModelsFor(CAPS, "chat").filter((m) => m === "openai/gpt-4o"))
      .toHaveLength(1);
  });

  it("returns nothing for a task nothing can do, rather than everything", () => {
    expect(capableModelsFor(CAPS, "speak")).toEqual([]);
  });
});
