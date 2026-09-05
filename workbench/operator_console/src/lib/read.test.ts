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

import { readConsoleEnv, type FetchLike } from "./console";
import { accountsFromWire, catalogFromWire, readAiCatalog } from "./read";

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

describe("the per-unit vendor costs (019, H-78)", () => {
  const PROFILE = (over: Record<string, unknown> = {}) => ({
    model: "anthropic/sonnet", label: null, context_window: null,
    max_output: null, vendor_input_per_1m_usd: null,
    vendor_output_per_1m_usd: null, description: "",
    reads_images: false, thinks_first: false, ...over,
  });

  const FEED_ROW = (over: Record<string, unknown> = {}) => ({
    model: "anthropic/sonnet", provider: "anthropic", mode: "chat",
    task: "chat", invocation: "acompletion", context_window: null,
    max_output: null, vendor_input_per_1m_usd: null,
    vendor_output_per_1m_usd: null, vendor_cached_input_per_1m_usd: null,
    reads_images: false, thinks_first: false, deprecated_on: null, ...over,
  });

  it("takes the three per-unit costs from the profile", () => {
    const m = catalogFromWire(
      WIRE({
        profiles: [PROFILE({
          vendor_per_minute_usd: "0.0060000000",
          vendor_per_character_usd: "0.0000150000",
          vendor_per_image_usd: "0.0400000000",
        })],
      }),
    ).models[0];
    expect(m.perMinuteUsd).toBe(0.006);
    expect(m.perCharacterUsd).toBe(0.000015);
    expect(m.perImageUsd).toBe(0.04);
  });

  it("🔴 THE BOARD READS THE PROFILE, NEVER THE FEED", () => {
    // A feed row with a cost and no profile row yields NO cost. Billing
    // reads `model_profile`, and only a staff Save changes that table — so
    // a vendor's published price can never move what we charge on its own.
    const cat = catalogFromWire(
      WIRE({
        feed: {
          synced_at: "2026-08-31T06:00:00Z", source: "github", models: 1,
          rows: [FEED_ROW({
            vendor_per_minute_usd: "0.0060000000",
            vendor_per_image_usd: "0.0400000000",
          })],
          available: [],
        },
      }),
    );
    expect(cat.models[0].perMinuteUsd).toBeNull();
    expect(cat.models[0].perImageUsd).toBeNull();
    // The feed still SHOWS the number, which is what drift is built on.
    expect(cat.feed.rows[0].perMinuteUsd).toBe("0.0060000000");
  });

  it("a Console mid-rollout that sends no per-unit field reads as unknown", () => {
    // Absent must never become zero: a zero per-image price reads as free.
    const m = catalogFromWire(
      WIRE({ profiles: [PROFILE()] }),
    ).models[0];
    expect(m.perMinuteUsd).toBeNull();
    expect(m.perCharacterUsd).toBeNull();
    expect(m.perImageUsd).toBeNull();
  });

  it("an unparseable per-unit price is UNKNOWN, never zero", () => {
    const m = catalogFromWire(
      WIRE({ profiles: [PROFILE({ vendor_per_image_usd: "not-a-number" })] }),
    ).models[0];
    expect(m.perImageUsd).toBeNull();
  });
});

