// The mapping seam — Console JSON becomes the contract, exactly once.
//
// 🔴 **This is the file that makes building the UI ahead of the backend safe**,
// and until now it had no test. Every screen reads `contract.ts` shapes, so a
// mistake here is invisible in the components and wrong on every page at once.
//
// ⚠️ **The subject is what the mapping INVENTS.** A missing profile row, a
// price that arrives as a string, a binding with no rank — each has an obvious
// wrong answer that renders as a confident fact.

import { describe, expect, it } from "vitest";

import { accountsFromWire, catalogFromWire } from "./read";

const TASKS = [{ slug: "chat", label: "Answer questions", natural_unit: "1k tokens" }];

const WIRE = (over: Record<string, unknown> = {}) =>
  ({
    tasks: TASKS,
    capabilities: [{ model: "anthropic/sonnet", task: "chat" }],
    bindings: [],
    rates: [],
    ...over,
  }) as never;

describe("what a model IS", () => {
  it("🔴 leaves every measurement NULL when there is no profile row", () => {
    // Nothing is seeded. A model with no profile must render as em dashes, and
    // the mapping must not substitute a zero or a guess to fill the card.
    const m = catalogFromWire(WIRE()).models[0];
    expect(m.contextWindow).toBeNull();
    expect(m.maxOutput).toBeNull();
    expect(m.inputPer1M).toBeNull();
    expect(m.description).toBe("");
  });

  it("uses the id as the label until somebody records one", () => {
    // Better than a prettified guess, which stops matching what the Router uses
    // and then two screens disagree about what a model is called.
    expect(catalogFromWire(WIRE()).models[0].label).toBe("anthropic/sonnet");
  });

  it("takes the window, the cap and the vendor price from the profile", () => {
    const m = catalogFromWire(
      WIRE({
        profiles: [{
          model: "anthropic/sonnet", label: "Claude Sonnet 4",
          context_window: 200000, max_output: 64000,
          vendor_input_per_1m_usd: "3.0000",
          vendor_output_per_1m_usd: "15.0000",
          description: "the workhorse", reads_images: true, thinks_first: false,
        }],
      }),
    ).models[0];
    expect(m.label).toBe("Claude Sonnet 4");
    expect(m.contextWindow).toBe(200000);
    expect(m.inputPer1M).toBe(3);
    expect(m.outputPer1M).toBe(15);
    expect(m.description).toBe("the workhorse");
    // 013: absent from this older wire shape → null, never zero. A zero
    // cached rate would make every cache-hitting call look free to cost.
    expect(m.cachedInputPer1M).toBeNull();
  });

  it("🔴 turns the two profile FLAGS into filterable kinds", () => {
    // The whole reason the columns exist. "Reads images" is not a task — no
    // tier binds it — so it can only reach the filter chips through here, and
    // D-AI-2's image rule turns on exactly this query.
    const m = catalogFromWire(
      WIRE({
        profiles: [{
          model: "anthropic/sonnet", label: null, context_window: null,
          max_output: null, vendor_input_per_1m_usd: null,
          vendor_output_per_1m_usd: null, description: "",
          reads_images: true, thinks_first: true,
        }],
      }),
    ).models[0];
    expect(m.kinds).toContain("vision");
    expect(m.kinds).toContain("reasoning");
    expect(m.kinds).toContain("chat");
  });

  it("does not claim a kind the profile says false to", () => {
    const m = catalogFromWire(
      WIRE({
        profiles: [{
          model: "anthropic/sonnet", label: null, context_window: null,
          max_output: null, vendor_input_per_1m_usd: null,
          vendor_output_per_1m_usd: null, description: "",
          reads_images: false, thinks_first: false,
        }],
      }),
    ).models[0];
    expect(m.kinds).not.toContain("vision");
    expect(m.kinds).not.toContain("reasoning");
  });

  it("🔴 treats an unparseable price as UNKNOWN, never as zero", () => {
    // A zero renders as "free", which is a bargain somebody might act on.
    const m = catalogFromWire(
      WIRE({
        profiles: [{
          model: "anthropic/sonnet", label: null, context_window: null,
          max_output: null, vendor_input_per_1m_usd: "not-a-number",
          vendor_output_per_1m_usd: null, description: "",
          reads_images: false, thinks_first: false,
        }],
      }),
    ).models[0];
    expect(m.inputPer1M).toBeNull();
  });

  it("ignores a profile for a model that is not declared", () => {
    // The catalog is the set of models we can CALL. A profile somebody filled
    // in while researching is not a model the Router will accept.
    const cat = catalogFromWire(
      WIRE({
        profiles: [{
          model: "openai/researched", label: "Researched", context_window: 1,
          max_output: null, vendor_input_per_1m_usd: null,
          vendor_output_per_1m_usd: null, description: "", reads_images: false,
          thinks_first: false,
        }],
      }),
    );
    expect(cat.models.map((m) => m.id)).toEqual(["anthropic/sonnet"]);
  });
});

