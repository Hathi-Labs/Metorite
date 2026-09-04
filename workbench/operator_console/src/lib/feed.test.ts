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
  perMinuteUsd: null, perCharacterUsd: null, perImageUsd: null,
  // 023 — the window and the context tier. Null everywhere: these
  // fixtures predate the columns and no case here depends on them.
  inputOffpeakPer1M: null, outputOffpeakPer1M: null,
  cachedInputOffpeakPer1M: null,
  offpeakStartUtc: null, offpeakEndUtc: null,
  contextTierThreshold: null, inputLongPer1M: null,
  outputLongPer1M: null, cachedInputLongPer1M: null,
  description: "", declared: true,
  ...over,
});

const F = (over: Partial<FeedModel>): FeedModel => ({
  id: "deepseek/deepseek-chat", provider: "deepseek", mode: "chat",
  task: "chat", invocation: "acompletion",
  contextWindow: 131072, maxOutput: 8192,
  inputPer1M: "0.280000", outputPer1M: "0.420000",
  cachedInputPer1M: "0.070000",
  perMinuteUsd: null, perCharacterUsd: null, perImageUsd: null,
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

  // ── The per-unit costs (H-78, §6A.11a clause 7) ─────────────────────────

  it("🔴 an EQUAL per-minute pair reports no drift", () => {
    // The fence for the whole seam. The feed table stores 0.0001 per SECOND
    // and the Console's feed read serves 0.006 per MINUTE, so both sides of
    // this compare speak the PROFILE's unit and a direct compare is right.
    // Convert on either side and this pair drifts by a factor of 60.
    const d = driftFor(
      M({ perMinuteUsd: 0.006 }),
      F({ task: "transcribe", perMinuteUsd: "0.0060000000" }),
    );
    expect(d).toEqual([]);
  });

  it("names the UNIT in every per-unit drift label", () => {
    // A row reading "$0.006 against $0.0001" with no unit invites the
    // seconds-against-minutes mistake this feature exists to prevent.
    const d = driftFor(
      M({ perMinuteUsd: 0.006, perCharacterUsd: 0.000015, perImageUsd: 0.04 }),
      F({ perMinuteUsd: "0.0120000000", perCharacterUsd: "0.0000300000",
          perImageUsd: "0.0800000000" }),
    );
    expect(d.map((x) => x.label)).toEqual([
      "per minute", "per character", "per image",
    ]);
    expect(d[0].upstream).toBe("0.0120000000");
  });

  it("a per-unit price unknown on either side never drifts", () => {
    expect(driftFor(M({ perImageUsd: 0.04 }), F({}))).toEqual([]);
    expect(
      driftFor(M({}), F({ perImageUsd: "0.0400000000" })),
    ).toEqual([]);
  });

  it("🔴 a per-unit price that DOUBLED at 3e-10 reports drift", () => {
    // The absolute 1e-9 epsilon is dollar-scale, and these columns are
    // NUMERIC(18,10) exactly so a tiny price fits. Under the old rule the
    // vendor could double this price and the gap (3e-10) stayed under the
    // epsilon, so the board said "no drift" about a 2x move. The per-unit
    // pairs compare relatively, so magnitude stops hiding a change.
    const d = driftFor(
      M({ perCharacterUsd: 3e-10 }),
      F({ perCharacterUsd: "0.0000000006" }),
    );
    expect(d).toHaveLength(1);
    expect(d[0].label).toBe("per character");
  });

  it("the relative rule still calls an equal tiny pair equal", () => {
    expect(
      driftFor(M({ perCharacterUsd: 6e-10 }),
               F({ perCharacterUsd: "0.0000000006" })),
    ).toEqual([]);
    // Trailing zeros are still the same price, at any magnitude.
    expect(
      driftFor(M({ perImageUsd: 0.04 }), F({ perImageUsd: "0.0400000000" })),
    ).toEqual([]);
  });

  it("🔴 the drift sentence says PLAIN DIGITS, never an exponent", () => {
    // `ours` came off `String(n)` and rendered "$3e-10" in the operator's
    // drift line. The number the message is about became unreadable exactly
    // where it mattered most.
    const d = driftFor(
      M({ perCharacterUsd: 3e-10 }),
      F({ perCharacterUsd: "0.0000000006" }),
    );
    expect(d[0].ours).toBe("0.0000000003");
    expect(d[0].ours).not.toContain("e");
  });

  it("two zeros are equal, and never divide by zero", () => {
    expect(
      driftFor(M({ perImageUsd: 0 }), F({ perImageUsd: "0.0000000000" })),
    ).toEqual([]);
  });

  it("the TOKEN pairs keep their absolute rule, unchanged", () => {
    // Scope: H-78 changed the per-unit comparison only. A dollar-scale
    // price has no magnitude problem, and re-tuning it here would be an
    // unrequested change to what the drift chip says about chat models.
    expect(
      driftFor(M({ inputPer1M: 0.28 }), F({ inputPer1M: "0.280000" })),
    ).toEqual([]);
    expect(
      driftFor(M({ inputPer1M: 0.14 }), F({ inputPer1M: "0.280000" })),
    ).toHaveLength(1);
  });
});