describe("the tier registry and the tier rates (015, D67)", () => {
  it("the registry leads and an EMPTY registered tier renders", () => {
    const cat = catalogFromWire(WIRE({
      tier_registry: [
        { slug: "tier-video", label: "Video", blurb: "b", sort_order: 100 },
        { slug: "tier-fast", label: "Fast", blurb: "", sort_order: 10 },
      ],
    }));
    const slugs = cat.tiers.map((t) => t.slug);
    // Sorted by sort_order, present even with no binding at all.
    expect(slugs.indexOf("tier-fast")).toBeLessThan(slugs.indexOf("tier-video"));
    const video = cat.tiers.find((t) => t.slug === "tier-video");
    expect(video?.jobs).toEqual([]);
    expect(video?.registered).toBe(true);
    expect(video?.label).toBe("Video");
    expect(video?.task ?? null).toBeNull();
  });

  it("🔴 a binding whose tier is NOT registered still renders, as a ghost", () => {
    // Hiding a thing that serves would be the board lying.
    const cat = catalogFromWire(WIRE({
      tier_registry: [
        { slug: "tier-fast", label: "Fast", blurb: "", sort_order: 10 },
      ],
      bindings: [{ tier: "ghost", task: "chat", model: "anthropic/sonnet" }],
    }));
    const ghost = cat.tiers.find((t) => t.slug === "ghost");
    expect(ghost?.registered).toBe(false);
    expect(ghost?.jobs).toHaveLength(1);
    expect(ghost?.task).toBeNull();
    // A picker cannot offer what the registry does not hold.
    expect(ghost?.customerVisible).toBe(false);
  });

  it("carries customer_visible through, and reads absent as TRUE (021)", () => {
    // The column defaults to TRUE. A Console that predates 021 sends no
    // field at all, and reading that as "hidden" would report every tier as
    // hidden for the length of a rollout.
    const cat = catalogFromWire(WIRE({
      tier_registry: [
        { slug: "tier-stt", label: "Speech to text", blurb: "b",
          sort_order: 70, customer_visible: false },
        { slug: "tier-fast", label: "Fast", blurb: "", sort_order: 10,
          customer_visible: true },
        { slug: "tier-old", label: "Old", blurb: "", sort_order: 20 },
      ],
    }));
    const by = (slug: string) => cat.tiers.find((t) => t.slug === slug);
    expect(by("tier-stt")?.customerVisible).toBe(false);
    expect(by("tier-fast")?.customerVisible).toBe(true);
    expect(by("tier-old")?.customerVisible).toBe(true);
  });

  it("maps the tier rates with money as STRINGS", () => {
    const cat = catalogFromWire(WIRE({
      tier_rates: [{
        tier: "tier-fast", task: "chat", unit: "tokens",
        pricing_mode: "priced", input_per_1m: "8",
        output_per_1m: "40", cached_input_per_1m: "0.8",
        credits_per_unit: "0",
      }],
    }));
    // ⚠️ This REPLACES the per-thousand fallback case. That test fed a wire
    // carrying only `_per_1k` and asserted the mapping derived the per-million
    // figure from it. Migration 030 dropped the columns, the projection and
    // the wire fields together, so the input it fed can no longer arrive — a
    // test that kept asserting it would be exercising a shape nothing sends.
    //
    // The property that still matters is that money crosses as a STRING and
    // is not re-derived on the way through.
    expect(cat.tierRates).toEqual([{
      tier: "tier-fast", task: "chat", unit: "tokens", mode: "priced",
      inputPer1m: "8", outputPer1m: "40", cachedInputPer1m: "0.8",
      creditsPerUnit: "0",
    }]);
  });

  it("a Console without the feature yields empty, not a crash", () => {
    const cat = catalogFromWire(WIRE({}));
    expect(cat.tierRates).toEqual([]);
  });

  it("carries the credit price through with money as STRINGS (017)", () => {
    const cat = catalogFromWire(WIRE({
      credit_price: {
        inr_per_credit: "1.500000", usd_to_inr: "88.000000",
        effective_from: "2026-08-30T00:00:00Z",
      },
    }));
    expect(cat.creditPrice).toEqual({
      inrPerCredit: "1.500000", usdToInr: "88.000000",
      effectiveFrom: "2026-08-30T00:00:00Z",
    });
  });

  it("no saved credit price reads as null, never as zero", () => {
    expect(catalogFromWire(WIRE({})).creditPrice).toBeNull();
    expect(catalogFromWire(WIRE({ credit_price: null })).creditPrice)
      .toBeNull();
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

describe("a failed credential read under a working catalog", () => {
  const ENV = {
    CUSTOMER_CONSOLE_URL: "https://console.internal",
    CUSTOMER_CONSOLE_OPERATOR_TOKEN: "op-secret-token",
  };
  const split = (credStatus: number): FetchLike => async (url) =>
    String(url).includes("/providers/credentials")
      ? { status: credStatus, text: async () => "boom" }
      : { status: 200, text: async () => JSON.stringify(WIRE()) };

  it("🔴 reads as UNKNOWN with a warning — never as 'no credential installed'", async () => {
    // An empty list by absence of evidence is not the fact "nothing is
    // installed" — the go-live rail once asserted "every AI call fails"
    // from exactly this state.
    const r = await readAiCatalog({ env: readConsoleEnv(ENV), fetchImpl: split(500) });
    expect(r.origin).toBe("live");
    expect(r.data.accountsKnown).toBe(false);
    expect(r.data.accounts).toEqual([]);
    expect(r.note).toContain("UNVERIFIED");
  });

  it("a working credential read stays known and silent", async () => {
    const ok: FetchLike = async (url) =>
      String(url).includes("/providers/credentials")
        ? { status: 200, text: async () => JSON.stringify({ credentials: [] }) }
        : { status: 200, text: async () => JSON.stringify(WIRE()) };
    const r = await readAiCatalog({ env: readConsoleEnv(ENV), fetchImpl: ok });
    expect(r.data.accountsKnown).toBe(true);
    expect(r.note).toBeUndefined();
  });
});
