// Fallback chains — the tests are the three chains that LOOK like insurance.
//
// ⚠️ **The subject is a false sense of safety.** A chain that is drawn, saved
// and wrong is worse than no chain, because somebody stopped worrying about it.
// Each test below is one way that happens.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { CatalogModel, Tier, TierJob } from "./contract";
import {
  backupOptionLabel,
  backupOptions,
  vendorsByBlastRadius,
  type ChainContext,
  canServe,
  chainLabel,
  chainProblems,
  chainTone,
  orderedChain,
  outageHeadline,
  outageReport,
  primaryOf,
  servedDuring,
  tierNextStep,
  unusedModels,
} from "./fallback";

const model = (
  id: string,
  kinds: CatalogModel["kinds"],
  declared = true,
): CatalogModel => ({
  id,
  label: id,
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
  declared,
});

const MODELS = [
  model("anthropic/haiku", ["chat"]),
  model("anthropic/sonnet", ["chat"]),
  model("openai/gpt-4o", ["chat"]),
  model("openai/whisper", ["transcribe"]),
  model("groq/llama", ["chat"]),
  model("deepseek/r1", ["chat"], false),
];

const CTX: ChainContext = {
  models: MODELS,
  armed: ["anthropic", "openai", "groq"],
};

const job = (task: string, ...models: string[]): TierJob => ({
  tier: "t",
  task,
  chain: models.map((m, i) => ({ model: m, rank: i + 1 })),
});

describe("the order the Router would try", () => {
  it("sorts by rank, not by the order the rows arrived", () => {
    const j: TierJob = {
      tier: "t",
      task: "chat",
      chain: [
        { model: "openai/gpt-4o", rank: 3 },
        { model: "anthropic/haiku", rank: 1 },
        { model: "groq/llama", rank: 2 },
      ],
    };
    expect(orderedChain(j).map((s) => s.model)).toEqual([
      "anthropic/haiku",
      "groq/llama",
      "openai/gpt-4o",
    ]);
    expect(primaryOf(j)).toBe("anthropic/haiku");
  });

  it("breaks a rank tie the same way every render", () => {
    // ⚠️ An unstable tiebreak makes the drawn order change between two renders
    // of identical data, which reads as somebody having edited the chain.
    const j: TierJob = {
      tier: "t",
      task: "chat",
      chain: [
        { model: "openai/gpt-4o", rank: 1 },
        { model: "anthropic/haiku", rank: 1 },
      ],
    };
    expect(orderedChain(j)[0].model).toBe("anthropic/haiku");
  });

  it("has no primary when nothing is set", () => {
    expect(primaryOf(job("chat"))).toBeNull();
  });
});

describe("what a chain is judged on", () => {
  it("says nothing about a healthy two-provider chain", () => {
    expect(chainProblems(job("chat", "anthropic/haiku", "openai/gpt-4o"), CTX))
      .toEqual([]);
  });

  it("🔴 calls out a chain that is ALL ONE PROVIDER", () => {
    // The whole point. Two Anthropic models is one point of failure written
    // twice — the provider is the thing that goes down, not the model.
    const p = chainProblems(job("chat", "anthropic/haiku", "anthropic/sonnet"), CTX);
    expect(p.map((x) => x.label)).toContain("same provider throughout");
    expect(chainTone(p)).toBe("warn");
  });

  it("does not call a ONE-STEP chain same-provider as well", () => {
    // Both are true of a single step, and saying both is noise. "No backup"
    // is the useful half.
    const p = chainProblems(job("chat", "anthropic/haiku"), CTX);
    expect(p.map((x) => x.label)).toEqual(["no backup"]);
  });

  it("🔴 is DANGER when a step has no key installed", () => {
    // A step we cannot call is not a fallback. It is a gap that is only
    // discovered after the primary has already failed.
    const p = chainProblems(job("chat", "anthropic/haiku", "deepseek/r1"), CTX);
    expect(p[0].tone).toBe("danger");
    expect(p.map((x) => x.label)).toContain("no key installed");
    expect(chainTone(p)).toBe("danger");
  });

  it("🔴 is DANGER when a step cannot do the job it is bound to", () => {
    const p = chainProblems(job("transcribe", "openai/whisper", "groq/llama"), CTX);
    expect(p.map((x) => x.label)).toContain("cannot do this job");
  });

  it("names a model that is not in the catalog at all", () => {
    const p = chainProblems(job("chat", "acme/ghost"), CTX);
    expect(p[0].label).toBe("unknown model");
    expect(p[0].detail).toContain("acme/ghost");
  });

  it("spots the same model listed twice", () => {
    const p = chainProblems(job("chat", "anthropic/haiku", "anthropic/haiku"), CTX);
    expect(p.map((x) => x.label)).toContain("the same model twice");
  });

  it("⚠️ orders DANGER before FRAGILE, always", () => {
    // An operator triages by looking. Mixing the two urgencies makes them read
    // instead.
    const p = chainProblems(job("chat", "deepseek/r1", "deepseek/r1"), CTX);
    expect(p[0].tone).toBe("danger");
    expect(p[p.length - 1].tone).toBe("warn");
  });

  it("does not judge a task it has never heard of", () => {
    // ⚠️ Defaulting to broken would mark an operator's own new task as failing
    // on every model — the catalog looks wrong when the table is what is stale.
    expect(canServe(MODELS[0], "summarise-invoices")).toBe(true);
  });
});

