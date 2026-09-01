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

import type { ProviderAccount } from "./contract";
import { capableModelsFor, providerOf, vendorWarning } from "./readiness";

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

describe("the vendor half of a model id, checked before it is saved", () => {
  // 🔴 The declare form takes free text. A model declared under a vendor we
  // hold no key for is accepted, binds to a tier happily, and answers 503 on
  // the first customer request — four steps after the mistake was made.
  const ARMED: ProviderAccount[] = [
    {
      id: "c1", provider: "anthropic", label: null, apiBase: null,
      orgSlug: null, createdAt: null, revokedAt: null, health: "unknown",
      lastCheckedAt: null, healthNote: null,
    },
  ];

  it("says nothing while the box is empty", () => {
    expect(vendorWarning("", ARMED)).toBeNull();
    expect(vendorWarning("   ", ARMED)).toBeNull();
  });

  it("says nothing when we hold a live key for that vendor", () => {
    expect(vendorWarning("anthropic/claude-sonnet-4", ARMED)).toBeNull();
  });

  it("🔴 warns for a vendor we know but hold no key for", () => {
    const w = vendorWarning("gemini/gemini-2.5-flash", ARMED);
    expect(w).toContain("Google Gemini");
    expect(w).toContain("503");
  });

  it("🔴 warns HARDER for a vendor nobody has heard of", () => {
    // This is the `google` and `together` case. The word looks right, the
    // form accepts it, and no card on the providers page will ever match.
    const w = vendorWarning("google/gemini-2.5-flash", ARMED);
    expect(w).toContain("litellm");
  });

  it("warns when the id names no vendor at all", () => {
    expect(vendorWarning("gpt-4o", ARMED)).toContain("no vendor");
  });

  it("🔴 does not count a REVOKED key as coverage", () => {
    const dead: ProviderAccount[] = [
      { ...ARMED[0], revokedAt: "2026-08-01T00:00:00Z" },
    ];
    expect(vendorWarning("anthropic/claude-sonnet-4", dead)).not.toBeNull();
  });

  it("🔴 does not count a BYOK key as coverage", () => {
    // A key scoped to one organization leaves every other tenant with no AI,
    // and reads exactly like coverage in a list.
    const byok: ProviderAccount[] = [{ ...ARMED[0], orgSlug: "acme" }];
    expect(vendorWarning("anthropic/claude-sonnet-4", byok)).not.toBeNull();
  });

  it("reads the vendor from the FIRST slash", () => {
    // `openrouter/anthropic/claude-3` is one OpenRouter model, not an
    // Anthropic one, and the credential it needs is OpenRouter's.
    expect(vendorWarning("openrouter/anthropic/claude-3", ARMED))
      .toContain("OpenRouter");
  });
});