describe("🔴 no client code converts a unit (H-78)", () => {
  // ⚠️ **A reviewer smuggled three conversions past the first version of
  // this scan**, which looked for `* 60` alone: `Number("60") * x`, `x*60`
  // and `x / 60` all read green. Each pattern below exists because one of
  // those got through.
  const SIXTY = [
    /[*/]\s*60\b/, // x * 60, x*60, x / 60, x/60
    /\b60\s*\*/, // 60 * x
    /\(\s*["']?60["']?\s*\)/, // Number(60), Number("60")
  ];

  /** Code lines that spell a literal sixty, comments stripped.
   *
   * ⚠️ **BLOCK comments go first, and a JSDoc line is why.** Every leading
   *  `*` in a doc block is an operator to the `60 *` pattern, so a line
   *  ending "…by 60" ahead of another `*` line false-positived. Stripping
   *  `//` alone was not enough. */
  const sixtySites = (src: string): string[] =>
    src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("\n")
      .map((ln) => ln.split("//")[0])
      .filter((code) => SIXTY.some((re) => re.test(code)))
      .map((code) => code.trim());

  it("the scan catches every spelling it claims to", () => {
    // The fence's own fence. A scan that silently stops matching is worse
    // than no scan, because it reports green while the rule rots.
    for (const smuggled of [
      "const x = Number('60') * p;",
      'const x = Number("60")*p;',
      "const x = p * 60;",
      "const x = p*60;",
      "const x = p / 60;",
      "const x = p/60;",
      "const x = 60 * p;",
    ]) {
      expect(sixtySites(smuggled).length).toBeGreaterThan(0);
    }
    for (const innocent of [
      "const ctx = 600;",
      "const ms = 3600;",
      "// multiply the per-second price by 60 here",
      // 🔴 The JSDoc false positive: the block's own `*` is an operator to
      // the `60 *` pattern, so this read as a conversion until the scan
      // stripped block comments first.
      "/** the feed read already multiplied by 60\n * so nothing here does\n */",
    ]) {
      expect(sixtySites(innocent)).toEqual([]);
    }
  });

  it("feed.ts and the two components convert nothing", () => {
    // The ×60 that turns litellm's per-SECOND transcription price into the
    // per-MINUTE one lives in the Console's feed-read projection, server
    // side, in Decimal. One here would be a FLOAT multiply, and a float
    // rewrites the number it was asked to copy.
    //
    // ⚠️ This bounds SPELLINGS, not semantics. A conversion hidden behind a
    // name (`const SIXTY = 60` then `p * SIXTY`) still passes, because the
    // operand carries no literal. Nothing source-scanned can close that, and
    // claiming otherwise would make the fence lie about its own reach.
    // ⚠️ `read.ts` and `priceboard.ts` are on this list because BOTH handle
    // a per-minute value — `read.ts` maps it off the wire and
    // `priceboard.ts` picks it per task. The first version of this scan
    // covered neither, so a conversion in the two files most likely to want
    // one would have gone unseen.
    for (const p of [
      "feed.ts",
      "read.ts",
      "priceboard.ts",
      "../app/models/ModelDetails.tsx",
      "../app/pricing/PriceFromCost.tsx",
    ]) {
      expect(sixtySites(readFileSync(join(__dirname, p), "utf8"))).toEqual([]);
    }
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

  it("counts the per-unit costs too (H-78)", () => {
    // Before 019 a transcribe model's hint always read "0 boxes" while
    // upstream held the one number the pricing board needs.
    const blank = F({
      inputPer1M: null, outputPer1M: null, cachedInputPer1M: null,
      contextWindow: null, maxOutput: null,
    });
    expect(fillCount(M({}), F({ ...blank, perMinuteUsd: "0.0060000000" })))
      .toBe(1);
    expect(fillCount(M({}), F({
      ...blank, perMinuteUsd: "0.0060000000",
      perCharacterUsd: "0.0000150000", perImageUsd: "0.0400000000",
    }))).toBe(3);
    // A cost the profile already carries is not a dash to fill.
    expect(fillCount(
      M({ perImageUsd: 0.04 }),
      F({ ...blank, perImageUsd: "0.0400000000" }),
    )).toBe(0);
  });
});

describe("prefillFrom", () => {
  it("hands the form the feed's strings verbatim, empty for unknown", () => {
    expect(prefillFrom(F({ cachedInputPer1M: null, maxOutput: null }))).toEqual({
      ctx: "131072", out: "", vin: "0.280000", vout: "0.420000",
      vcached: "", vmin: "", vchar: "", vimg: "",
      readsImages: false, thinksFirst: false,
    });
  });

  it("🔴 copies the per-unit costs verbatim, converting none of them", () => {
    // The string the Console served, character for character. It is already
    // per MINUTE — the ×60 happened server-side, in Decimal.
    const v = prefillFrom(F({
      task: "transcribe", perMinuteUsd: "0.0060000000",
      perCharacterUsd: "0.0000150000", perImageUsd: "0.0400000000",
    }));
    expect(v.vmin).toBe("0.0060000000");
    expect(v.vchar).toBe("0.0000150000");
    expect(v.vimg).toBe("0.0400000000");
  });
});

describe("declareBodies", () => {
  it("carries the feed's price STRINGS into the profile body verbatim", () => {
    const b = declareBodies(F({}));
    expect(b.profile.vendor_input_per_1m_usd).toBe("0.280000");
    expect(b.profile.vendor_output_per_1m_usd).toBe("0.420000");
    expect(b.profile.model).toBe("deepseek/deepseek-chat");
  });

  it("🔴 carries the per-unit costs under the WIRE's own names (H-78)", () => {
    // The wire name and the profile column agree, so this is a copy and not
    // a rename. `vendor_per_second_usd` never appears: the Console converted
    // once, in the feed read, and the browser converts nothing.
    const b = declareBodies(F({
      task: "transcribe", invocation: "atranscription",
      perMinuteUsd: "0.0060000000",
      perCharacterUsd: "0.0000150000", perImageUsd: "0.0400000000",
    }));
    expect(b.profile.vendor_per_minute_usd).toBe("0.0060000000");
    expect(b.profile.vendor_per_character_usd).toBe("0.0000150000");
    expect(b.profile.vendor_per_image_usd).toBe("0.0400000000");
    expect(b.profile).not.toHaveProperty("vendor_per_second_usd");
  });

  it("an unpriced per-unit field travels as null, never as zero", () => {
    const b = declareBodies(F({}));
    expect(b.profile.vendor_per_minute_usd).toBeNull();
    expect(b.profile.vendor_per_image_usd).toBeNull();
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

describe("ModelDetails posts the wire's own strings", () => {
  it("🔴 typed vendor prices travel as trimmed STRINGS, garbage is refused", () => {
    // The feed's one-click path posts the feed's strings verbatim; the
    // hand-edit path once posted Number() floats and silently nulled
    // "3,50" under a green "Saved." One wire, one rule.
    const src = readFileSync(
      join(__dirname, "..", "app", "models", "ModelDetails.tsx"), "utf8");
    expect(src).toContain("vendor_input_per_1m_usd: blankToNull(vin)");
    expect(src).toContain("vendor_cached_input_per_1m_usd: blankToNull(vcached)");
    expect(src).not.toMatch(/function numeric\(/);
    expect(src).toContain("badNumber");
  });

  it("🔴 the three per-unit boxes post and validate like the rest (H-78)", () => {
    // The JSX half of clause 7. This app carries no React renderer, so the
    // source text is the only fence a form gets — and a box that renders but
    // never reaches the body is exactly the failure that reads as a green
    // "Saved." while the price stays a dash.
    const src = readFileSync(
      join(__dirname, "..", "app", "models", "ModelDetails.tsx"), "utf8");
    for (const f of ["vendor_per_minute_usd: blankToNull(vmin)",
                     "vendor_per_character_usd: blankToNull(vchar)",
                     "vendor_per_image_usd: blankToNull(vimg)"]) {
      expect(src).toContain(f);
    }
    // The garbage guard covers them too — `badNumber` reads this list.
    expect(src).toContain('["$ per minute", vmin]');
    expect(src).toContain('["$ per character", vchar]');
    expect(src).toContain('["$ per image", vimg]');
    // And "Copy the vendor's facts" fills them.
    expect(src).toContain("setVmin(v.vmin)");
  });
});

describe("the pricing board's per-unit half (H-78)", () => {
  it("🔴 PriceFromCost reads the RECORDED cost through priceboard.ts", () => {
    // The board half of clause 7. The task-to-column judgement is a pure
    // function (`recordedVendorUsd`) because this app has no React renderer
    // and logic inside a component is untested by construction. The fence is
    // that the component calls it rather than re-deciding inline.
    const src = readFileSync(
      join(__dirname, "..", "app", "pricing", "PriceFromCost.tsx"), "utf8");
    expect(src).toContain("recordedVendorUsd(j.task, m)");
    expect(src).toContain("vendorUsdBox(vendorUsd[key], recorded)");
    // No second task-to-column table inside the JSX.
    expect(src).not.toContain("perMinuteUsd");
    expect(src).not.toContain("perImageUsd");
    // 🔴 The stale claim is gone. The header once told the reader the feed
    // "does not carry those columns yet", which stopped being true the day
    // 019 landed — and a comment that lies is how the next agent rebuilds a
    // seam that already exists. The retraction line may quote the old words;
    // the live claim may not repeat them.
    expect(src).toContain("This read");
    expect(src.split("This read")[0]).not.toContain("does not carry");
  });
});
