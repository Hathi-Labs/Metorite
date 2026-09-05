// Finding a model in a catalog of hundreds.
//
// ⚠️ **The subject is the number ON the chip.** A filter that returns the wrong
// rows is noticed in a second. A facet count that promises fourteen results and
// delivers none is believed, and it is the one people plan around.

import { describe, expect, it } from "vitest";

import { MODEL_KINDS, type CatalogModel } from "./contract";
import {
  NO_FILTERS,
  filterModels,
  formatTokens,
  formatVendorPrice,
  kindFacets,
  matchesKinds,
  matchesQuery,
  providerFacets,
  resultLine,
  sortModels,
  statusOf,
  toggle,
} from "./modelSearch";

const m = (
  id: string,
  kinds: CatalogModel["kinds"],
  over: Partial<CatalogModel> = {},
): CatalogModel => ({
  id,
  label: id.split("/")[1] ?? id,
  provider: id.split("/")[0],
  kinds,
  contextWindow: null,
  maxOutput: null,
  inputPer1M: null,
  outputPer1M: null,
  cachedInputPer1M: null,
  // 023 — the window and the context tier. Null everywhere: these
  // fixtures predate the columns and no case here depends on them.
  inputOffpeakPer1M: null,
  outputOffpeakPer1M: null,
  cachedInputOffpeakPer1M: null,
  offpeakStartUtc: null,
  offpeakEndUtc: null,
  contextTierThreshold: null,
  inputLongPer1M: null,
  outputLongPer1M: null,
  cachedInputLongPer1M: null,
  perMinuteUsd: null,
  perCharacterUsd: null,
  perImageUsd: null,
  description: "",
  declared: true,
  ...over,
});

const CATALOG = [
  m("anthropic/sonnet", ["chat", "vision", "reasoning"], {
    contextWindow: 200000,
    inputPer1M: 3,
    description: "the everyday workhorse",
  }),
  m("anthropic/haiku", ["chat", "vision"], {
    contextWindow: 200000,
    inputPer1M: 0.8,
    description: "cheap and quick",
  }),
  m("openai/o3-mini", ["chat", "reasoning"], {
    contextWindow: 200000,
    inputPer1M: 1.1,
  }),
  m("openai/whisper", ["transcribe"], {}),
  m("groq/llama", ["chat"], { declared: false, inputPer1M: 0.59 }),
];

describe("free text", () => {
  it("matches the id, the label and the provider", () => {
    expect(matchesQuery(CATALOG[0], "sonnet")).toBe(true);
    expect(matchesQuery(CATALOG[0], "anthropic")).toBe(true);
  });

  it("🔴 searches the DESCRIPTION too", () => {
    // An operator looking for a cheap model types "cheap", not a model id.
    // Restricting the match to the id makes the box useless for the query it
    // is actually used for.
    expect(matchesQuery(CATALOG[1], "cheap")).toBe(true);
  });

  it("⚠️ requires EVERY word, not any of them", () => {
    // "anthropic cheap" should find one model. An OR returns every Anthropic
    // model plus everything described as cheap, which is most of the list.
    expect(matchesQuery(CATALOG[1], "anthropic cheap")).toBe(true);
    expect(matchesQuery(CATALOG[0], "anthropic cheap")).toBe(false);
  });

  it("matches everything when the box is empty or blank", () => {
    expect(matchesQuery(CATALOG[0], "   ")).toBe(true);
  });
});

describe("kind filters", () => {
  it("🔴 requires ALL selected kinds, not any", () => {
    // The real question is "a chat model that can also read an image" — one
    // model doing both. An OR answers a question nobody asked, and D-AI-2
    // turns on exactly this query.
    expect(matchesKinds(CATALOG[2], ["chat", "vision"])).toBe(false);
    expect(matchesKinds(CATALOG[0], ["chat", "vision"])).toBe(true);
  });

  it("matches everything when nothing is selected", () => {
    expect(matchesKinds(CATALOG[3], [])).toBe(true);
  });
});

describe("the facet counts", () => {
  it("🔴 counts each facet against the OTHER filters, never the whole catalog", () => {
    // With anthropic selected, a "speech to text" chip reading 1 promises a
    // result and delivers none. Each facet drops its own dimension and keeps
    // every other one — the only rule where the number on the chip is the
    // number of rows you get by clicking it.
    const f = { ...NO_FILTERS, providers: ["anthropic"] };
    const facets = Object.fromEntries(
      kindFacets(CATALOG, f, MODEL_KINDS).map((x) => [x.value, x.count]),
    );
    expect(facets.transcribe).toBe(0);
    expect(facets.chat).toBe(2);
    expect(facets.reasoning).toBe(1);
  });

  it("a kind facet ADDS to the selection rather than replacing it", () => {
    const f = { ...NO_FILTERS, kinds: ["chat" as const] };
    const facets = Object.fromEntries(
      kindFacets(CATALOG, f, MODEL_KINDS).map((x) => [x.value, x.count]),
    );
    // chat + vision, not vision alone.
    expect(facets.vision).toBe(2);
  });

  it("provider facets are counted with the query still applied", () => {
    const f = { ...NO_FILTERS, query: "cheap" };
    const facets = Object.fromEntries(
      providerFacets(CATALOG, f).map((x) => [x.value, x.count]),
    );
    expect(facets.anthropic).toBe(1);
    expect(facets.openai).toBe(0);
  });
});