describe("the words on the chip", () => {
  it("counts the spares rather than saying 'ok'", () => {
    const j = job("chat", "anthropic/haiku", "openai/gpt-4o", "groq/llama");
    expect(chainLabel(j, chainProblems(j, CTX))).toBe("working, 2 spare");
  });

  it("says 'not set' for an empty chain, never 'will fail'", () => {
    // Nothing set is a job nobody has done. It is not a broken job.
    expect(chainLabel(job("chat"), [])).toBe("not set");
  });

  it("🔴 the verdict never says 'every tier' about the ones it filtered out", () => {
    // Measured 2026-09-22: 7 of 11 registered tiers had no chain. Fixing the
    // last broken one would have turned the rail green under the title
    // "Every tier has a backup" — a claim about the tiers this function had
    // already excluded.
    const tier = (slug: string, jobs: Tier["jobs"]): Tier => ({
      slug,
      label: slug,
      blurb: "",
      registered: true,
      customerVisible: true,
      jobs,
    });
    const bound = tier("tier-fast", [
      job("chat", "anthropic/haiku", "openai/gpt-4o"),
    ]);
    const empty = tier("tier-image", []);

    const both = tierNextStep([bound, empty], CTX);
    expect(both.title).not.toContain("Every tier");
    expect(both.title).toContain("1 tier still empty");
    expect(both.detail).toContain("tier-image");
    // ⚠️ Still `ok`. An unsold job is not a fault, it just may not hide.
    expect(both.tone).toBe("ok");

    // With nothing left empty the original sentence returns.
    expect(tierNextStep([bound], CTX).title).toBe("Every tier has a backup");
  });

  it("🔴 but 'not set' is NOT painted green", () => {
    // Measured 2026-09-22: `chainProblems` returned [] for an unbound job,
    // so `chainTone([])` answered `ok`. Seven of eleven registered tiers had
    // no chain, and the board drew every one of them in a healthy chip — a
    // tier that serves nothing and sells nothing, reading as fine.
    //
    // The LABEL is unchanged, and the clause above still guards it. What
    // changes is the colour and the fact that the job now explains itself.
    const j = job("chat");
    const problems = chainProblems(j, CTX);
    expect(problems.map((p) => p.label)).toEqual(["nothing bound"]);
    expect(chainTone(problems)).toBe("warn");
    expect(chainLabel(j, problems)).toBe("not set");
  });

  it("uses no word that needs looking up", () => {
    const jobs = [
      job("chat"),
      job("chat", "anthropic/haiku"),
      job("chat", "deepseek/r1"),
      job("chat", "anthropic/haiku", "openai/gpt-4o"),
    ];
    const jargon = ["binding", "capability", "tier_binding", "rate card", "chain"];
    for (const j of jobs) {
      const label = chainLabel(j, chainProblems(j, CTX));
      for (const w of jargon) expect(label.toLowerCase()).not.toContain(w);
    }
  });
});

