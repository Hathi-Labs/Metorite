// Fallback chains — the tests are the three chains that LOOK like insurance.
//
// ⚠️ **The subject is a false sense of safety.** A chain that is drawn, saved
// and wrong is worse than no chain, because somebody stopped worrying about it.
// Each test below is one way that happens.

import { describe, expect, it } from "vitest";

import type { CatalogModel, Tier, TierJob } from "./contract";
import {
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
  description: "",
  declared,
  priced: true,
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
      jobs: [job("chat", "anthropic/haiku", "openai/gpt-4o")],
    },
    {
      slug: "powerful",
      label: "Powerful",
      blurb: "",
      jobs: [job("chat", "anthropic/sonnet")],
    },
    {
      slug: "media",
      label: "Media",
      blurb: "",
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
      { slug: "x", label: "X", blurb: "", jobs: [job("chat", "deepseek/r1")] },
    ];
    const r = outageReport(withTier(broken), ["anthropic"], CTX);
    expect(r[0].status).toBe("already-broken");
  });

  it("skips a job with nothing set rather than reporting it down", () => {
    const empty: Tier[] = [
      { slug: "x", label: "X", blurb: "", jobs: [job("chat")] },
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
    slug, label: slug, blurb: "",
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
      { slug: "a", label: "A", blurb: "", jobs: [job("chat", "anthropic/haiku")] },
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
        slug: "a", label: "A", blurb: "",
        jobs: [job("chat", "anthropic/haiku", "openai/gpt-4o")],
      },
    ];
    expect(unusedModels(tiers, MODELS)).not.toContain("openai/gpt-4o");
  });
});