describe("status", () => {
  // Every provider in the fixture holds a key, except where a test says so.
  const ARMED = ["anthropic", "openai", "groq"];

  it("🔴 says NOT CONNECTED before it says COSTS BLIND", () => {
    // Same order as readiness.ts. Nothing can be costed before it can be
    // served — and since D67 the SELLING state lives on the tier board, so
    // this page only judges supply: declared, keyed, and do we know what
    // we pay.
    expect(statusOf(CATALOG[4], ARMED)).toBe("undeclared");
    expect(statusOf(CATALOG[3], ARMED)).toBe("costblind");
    expect(statusOf(CATALOG[0], ARMED)).toBe("costed");
  });

  it("🔴 a declared model of a KEYLESS vendor says NO KEY, in red", () => {
    // The seed proved the state real: it ships tier-stt on a groq model,
    // so groq/whisper is DECLARED on every fresh install — and with no
    // groq key it read as merely "costs blind", underselling "every call
    // fails" (owner report, 2026-08-30).
    expect(statusOf(CATALOG[3], ["anthropic"])).toBe("nokey");
    // Undeclared still outranks it: nothing can be called before it exists.
    expect(statusOf(CATALOG[4], [])).toBe("undeclared");
  });

  it("the no-key state is filterable and counted like any other", () => {
    const hit = filterModels(
      CATALOG,
      { ...NO_FILTERS, statuses: ["nokey"] },
      ["anthropic"],
    );
    expect(hit.length).toBeGreaterThan(0);
    expect(hit.every((m) => m.provider !== "anthropic" && m.declared)).toBe(true);
  });
});

describe("sorting", () => {
  it("🔴 puts an unknown number LAST, cheapest-first included", () => {
    // "We do not know the price" is not "the price is zero". Sorting nulls to
    // the top of a cheapest-first list recommends them.
    const sorted = sortModels(CATALOG, "cheapest");
    expect(sorted[0].inputPer1M).toBe(0.59);
    // ⚠️ Assert the PROPERTY, not a row id. Naming a row pins whichever
    // tiebreak the sort happens to produce, and the test then fails on an
    // unrelated catalog edit rather than on a broken sort.
    expect(sorted[sorted.length - 1].inputPer1M).toBeNull();
  });

  it("puts the biggest window first, unknowns last", () => {
    const sorted = sortModels(CATALOG, "context");
    expect(sorted[0].contextWindow).toBe(200000);
    expect(sorted[sorted.length - 1].contextWindow).toBeNull();
  });

  it("does not mutate the array it was given", () => {
    const before = CATALOG.map((x) => x.id);
    sortModels(CATALOG, "name");
    expect(CATALOG.map((x) => x.id)).toEqual(before);
  });
});

describe("the line above the list", () => {
  it("🔴 says WHY nothing matched, and what to remove", () => {
    // "0 results" makes somebody re-type the query. Naming the filters makes
    // them drop one.
    const f = { ...NO_FILTERS, query: "whisper", kinds: ["chat" as const] };
    const line = resultLine(filterModels(CATALOG, f).length, CATALOG.length, f);
    expect(line).toContain("whisper");
    expect(line).toContain("Remove a filter");
  });

  it("distinguishes an empty catalog from an empty result", () => {
    expect(resultLine(0, 0, NO_FILTERS)).toContain("No models in the catalog");
  });

  it("does not say '5 of 5'", () => {
    expect(resultLine(5, 5, NO_FILTERS)).toBe("5 models.");
  });
});

describe("toggle", () => {
  it("adds what is missing and removes what is there", () => {
    expect(toggle(["a"], "b")).toEqual(["a", "b"]);
    expect(toggle(["a", "b"], "a")).toEqual(["b"]);
  });
});

describe("display", () => {
  it("shortens a token count without inventing precision", () => {
    expect(formatTokens(200000)).toBe("200K");
    expect(formatTokens(1048576)).toBe("1.0M");
    expect(formatTokens(8191)).toBe("8.2K");
    expect(formatTokens(512)).toBe("512");
  });

  it("🔴 renders an unknown window as a dash, never as zero", () => {
    // "0 tokens" reads as a broken model. The truth is a missing column.
    expect(formatTokens(null)).toBe("—");
    expect(formatTokens(Number.NaN)).toBe("—");
  });

  it("names both halves of a vendor price and marks a missing half", () => {
    expect(formatVendorPrice(3, 15)).toBe("$3 in / $15 out");
    expect(formatVendorPrice(0.13, null)).toBe("$0.13 in / ? out");
    expect(formatVendorPrice(null, null)).toBe("—");
  });
});