describe("what if a provider goes down", () => {
  const TIERS: Tier[] = [
    {
      slug: "fast",
      label: "Fast",
      blurb: "",
      registered: true, customerVisible: true,
      jobs: [job("chat", "anthropic/haiku", "openai/gpt-4o")],
    },
    {
      slug: "powerful",
      label: "Powerful",
      blurb: "",
      registered: true, customerVisible: true,
      jobs: [job("chat", "anthropic/sonnet")],
    },
    {
      slug: "media",
      label: "Media",
      blurb: "",
      registered: true, customerVisible: true,
      jobs: [job("transcribe", "openai/whisper")],
    },
  ];
  const withTier = (t: Tier[]) => t.map((x) => ({ ...x, jobs: x.jobs.map((j) => ({ ...j, tier: x.slug })) }));

  it("walks past the outage to the first step that can really serve", () => {
    expect(servedDuring(job("chat", "anthropic/haiku", "openai/gpt-4o"), ["anthropic"], CTX))
      .toBe("openai/gpt-4o");
  });

  it("🔴 does NOT walk past an outage onto a step with no key", () => {
    // Reporting a survival that will not happen is the most expensive wrong
    // answer here — it is read as a reason not to act.
    expect(servedDuring(job("chat", "anthropic/haiku", "deepseek/r1"), ["anthropic"], CTX))
      .toBeNull();
  });

  it("🔴 does NOT walk past an outage onto a step that cannot do the job", () => {
    expect(servedDuring(job("transcribe", "openai/whisper", "groq/llama"), ["openai"], CTX))
      .toBeNull();
  });

  it("reports unaffected, failover and down as three different things", () => {
    const r = outageReport(withTier(TIERS), ["anthropic"], CTX);
    const by = Object.fromEntries(r.map((o) => [o.tier, o]));
    expect(by.fast.status).toBe("failover");
    expect(by.fast.after).toBe("openai/gpt-4o");
    expect(by.powerful.status).toBe("down");
    expect(by.powerful.after).toBeNull();
    expect(by.media.status).toBe("unaffected");
  });

  it("🔴 does not blame a provider for a chain that ALREADY does not work", () => {
    // ⚠️ Counting a broken job as an outage casualty sends somebody to the
    // wrong place, and hides that it was broken before anything went down.
    const broken: Tier[] = [
      { slug: "x", label: "X", blurb: "", registered: true, customerVisible: true, jobs: [job("chat", "deepseek/r1")] },
    ];
    const r = outageReport(withTier(broken), ["anthropic"], CTX);
    expect(r[0].status).toBe("already-broken");
  });

  it("skips a job with nothing set rather than reporting it down", () => {
    const empty: Tier[] = [
      { slug: "x", label: "X", blurb: "", registered: true, customerVisible: true, jobs: [job("chat")] },
    ];
    expect(outageReport(withTier(empty), ["anthropic"], CTX)).toEqual([]);
  });

  it("headlines the count that hurts, and names the provider", () => {
    const r = outageReport(withTier(TIERS), ["anthropic"], CTX);
    const h = outageHeadline(["anthropic"], r);
    expect(h.tone).toBe("danger");
    expect(h.text).toContain("anthropic");
    expect(h.text).toContain("1 would STOP");
  });

  it("says plainly when a provider we hold is used by nothing", () => {
    const r = outageReport(withTier(TIERS), ["groq"], CTX);
    expect(outageHeadline(["groq"], r).tone).toBe("ok");
    expect(outageHeadline(["groq"], r).text).toContain("No customer would notice");
  });

  it("asks for a provider before it claims anything", () => {
    expect(outageHeadline([], []).tone).toBe("neutral");
  });
});