describe("what is priced", () => {
  it("counts a card that is absorbed, and not one that is unpriced", () => {
    // D19.2: `absorbed` is a decision and `unpriced` is an omission. Drawing
    // them the same is how a draft price ships.
    const priced = catalogFromWire(
      WIRE({ rates: [{ model: "anthropic/sonnet", task: "chat", pricing_mode: "absorbed" }] }),
    ).models[0].priced;
    const unpriced = catalogFromWire(
      WIRE({ rates: [{ model: "anthropic/sonnet", task: "chat", pricing_mode: "unpriced" }] }),
    ).models[0].priced;
    expect(priced).toBe(true);
    expect(unpriced).toBe(false);
  });
});

describe("chains", () => {
  it("groups binding rows back into one ordered chain per job", () => {
    const tiers = catalogFromWire(
      WIRE({
        bindings: [
          { tier: "fast", task: "chat", model: "a/one", rank: 1 },
          { tier: "fast", task: "chat", model: "b/two", rank: 2 },
        ],
      }),
    ).tiers;
    expect(tiers).toHaveLength(1);
    expect(tiers[0].jobs[0].chain.map((s) => s.model)).toEqual(["a/one", "b/two"]);
  });

  it("🔴 reads a MISSING rank as 1, never as 0", () => {
    // ⚠️ A deployment mid-rollout can answer from older code with no rank at
    // all. Defaulting to 0 would put the unranked step AHEAD of a real first
    // choice — the one ordering mistake nobody sees until a failover.
    const chain = catalogFromWire(
      WIRE({
        bindings: [
          { tier: "fast", task: "chat", model: "a/one", rank: 1 },
          { tier: "fast", task: "chat", model: "b/two" },
        ],
      }),
    ).tiers[0].jobs[0].chain;
    expect(chain.every((s) => s.rank >= 1)).toBe(true);
  });

  it("keeps two tasks on one tier apart", () => {
    const jobs = catalogFromWire(
      WIRE({
        bindings: [
          { tier: "fast", task: "chat", model: "a/one", rank: 1 },
          { tier: "fast", task: "embed", model: "c/three", rank: 1 },
        ],
      }),
    ).tiers[0].jobs;
    expect(jobs).toHaveLength(2);
  });
});

describe("provider accounts", () => {
  const CRED = {
    id: "c1", provider: "anthropic", api_base: null, label: null,
    org_slug: null, scope: "platform", created_at: "2026-08-01T00:00:00Z",
    revoked_at: null,
  };

  it("🔴 reports health as UNKNOWN, never as ok", () => {
    // Nothing probes a vendor account. A green dot would be a claim nobody
    // measured, on the one screen where believing it means not checking.
    const [a] = accountsFromWire([CRED]);
    expect(a.health).toBe("unknown");
    expect(a.lastCheckedAt).toBeNull();
  });

  it("carries the revocation through, so a dead key cannot read as live", () => {
    const [a] = accountsFromWire([{ ...CRED, revoked_at: "2026-08-02T00:00:00Z" }]);
    expect(a.revokedAt).toBe("2026-08-02T00:00:00Z");
  });

  it("keeps platform and BYOK apart on the column the Router keys on", () => {
    const [platform, byok] = accountsFromWire([
      CRED, { ...CRED, id: "c2", org_slug: "acme" },
    ]);
    expect(platform.orgSlug).toBeNull();
    expect(byok.orgSlug).toBe("acme");
  });
});
