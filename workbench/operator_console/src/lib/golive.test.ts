// The go-live rail — six steps judged from the catalog, honestly.
//
// 🔴 The rail is the answer to "the system is confusing", so the thing to
// fence is that it never lies in either direction: a green it cannot verify,
// or a red on a state that is actually fine.

import { describe, expect, it } from "vitest";

import type { AiCatalog, CatalogModel, ProviderAccount } from "./contract";
import { EMPTY_CATALOG } from "./contract";
import { goLiveSteps, railSummary, stepTone } from "./golive";

const ACCOUNT = (over: Partial<ProviderAccount> = {}): ProviderAccount => ({
  id: "a1", provider: "deepseek", label: null, apiBase: null, orgSlug: null,
  createdAt: null, revokedAt: null, health: "unknown", lastCheckedAt: null,
  healthNote: null, ...over,
});

const MODEL = (over: Partial<CatalogModel> = {}): CatalogModel => ({
  id: "deepseek/chat", label: "DeepSeek", provider: "deepseek",
  kinds: ["chat"], contextWindow: null, maxOutput: null,
  inputPer1M: null, outputPer1M: null, cachedInputPer1M: null,
  perMinuteUsd: null, perCharacterUsd: null, perImageUsd: null,
  // 023 — the window and the context tier. Null everywhere: these
  // fixtures predate the columns and no case here depends on them.
  inputOffpeakPer1M: null, outputOffpeakPer1M: null,
  cachedInputOffpeakPer1M: null,
  offpeakStartUtc: null, offpeakEndUtc: null,
  contextTierThreshold: null, inputLongPer1M: null,
  outputLongPer1M: null, cachedInputLongPer1M: null,
  description: "", declared: true, ...over,
});

const CAT = (over: Partial<AiCatalog> = {}): AiCatalog => ({
  ...EMPTY_CATALOG,
  tasks: [{ slug: "chat", label: "Chat", natural_unit: "tokens" }],
  ...over,
});

const step = (cat: AiCatalog, key: string) =>
  goLiveSteps(cat).find((s) => s.key === key)!;

describe("the six steps, on an empty console", () => {
  const steps = goLiveSteps(CAT());

  it("orders them the way the work goes", () => {
    expect(steps.map((s) => s.key)).toEqual(
      ["keys", "models", "tiers", "prices", "customer", "flags"],
    );
    expect(steps.map((s) => s.n)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("🔴 marks the buildable steps TODO and says what fails", () => {
    expect(step(CAT(), "keys").state).toBe("todo");
    expect(step(CAT(), "keys").detail).toContain("every AI call fails");
    expect(step(CAT(), "models").state).toBe("todo");
    expect(step(CAT(), "tiers").state).toBe("todo");
  });

  it("🔴 NEVER claims the flags or the customer step from here", () => {
    // The flags live in a box's environment. A green this page cannot
    // measure is the health-dot lie again.
    expect(step(CAT(), "flags").state).toBe("info");
    expect(step(CAT(), "customer").state).toBe("info");
    expect(step(CAT(), "flags").detail).toContain("Owner acts");
  });
});

describe("step 1 — keys", () => {
  it("is done once a live PLATFORM key exists, and names the vendors", () => {
    const s = step(CAT({ accounts: [ACCOUNT()] }), "keys");
    expect(s.state).toBe("done");
    expect(s.detail).toContain("deepseek");
  });

  it("🔴 does not count a revoked or BYOK key", () => {
    const dead = CAT({ accounts: [ACCOUNT({ revokedAt: "2026-08-01T00:00:00Z" })] });
    const byok = CAT({ accounts: [ACCOUNT({ orgSlug: "acme" })] });
    expect(step(dead, "keys").state).toBe("todo");
    expect(step(byok, "keys").state).toBe("todo");
  });
});

describe("step 2 — models", () => {
  it("is PARTIAL while any declared model has no vendor price", () => {
    const s = step(CAT({ models: [MODEL()] }), "models");
    expect(s.state).toBe("partial");
    // The consequence in words: those calls cannot be costed.
    expect(s.detail.toLowerCase()).toContain("cost");
  });

  it("is done when every declared model carries the vendor's price", () => {
    expect(step(CAT({ models: [MODEL({ inputPer1M: 3 })] }), "models").state)
      .toBe("done");
  });

  it("ignores an undeclared research row", () => {
    expect(step(CAT({ models: [MODEL({ declared: false })] }), "models").state)
      .toBe("todo");
  });
});

describe("step 4 — prices", () => {
  const BOUND = CAT({
    accounts: [ACCOUNT()],
    models: [MODEL({ inputPer1M: 3 })],
    tiers: [{
      slug: "fast", label: "Fast", blurb: "", registered: true, customerVisible: true,
      jobs: [{ tier: "fast", task: "chat",
        chain: [{ model: "deepseek/chat", rank: 1 }] }],
    }],
  });

  it("🔴 names the bound tier jobs that would bill NOTHING", () => {
    // D67: the customer buys the TIER, so what goes unpriced is the
    // (tier, job) pair - the detail names it in those words.
    const s = step(BOUND, "prices");
    expect(s.state).toBe("todo");
    expect(s.detail).toContain("fast (chat)");
    expect(s.detail).toContain("NOTHING");
  });

  const DECIDED = {
    ...BOUND,
    tierRates: [{
      tier: "fast", task: "chat", unit: "tokens",
      mode: "absorbed" as const, inputPer1k: "0", outputPer1k: "0",
      cachedInputPer1k: "0", creditsPerUnit: "0",
    }],
  };

  it("counts ABSORBED as decided — free on purpose is a decision", () => {
    const s = step(
      {
        ...DECIDED,
        creditPrice: {
          inrPerCredit: "1", usdToInr: "88", effectiveFrom: null,
        },
      },
      "prices",
    );
    expect(s.state).toBe("done");
  });

  it("🔴 stays PARTIAL while the credit itself has no rupee price", () => {
    // Both halves of H-42: the tier rates say how many credits a call
    // burns, and `credit_price` says what a credit sells for. With only
    // the first, a bank transfer has no official conversion — the rail
    // must not read green.
    const s = step(DECIDED, "prices");
    expect(s.state).toBe("partial");
    expect(s.detail).toContain("H-42");
    expect(s.detail).toContain("no rupee price");
  });

  it("waits quietly while nothing is bound yet", () => {
    expect(step(CAT(), "prices").state).toBe("info");
  });
});

describe("the summary line", () => {
  it("stays null while anything derivable needs doing", () => {
    expect(railSummary(goLiveSteps(CAT()))).toBeNull();
  });

  it("appears only when every derivable step is green", () => {
    const done = goLiveSteps(CAT()).map((s) =>
      s.state === "info" ? s : { ...s, state: "done" as const });
    expect(railSummary(done)).toContain("configured");
  });
});

describe("tones", () => {
  it("maps states to the shared vocabulary, info staying neutral", () => {
    expect(stepTone("done")).toBe("ok");
    expect(stepTone("partial")).toBe("warn");
    expect(stepTone("todo")).toBe("danger");
    expect(stepTone("info")).toBe("neutral");
  });
});

describe("an unreadable credential list", () => {
  it("🔴 says UNKNOWN — never 'every AI call fails' from absent evidence", () => {
    const step = goLiveSteps({ ...EMPTY_CATALOG, accountsKnown: false })
      .find((s) => s.key === "keys");
    expect(step?.state).toBe("todo");
    expect(step?.detail).toContain("UNKNOWN");
    expect(step?.detail).not.toContain("every AI call fails");
  });
});