describe("the one instruction", () => {
  const tier = (slug: string, ...jobs: TierJob[]): Tier => ({
    slug, label: slug, blurb: "", registered: true, customerVisible: true,
    jobs: jobs.map((j) => ({ ...j, tier: slug })),
  });

  it("asks for a first tier when nothing is set at all", () => {
    expect(tierNextStep([], CTX).tone).toBe("danger");
    expect(tierNextStep([tier("t", job("chat"))], CTX).title)
      .toContain("first tier");
  });

  it("🔴 puts WILL FAIL ahead of NO BACKUP, always", () => {
    // Nothing can be made resilient before it works. Sending somebody to add
    // a backup to a healthy tier while another one 500s is the wrong afternoon.
    const tiers = [
      tier("a", job("chat", "anthropic/haiku")),
      tier("b", job("chat", "deepseek/r1")),
    ];
    const step = tierNextStep(tiers, CTX);
    expect(step.tone).toBe("danger");
    expect(step.title).toContain("will fail");
  });

  it("names WHY it will fail, not just how many", () => {
    const step = tierNextStep([tier("b", job("chat", "deepseek/r1"))], CTX);
    expect(step.detail).toContain("deepseek");
  });

  it("asks for a backup from a DIFFERENT provider, in those words", () => {
    const tiers = [tier("a", job("chat", "anthropic/haiku", "anthropic/sonnet"))];
    const step = tierNextStep(tiers, CTX);
    expect(step.tone).toBe("warn");
    expect(step.detail).toContain("DIFFERENT provider");
  });

  it("says so plainly when everything is healthy", () => {
    const tiers = [tier("a", job("chat", "anthropic/haiku", "openai/gpt-4o"))];
    expect(tierNextStep(tiers, CTX).tone).toBe("ok");
  });

  it("🔴 uses no word an operator would have to look up", () => {
    // The 15-year-old bar. A guard on the vocabulary, not on the wording.
    const jargon = [
      "binding", "capability", "tier_binding", "rate card", "unpriced",
      "servable", "resolve", "chain", "invocation",
    ];
    const cases: Tier[][] = [
      [],
      [tier("a", job("chat"))],
      [tier("a", job("chat", "deepseek/r1"))],
      [tier("a", job("chat", "anthropic/haiku"))],
      [tier("a", job("chat", "anthropic/haiku", "openai/gpt-4o"))],
    ];
    for (const c of cases) {
      const s = tierNextStep(c, CTX);
      const text = `${s.title} ${s.detail}`.toLowerCase();
      for (const w of jargon) expect(text).not.toContain(w);
    }
  });
});

describe("capacity we are not selling", () => {
  it("names a declared model no tier points at", () => {
    const tiers: Tier[] = [
      { slug: "a", label: "A", blurb: "", registered: true, customerVisible: true, jobs: [job("chat", "anthropic/haiku")] },
    ];
    const out = unusedModels(tiers, MODELS);
    expect(out).toContain("openai/gpt-4o");
    expect(out).not.toContain("anthropic/haiku");
  });

  it("🔴 does not list a model that was never declared", () => {
    // ⚠️ Undeclared is a different fact and a worse one — it cannot be used at
    // all. Mixing it into "spare capacity" makes a gap read as an opportunity.
    expect(unusedModels([], MODELS)).not.toContain("deepseek/r1");
  });

  it("counts every step of a chain as used, not only the first", () => {
    const tiers: Tier[] = [
      {
        slug: "a", label: "A", blurb: "", registered: true, customerVisible: true,
        jobs: [job("chat", "anthropic/haiku", "openai/gpt-4o")],
      },
    ];
    expect(unusedModels(tiers, MODELS)).not.toContain("openai/gpt-4o");
  });
});

describe("TierBoard structure", () => {
  const src = readFileSync(
    join(__dirname, "..", "app", "tiers", "TierBoard.tsx"), "utf8");

  it("🔴 no component is declared inside TierBoard", () => {
    // A component declared during a render is a NEW type on every render:
    // React remounts the subtree and focus dies on each keystroke.
    // ProviderAdmin's header states the rule; Job broke it once.
    expect(src).not.toMatch(/\n  function [A-Z]/);
  });

  it("a saved chain stays on the board", () => {
    // Deleting the draft on success rolled the board back to the stale
    // server props — the saved backup vanished and read as a failed save.
    expect(src).toContain("if (res.ok) router.refresh();");
    expect(src).not.toMatch(/delete d\[`\$\{tier\}::\$\{task\}`\]/);
  });
});

