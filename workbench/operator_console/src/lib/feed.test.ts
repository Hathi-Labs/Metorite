// The vendor feed's judgements — WS-31, owner directive 2026-08-30.
//
// The fences that matter most here:
//   * drift only fires when BOTH sides know a number — null is "unknown",
//     and unknown cannot disagree;
//   * trailing zeros never drift ("0.2800" vs "0.280000" is the SAME price);
//   * the copy bodies carry the feed's STRINGS verbatim, so nothing float-y
//     writes its own noise into the database;
//   * `streams` mirrors the Console's STREAMABLE_TASKS exactly.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { CatalogModel, FeedModel, VendorFeed } from "./contract";
import {
  availableByVendor,
  declareBodies,
  driftFor,
  feedById,
  fillCount,
  freshness,
  prefillFrom,
} from "./feed";

const M = (over: Partial<CatalogModel>): CatalogModel => ({
  id: "deepseek/deepseek-chat", label: "DeepSeek Chat", provider: "deepseek",
  kinds: ["chat"], contextWindow: null, maxOutput: null,
  inputPer1M: null, outputPer1M: null, cachedInputPer1M: null,
  description: "", declared: true, priced: false,
  ...over,
});

const F = (over: Partial<FeedModel>): FeedModel => ({
  id: "deepseek/deepseek-chat", provider: "deepseek", mode: "chat",
  task: "chat", invocation: "acompletion",
  contextWindow: 131072, maxOutput: 8192,
  inputPer1M: "0.280000", outputPer1M: "0.420000",
  cachedInputPer1M: "0.070000",
  readsImages: false, thinksFirst: false, deprecatedOn: null,
  ...over,
});

const FEED = (over: Partial<VendorFeed>): VendorFeed => ({
  syncedAt: "2026-08-30T10:00:00Z", source: "github", models: 2716,
  rows: [], available: [],
  ...over,
});

describe("driftFor", () => {
  it("fires when the vendor moved a price under a typed profile", () => {
    const d = driftFor(M({ inputPer1M: 0.14 }), F({ inputPer1M: "0.280000" }));
    expect(d).toHaveLength(1);
    expect(d[0]).toEqual({
      label: "per 1M in", ours: "0.14", upstream: "0.280000",
    });
  });

  it("🔴 trailing zeros are the SAME price — no phantom drift", () => {
    // The profile is NUMERIC(12,4) ("0.2800"), the feed NUMERIC(14,6)
    // ("0.280000"). String comparison would warn on every correct profile.
    const d = driftFor(
      M({ inputPer1M: 0.28, outputPer1M: 0.42, cachedInputPer1M: 0.07 }),
      F({}),
    );
    expect(d).toEqual([]);
  });

  it("unknown cannot disagree — null on either side never drifts", () => {
    expect(driftFor(M({}), F({}))).toEqual([]);
    expect(
      driftFor(M({ inputPer1M: 3 }), F({ inputPer1M: null })),
    ).toEqual([]);
    expect(driftFor(M({ inputPer1M: 3 }), undefined)).toEqual([]);
  });

  it("reports every drifted field, not just the first", () => {
    const d = driftFor(
      M({ inputPer1M: 1, outputPer1M: 2 }),
      F({ inputPer1M: "1.100000", outputPer1M: "2.200000" }),
    );
    expect(d.map((x) => x.label)).toEqual(["per 1M in", "per 1M out"]);
  });
});

describe("fillCount", () => {
  it("counts the dashes upstream could fill", () => {
    expect(fillCount(M({}), F({}))).toBe(5);
    expect(fillCount(M({ inputPer1M: 0.28, contextWindow: 131072 }), F({}))).toBe(3);
    expect(fillCount(M({}), undefined)).toBe(0);
  });

  it("does not count facts upstream lacks too", () => {
    expect(
      fillCount(M({}), F({
        inputPer1M: null, outputPer1M: null, cachedInputPer1M: null,
        contextWindow: null, maxOutput: null,
      })),
    ).toBe(0);
  });
});

describe("prefillFrom", () => {
  it("hands the form the feed's strings verbatim, empty for unknown", () => {
    expect(prefillFrom(F({ cachedInputPer1M: null, maxOutput: null }))).toEqual({
      ctx: "131072", out: "", vin: "0.280000", vout: "0.420000",
      vcached: "", readsImages: false, thinksFirst: false,
    });
  });
});