// ── Which vendor going down would hurt most? ───────────────────────────────
//
// 🔴 Measured 2026-09-19: the outage question drew 67 chips across ten rows,
// alphabetically. The first chip a reader saw served one ghost tier; the one
// serving every chat band was nine rows down.

describe("vendorsByBlastRadius", () => {
  const T = (registered: boolean, ...chains: string[][]) => ({
    registered,
    jobs: chains.map((models) => ({
      chain: models.map((model, i) => ({ model, rank: i + 1 })),
    })),
  });

  it("ranks the vendor serving more REGISTERED jobs first", () => {
    const got = vendorsByBlastRadius([
      T(true, ["deepseek/a"], ["deepseek/b"]),
      T(true, ["groq/a"]),
    ]);
    expect(got[0].provider).toBe("deepseek");
    expect(got[0].registeredJobs).toBe(2);
  });

  it("🔴 a registered job outranks any number of ghosts", () => {
    // A ghost serves, so it counts — but the product is what an operator is
    // deciding about, and it must not be pushed below the noise.
    const got = vendorsByBlastRadius([
      T(false, ["ghostvendor/a"], ["ghostvendor/b"], ["ghostvendor/c"]),
      T(true, ["realvendor/a"]),
    ]);
    expect(got[0].provider).toBe("realvendor");
  });

  it("still COUNTS an unregistered binding, because it serves", () => {
    const got = vendorsByBlastRadius([T(false, ["ghostvendor/a"])]);
    expect(got[0].jobs).toBe(1);
    expect(got[0].registeredJobs).toBe(0);
  });

  it("🔴 counts one job ONCE even when a vendor holds primary AND backup", () => {
    // That vendor can only take the job down once. Counting the chain steps
    // would rank it above a vendor that really does serve two jobs.
    const got = vendorsByBlastRadius([T(true, ["deepseek/a", "deepseek/b"])]);
    expect(got[0].jobs).toBe(1);
  });

  it("counts a job once per vendor when the chain spans two", () => {
    const got = vendorsByBlastRadius([T(true, ["deepseek/a", "groq/b"])]);
    expect(got.map((g) => g.jobs)).toEqual([1, 1]);
  });

  it("reads a model id with no slash as its own vendor", () => {
    const got = vendorsByBlastRadius([T(true, ["bare-model"])]);
    expect(got[0].provider).toBe("bare-model");
  });

  it("breaks a tie by name, so the row does not reshuffle", () => {
    const got = vendorsByBlastRadius([T(true, ["b/x"]), T(true, ["a/x"])]);
    expect(got.map((g) => g.provider)).toEqual(["a", "b"]);
  });

  it("is empty when nothing is bound", () => {
    expect(vendorsByBlastRadius([])).toEqual([]);
  });
});

// ── The backup picker, which used to contradict the page above it ──────────
//
// 🔴 The board warns "add a step from a different provider" and then offered a
// flat list of 43 model ids. Following the advice meant reading a prefix off
// each string and remembering which vendors the chain already used.

describe("backupOptions", () => {
  const M = (id: string, provider: string, inp: number | null, out: number | null) =>
    ({ id, provider, inputPer1M: inp, outputPer1M: out });

  const MODELS = [
    M("deepseek/cheap", "deepseek", 0.28, 0.42),
    M("deepseek/dear", "deepseek", 1.32, 3.96),
    M("groq/a", "groq", 0.05, 0.08),
    M("anthropic/x", "anthropic", null, null),
  ];

  it("🔴 a provider NOT in the chain comes FIRST", () => {
    // Those are the only choices that fix the same-provider warning.
    const got = backupOptions(
      ["deepseek/dear", "groq/a"],
      MODELS,
      ["deepseek/cheap"],
    );
    expect(got[0].provider).toBe("groq");
    expect(got[0].alreadyInChain).toBe(false);
    expect(got[1].provider).toBe("deepseek");
    expect(got[1].alreadyInChain).toBe(true);
  });

  it("REPORTS an in-chain provider, never removes it", () => {
    // A second model from the same vendor survives a retirement or a rate
    // limit — just not the vendor going down. That is the operator's call.
    const got = backupOptions(["deepseek/dear"], MODELS, ["deepseek/cheap"]);
    expect(got).toHaveLength(1);
    expect(got[0].alreadyInChain).toBe(true);
  });

  it("puts the cheapest first inside a group", () => {
    const got = backupOptions(
      ["deepseek/dear", "deepseek/cheap"],
      MODELS,
      [],
    );
    expect(got[0].options.map((o) => o.model)).toEqual([
      "deepseek/cheap",
      "deepseek/dear",
    ]);
  });

  it("🔴 an UNPRICED model sorts LAST, never first", () => {
    // "We do not know" is not "free". A cheapest-first list that recommends
    // the unknown is the error `nullsLast` exists to stop elsewhere.
    const got = backupOptions(["anthropic/x"], [...MODELS], []);
    expect(got[0].options[0].inputPer1M).toBeNull();
    const mixed = backupOptions(
      ["anthropic/x", "anthropic/y"],
      [...MODELS, M("anthropic/y", "anthropic", 5, 10)],
      [],
    );
    expect(mixed[0].options.map((o) => o.model)).toEqual([
      "anthropic/y",
      "anthropic/x",
    ]);
  });

  it("falls back to the id prefix for a model the catalog does not carry", () => {
    const got = backupOptions(["mystery/model"], MODELS, []);
    expect(got[0].provider).toBe("mystery");
  });

  it("is empty when nothing is on offer", () => {
    expect(backupOptions([], MODELS, [])).toEqual([]);
  });
});

describe("backupOptionLabel", () => {
  it("names the price so a backup is not chosen blind", () => {
    expect(
      backupOptionLabel({ model: "deepseek/a", inputPer1M: 0.28, outputPer1M: 0.42 }),
    ).toBe("deepseek/a — $0.28 in / $0.42 out per 1M");
  });

  it("says so when nobody has recorded a price", () => {
    expect(
      backupOptionLabel({ model: "x/y", inputPer1M: null, outputPer1M: null }),
    ).toBe("x/y — no price recorded");
  });

  it("draws a dash for the half it does not know", () => {
    expect(
      backupOptionLabel({ model: "x/y", inputPer1M: 3, outputPer1M: null }),
    ).toContain("$3 in / — out");
  });
});

describe("backupOptions ordering puts USABLE choices first", () => {
  const M = (id: string, provider: string, inp: number | null) =>
    ({ id, provider, inputPer1M: inp, outputPer1M: inp });

  it("🔴 a PRICED vendor outranks an unpriced one, whatever the alphabet says", () => {
    // Measured 2026-09-19: an alphabetical tiebreak put sixty unpriced
    // fixture vendors above deepseek and groq, so the first sixty choices
    // offered would each have landed a costs-blind step.
    const models = [
      M("aaa/ghost", "aaa", null),
      M("zzz/real", "zzz", 0.28),
    ];
    const got = backupOptions(["aaa/ghost", "zzz/real"], models, []);
    expect(got[0].provider).toBe("zzz");
  });

  it("then prefers the cheaper vendor, on its cheapest option", () => {
    const models = [
      M("dear/a", "dear", 5),
      M("cheap/a", "cheap", 0.1),
    ];
    const got = backupOptions(["dear/a", "cheap/a"], models, []);
    expect(got.map((g) => g.provider)).toEqual(["cheap", "dear"]);
  });

  it("🔴 but a DIFFERENT provider still beats a priced one already in use", () => {
    // The warning this picker serves is about the vendor, not the price. A
    // cheap second model from the vendor that is already the single point of
    // failure must not be the first thing offered.
    const models = [
      M("inchain/cheap", "inchain", 0.01),
      M("fresh/dear", "fresh", 9),
    ];
    const got = backupOptions(
      ["inchain/cheap", "fresh/dear"],
      models,
      ["inchain/other"],
    );
    expect(got[0].provider).toBe("fresh");
  });

  it("orders two unpriced vendors by name, so the list does not reshuffle", () => {
    const models = [M("b/x", "b", null), M("a/x", "a", null)];
    const got = backupOptions(["b/x", "a/x"], models, []);
    expect(got.map((g) => g.provider)).toEqual(["a", "b"]);
  });
});