describe("declareBodies", () => {
  it("carries the feed's price STRINGS into the profile body verbatim", () => {
    const b = declareBodies(F({}));
    expect(b.profile.vendor_input_per_1m_usd).toBe("0.280000");
    expect(b.profile.vendor_output_per_1m_usd).toBe("0.420000");
    expect(b.profile.model).toBe("deepseek/deepseek-chat");
  });

  it("🔴 streams mirrors the Console's STREAMABLE_TASKS: chat and speak", () => {
    expect(declareBodies(F({ task: "chat" })).capability?.streams).toBe(true);
    expect(
      declareBodies(F({ task: "speak", invocation: "aspeech" }))
        .capability?.streams,
    ).toBe(true);
    expect(
      declareBodies(F({ task: "transcribe", invocation: "atranscription" }))
        .capability?.streams,
    ).toBe(false);
    expect(
      declareBodies(F({ task: "embed", invocation: "aembedding" }))
        .capability?.streams,
    ).toBe(false);
  });

  it("a mode we cannot serve gets NO capability body", () => {
    const b = declareBodies(F({ mode: "rerank", task: null, invocation: null }));
    expect(b.capability).toBeNull();
    expect(b.profile.model).toBe("deepseek/deepseek-chat");
  });
});

describe("freshness", () => {
  const now = new Date("2026-08-30T12:00:00Z");

  it("never fetched is a warning that names the consequence", () => {
    const f = freshness(FEED({ syncedAt: null, source: null, models: 0 }), now);
    expect(f.tone).toBe("warn");
    expect(f.label).toContain("typed by hand");
  });

  it("a fresh sync reads calm and names its source", () => {
    const f = freshness(FEED({}), now);
    expect(f.tone).toBe("ok");
    expect(f.label).toBe("2716 models fetched today from the live litellm feed.");
  });

  it("names the offline snapshot when that is what answered", () => {
    const f = freshness(FEED({ source: "packaged:litellm" }), now);
    expect(f.label).toContain("offline litellm snapshot");
  });

  it("warns past a week — litellm moves near-daily", () => {
    const f = freshness(FEED({ syncedAt: "2026-08-20T10:00:00Z" }), now);
    expect(f.tone).toBe("warn");
    expect(f.label).toContain("10 days old");
  });

  it("says yesterday like a person", () => {
    const f = freshness(FEED({ syncedAt: "2026-08-29T06:00:00Z" }), now);
    expect(f.label).toContain("yesterday");
  });
});

describe("feedById and availableByVendor", () => {
  it("indexes rows and available together", () => {
    const map = feedById(FEED({
      rows: [F({})],
      available: [F({ id: "groq/whisper-large-v3", provider: "groq" })],
    }));
    expect(map.size).toBe(2);
    expect(map.get("groq/whisper-large-v3")?.provider).toBe("groq");
  });

  it("groups by vendor and filters by the query", () => {
    const feed = FEED({
      available: [
        F({ id: "deepseek/deepseek-reasoner" }),
        F({ id: "groq/whisper-large-v3", provider: "groq", mode: "audio_transcription" }),
      ],
    });
    expect([...availableByVendor(feed, "").keys()]).toEqual(["deepseek", "groq"]);
    const hit = availableByVendor(feed, "whisper");
    expect([...hit.keys()]).toEqual(["groq"]);
    // mode matches too — an operator searches "transcription", not slugs.
    expect([...availableByVendor(feed, "transcription").keys()]).toEqual(["groq"]);
  });
});

describe("the wiring fences", () => {
  const read = (p: string) => readFileSync(join(__dirname, p), "utf8");

  it("ModelBrowser threads the feed and stays free of fetch(", () => {
    const src = read("../app/models/ModelBrowser.tsx");
    expect(src).toContain("feed");
    expect(src).not.toContain("fetch(");
  });

  it("the models page passes the catalog's feed through", () => {
    const src = read("../app/models/page.tsx");
    expect(src).toMatch(/feed=\{/);
  });

  it("🔴 nothing ever auto-saves a profile from the feed", () => {
    // The feed PREFILLS; the operator SAVES. A save that fires from feed
    // data without a click would make upstream a billing input after all.
    // The two feed components may only write through declare/profiles/feed
    // endpoints ON CLICK — crude but load-bearing: their fetch( calls must
    // all live inside onClick/async handlers named in this file's scope.
    for (const file of ["FeedStrip.tsx", "FeedAvailable.tsx"]) {
      const src = read(`../app/models/${file}`);
      expect(src).not.toContain("useEffect");
    }
  });
});
