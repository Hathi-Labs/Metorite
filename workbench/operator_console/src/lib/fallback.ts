// Fallback chains — what a tier does when its first choice is down.
//
// 🔴 **This is designed here BEFORE the backend can hold it.** `tier_binding`
// stores one model per (tier, task) with no ordering column, so there is
// nowhere to put a second choice. The screen is built against `ChainStep[]`,
// and the migration that adds `rank` is owed. Nothing here is speculative
// about the RULES, only about the storage.
//
// ⚠️ **The chain must be judged, not just drawn.** Three failure shapes are
// invisible to the eye and each one is a chain that looks like insurance and
// is not:
//
//   1. Every step on the SAME provider. The provider is the thing that goes
//      down. Three OpenAI models is one point of failure drawn three times.
//   2. A step whose provider has no key installed. It cannot be tried.
//   3. A step that cannot do the job. It fails on the first request, and it
//      fails AFTER the primary already failed, so it is discovered during an
//      outage rather than before one.

import type { CatalogModel, ChainStep, ModelKind, Tier, TierJob } from "./contract";
import { providerOf } from "./readiness";
import type { Tone } from "./tone";

/** The kind a task needs a model to have.
 *
 * ⚠️ **A task not in this table is NOT judged.** Returning a default would
 * mark an operator's own new task as broken on every model, which reads as the
 * catalog being wrong when it is this table that is out of date. Silence is
 * the honest answer to a question we cannot ask. */
export const TASK_KIND: Record<string, ModelKind> = {
  chat: "chat",
  image: "image",
  transcribe: "transcribe",
  speak: "speak",
  embed: "embed",
};

export type ChainContext = {
  models: CatalogModel[];
  /** Providers with a LIVE PLATFORM account — the ones the Router can call for
   *  a tenant that has not brought its own key. */
  armed: string[];
};

/** Steps in the order the Router would try them.
 *
 * ⚠️ Sorted by rank, then by model id. A stable tiebreak keeps the drawn order
 * from changing between two renders of the same data, which otherwise reads as
 * the chain having been edited. */
export function orderedChain(job: TierJob): ChainStep[] {
  return [...job.chain].sort(
    (a, b) => a.rank - b.rank || a.model.localeCompare(b.model),
  );
}

export function primaryOf(job: TierJob): string | null {
  return orderedChain(job)[0]?.model ?? null;
}

/** Can this model do this job, as far as the catalog knows? */
export function canServe(model: CatalogModel | undefined, task: string): boolean {
  if (!model) return false;
  const need = TASK_KIND[task];
  if (!need) return model.declared;
  return model.declared && model.kinds.includes(need);
}

export type Problem = { tone: Tone; label: string; detail: string };

/** Everything wrong with one chain, worst first.
 *
 * ⚠️ **"Will fail" always outranks "fragile".** A chain that cannot serve now
 * and a chain with no backup are different urgencies, and mixing them makes an
 * operator triage by reading rather than by looking. */
export function chainProblems(job: TierJob, ctx: ChainContext): Problem[] {
  const steps = orderedChain(job);
  if (steps.length === 0) return [];

  const byId = new Map(ctx.models.map((m) => [m.id, m]));
  const armed = new Set(ctx.armed.map((p) => p.toLowerCase()));
  const bad: Problem[] = [];
  const fragile: Problem[] = [];

  for (const s of steps) {
    const m = byId.get(s.model);
    if (!m) {
      bad.push({
        tone: "danger",
        label: "unknown model",
        detail: `${s.model} is not in the catalog. The Router will refuse it.`,
      });
      continue;
    }
    if (!canServe(m, job.task)) {
      bad.push({
        tone: "danger",
        label: "cannot do this job",
        detail: `${m.label} cannot ${job.task}. It fails on the first request.`,
      });
    }
    if (!armed.has(providerOf(s.model).toLowerCase())) {
      bad.push({
        tone: "danger",
        label: "no key installed",
        detail:
          `We hold no live ${providerOf(s.model)} account, so this step ` +
          "cannot be tried at all.",
      });
    }
  }

  if (steps.length === 1) {
    fragile.push({
      tone: "warn",
      label: "no backup",
      detail:
        "One model only. When it is down or rate limited, this job stops for " +
        "every customer on this tier.",
    });
  }

  const providers = new Set(steps.map((s) => providerOf(s.model).toLowerCase()));
  if (steps.length > 1 && providers.size === 1) {
    fragile.push({
      tone: "warn",
      label: "same provider throughout",
      detail:
        `Every step is ${providerOf(steps[0].model)}. The provider is the ` +
        "thing that goes down, so this is one point of failure written " +
        "several times. Add a step from a different provider.",
    });
  }

  const seen = new Set<string>();
  for (const s of steps) {
    if (seen.has(s.model)) {
      fragile.push({
        tone: "warn",
        label: "the same model twice",
        detail: `${s.model} appears more than once. The second try adds nothing.`,
      });
      break;
    }
    seen.add(s.model);
  }

  return [...bad, ...fragile];
}

/** The single word for a chain's state, for a chip. */
export function chainTone(problems: Problem[]): Tone {
  if (problems.some((p) => p.tone === "danger")) return "danger";
  if (problems.length > 0) return "warn";
  return "ok";
}

export function chainLabel(job: TierJob, problems: Problem[]): string {
  if (job.chain.length === 0) return "not set";
  if (problems.some((p) => p.tone === "danger")) return "will fail";
  if (problems.length > 0) return "no backup";
  return `working, ${job.chain.length - 1} spare`;
}

// ── "What if this provider goes down?" ──────────────────────────────────────

export type OutageOutcome = {
  tier: string;
  task: string;
  /** What serves today. */
  before: string | null;
  /** What would serve during the outage. NULL means the job stops. */
  after: string | null;
  status: "unaffected" | "failover" | "down" | "already-broken";
};

/** Which step answers when a set of providers is unreachable.
 *
 * ⚠️ **A step is only counted if it could serve on a GOOD day.** Walking past
 * the outage to a step that has no key, or that cannot do the job, would report
 * a survival that will not happen — the most expensive kind of wrong answer
 * this screen can give, because it is read as a reason not to act. */
export function servedDuring(
  job: TierJob,
  down: string[],
  ctx: ChainContext,
): string | null {
  const byId = new Map(ctx.models.map((m) => [m.id, m]));
  const armed = new Set(ctx.armed.map((p) => p.toLowerCase()));
  const out = new Set(down.map((p) => p.toLowerCase()));
  for (const s of orderedChain(job)) {
    const p = providerOf(s.model).toLowerCase();
    if (out.has(p)) continue;
    if (!armed.has(p)) continue;
    if (!canServe(byId.get(s.model), job.task)) continue;
    return s.model;
  }
  return null;
}

export function outageReport(
  tiers: Tier[],
  down: string[],
  ctx: ChainContext,
): OutageOutcome[] {
  const out: OutageOutcome[] = [];
  for (const t of tiers) {
    for (const job of t.jobs) {
      if (job.chain.length === 0) continue;
      const before = servedDuring(job, [], ctx);
      const after = servedDuring(job, down, ctx);
      // ⚠️ A job that is ALREADY broken must not be reported as an outage
      // casualty. Blaming a provider for a chain that never worked sends
      // someone to the wrong place.
      const status: OutageOutcome["status"] =
        before === null
          ? "already-broken"
          : after === null
            ? "down"
            : after === before
              ? "unaffected"
              : "failover";
      out.push({ tier: t.slug, task: job.task, before, after, status });
    }
  }
  return out;
}

/** The sentence above the outage list. */
export function outageHeadline(
  down: string[],
  outcomes: OutageOutcome[],
): { text: string; tone: Tone } {
  if (down.length === 0) {
    return {
      text: "Pick a provider to see what would still work.",
      tone: "neutral",
    };
  }
  const stops = outcomes.filter((o) => o.status === "down").length;
  const moves = outcomes.filter((o) => o.status === "failover").length;
  const fine = outcomes.filter((o) => o.status === "unaffected").length;
  const who = down.join(" and ");
  if (stops === 0 && moves === 0) {
    return {
      text: `Nothing we sell uses ${who}. No customer would notice.`,
      tone: "ok",
    };
  }
  const parts: string[] = [];
  if (stops > 0) parts.push(`${stops} would STOP`);
  if (moves > 0) parts.push(`${moves} would move to a backup`);
  if (fine > 0) parts.push(`${fine} unaffected`);
  return {
    text: `If ${who} went down: ${parts.join(", ")}.`,
    tone: stops > 0 ? "danger" : "warn",
  };
}

// ── The one instruction at the top of the tiers page ────────────────────────

export type NextStep = { title: string; detail: string; tone: Tone };

/** The ONE thing to do next, in plain words.
 *
 * ⚠️ **One step at a time, and the order is the design.** A tier that will
 * fail now outranks a tier with no backup, which outranks a tier nobody has
 * set up. Showing all three at once makes a reader choose, and the wrong
 * choice spends an afternoon on the least urgent one.
 *
 * ⚠️ **No word here needs looking up.** `fallback.test.ts` fails if one does.
 */
export function tierNextStep(tiers: Tier[], ctx: ChainContext): NextStep {
  const jobs = tiers.flatMap((t) => t.jobs);
  const set = jobs.filter((j) => j.chain.length > 0);

  if (set.length === 0) {
    return {
      title: "Set up your first tier",
      detail:
        "A tier is a speed and quality setting a customer picks — Fast, " +
        "Balanced or Powerful. Until one points at a model, every AI request " +
        "fails.",
      tone: "danger",
    };
  }

  const judged = set.map((j) => ({ job: j, problems: chainProblems(j, ctx) }));
  const broken = judged.filter((x) => x.problems.some((p) => p.tone === "danger"));
  if (broken.length > 0) {
    const n = broken.length;
    return {
      title: `Fix ${n} ${n === 1 ? "job that will fail" : "jobs that will fail"}`,
      detail:
        `${n === 1 ? "One job points" : "Some jobs point"} at a model we ` +
        "cannot call — no key, or the model cannot do that work. The first " +
        "customer request fails. " +
        broken[0].problems[0].detail,
      tone: "danger",
    };
  }

  const bare = judged.filter((x) => x.problems.length > 0);
  if (bare.length > 0) {
    const n = bare.length;
    return {
      title: `Add a backup to ${n} ${n === 1 ? "job" : "jobs"}`,
      detail:
        "These work today and have nowhere to go when the provider is down " +
        "or busy. A backup from a DIFFERENT provider is the one that helps — " +
        "the provider is the thing that goes down, not the model.",
      tone: "warn",
    };
  }

  return {
    title: "Every tier has a backup",
    detail:
      `${set.length} ${set.length === 1 ? "job" : "jobs"} can serve customers, ` +
      "and each has somewhere to go if its first choice stops answering.",
    tone: "ok",
  };
}

/** Models that can do work no tier points at.
 *
 * 🔴 **Restored from a field the Console used to compute.** The old catalog
 * response carried an `unbound` list and this page lost it in the rebuild.
 * Deriving it here is better than reading it back: one source of truth, and it
 * stays correct when a tier changes without the Console being asked again.
 *
 * ⚠️ **Nothing here is BROKEN.** It is capacity we are not selling, which is a
 * calm fact and must be drawn as one. Rendering it at the same weight as a
 * chain that will fail is how a real alarm gets ignored. */
export function unusedModels(tiers: Tier[], models: CatalogModel[]): string[] {
  const used = new Set(
    tiers.flatMap((t) => t.jobs).flatMap((j) => j.chain).map((s) => s.model),
  );
  return models
    .filter((m) => m.declared && m.kinds.length > 0 && !used.has(m.id))
    .map((m) => m.id)
    .sort();
}

/** How many vendor chips the outage question can ask before it stops being a
 *  question.
 *
 * ⚠️ *Agent default.* Twelve fills about two lines at 1440px. Measured
 * 2026-09-19: the simulator drew 67 chips across ten rows, and a control that
 * needs ten rows of options is a list, not a question. */
export const OUTAGE_VENDOR_HEAD = 12;

/** The vendors an outage would actually hurt, worst first.
 *
 * 🔴 **Ranked by BLAST RADIUS, not alphabetically.** The question this control
 * asks is "what would we lose". Sorting by name puts a vendor that serves one
 * ghost tier above one that serves every chat band, so the first chips a
 * reader sees are the least interesting ones on the page.
 *
 * ⚠️ **A REGISTERED tier counts for more than a ghost, but a ghost still
 * counts.** An unregistered binding cannot be priced and still SERVES, so
 * dropping it from the count would understate a real outage. Registered jobs
 * are weighted above unregistered ones instead, which orders the list without
 * hiding anything.
 */
export function vendorsByBlastRadius(
  tiers: { registered?: boolean; jobs: { chain: { model: string }[] }[] }[],
): { provider: string; jobs: number; registeredJobs: number }[] {
  const seen = new Map<string, { jobs: number; registeredJobs: number }>();
  for (const t of tiers) {
    for (const j of t.jobs) {
      // ⚠️ One count per (vendor, job), not per chain STEP. A vendor holding
      // both the primary and the backup of one job can only take that job down
      // once, and counting twice would rank it above a vendor that really does
      // serve two.
      const vendors = new Set(
        j.chain.map((s) =>
          s.model.includes("/") ? s.model.split("/")[0] : s.model,
        ),
      );
      for (const v of vendors) {
        const cur = seen.get(v) ?? { jobs: 0, registeredJobs: 0 };
        cur.jobs += 1;
        if (t.registered) cur.registeredJobs += 1;
        seen.set(v, cur);
      }
    }
  }
  return [...seen.entries()]
    .map(([provider, c]) => ({ provider, ...c }))
    .sort(
      (a, b) =>
        b.registeredJobs - a.registeredJobs ||
        b.jobs - a.jobs ||
        a.provider.localeCompare(b.provider),
    );
}

/** The models offered as the next step in a chain, grouped so the page's own
 *  advice is followable.
 *
 * 🔴 **The board tells an operator to "add a step from a different provider",
 * then offered a flat list of model ids.** Following that instruction meant
 * reading a prefix off each of forty-three strings and remembering which
 * vendors the chain already used. The advice and the control disagreed.
 *
 * ⚠️ **A provider NOT already in the chain sorts first**, because those are the
 * options that fix the warning. Within a group the cheapest comes first: a
 * backup is chosen under time pressure and the price difference between two
 * models of the same vendor can be tenfold.
 *
 * ⚠️ **`alreadyInChain` is reported, never removed.** A second model from the
 * same vendor is a legitimate choice — it survives a model being retired or
 * rate limited, just not the vendor going down. Hiding it would make the board
 * refuse a decision that is the operator's to make.
 *
 * ⚠️ **An unpriced model is LABELLED, not hidden.** Its calls serve; they
 * cannot be costed. The picker says so rather than presenting it as equal to a
 * model whose margin is known.
 */
export type BackupOption = {
  model: string;
  /** USD per million input tokens, or null when nobody has recorded it. */
  inputPer1M: number | null;
  outputPer1M: number | null;
};

export type BackupGroup = {
  provider: string;
  alreadyInChain: boolean;
  options: BackupOption[];
};

export function backupOptions(
  optionIds: string[],
  models: { id: string; provider: string; inputPer1M: number | null; outputPer1M: number | null }[],
  chain: string[],
): BackupGroup[] {
  const byId = new Map(models.map((m) => [m.id, m]));
  const vendorOf = (id: string) =>
    byId.get(id)?.provider ?? (id.includes("/") ? id.split("/")[0] : id);
  const used = new Set(chain.map(vendorOf));

  const groups = new Map<string, BackupOption[]>();
  for (const id of optionIds) {
    const m = byId.get(id);
    const v = vendorOf(id);
    if (!groups.has(v)) groups.set(v, []);
    groups.get(v)?.push({
      model: id,
      inputPer1M: m?.inputPer1M ?? null,
      outputPer1M: m?.outputPer1M ?? null,
    });
  }

  return [...groups.entries()]
    .map(([provider, options]) => ({
      provider,
      alreadyInChain: used.has(provider),
      // ⚠️ An unpriced model sorts LAST inside its group, never first. "We do
      // not know" is not "free", and a cheapest-first list that recommends the
      // unknown is the same error `nullsLast` exists to stop elsewhere.
      options: [...options].sort((a, b) => {
        if (a.inputPer1M === null && b.inputPer1M === null) {
          return a.model.localeCompare(b.model);
        }
        if (a.inputPer1M === null) return 1;
        if (b.inputPer1M === null) return -1;
        return a.inputPer1M - b.inputPer1M || a.model.localeCompare(b.model);
      }),
    }))
    .sort(
      (a, b) =>
        // 1. A provider the chain does not use yet — the only kind that fixes
        //    the same-provider warning.
        Number(a.alreadyInChain) - Number(b.alreadyInChain) ||
        // 2. 🔴 A provider with a PRICED option before one with none.
        //    Measured 2026-09-19 against the live catalog: an alphabetical
        //    tiebreak put sixty unpriced vendors above deepseek and groq, so
        //    the first sixty choices the picker offered would each have landed
        //    a costs-blind step. A choice we cannot cost is not the equal of
        //    one we can.
        Number(hasPrice(b)) - Number(hasPrice(a)) ||
        // 3. Then the cheaper vendor, judged on its cheapest usable option.
        cheapestOption(a) - cheapestOption(b) ||
        a.provider.localeCompare(b.provider),
    );
}

/** Does this vendor offer anything we can cost? */
function hasPrice(g: { options: BackupOption[] }): boolean {
  return g.options.some((o) => o.inputPer1M !== null);
}

/** The cheapest priced option, or Infinity when the vendor has none — so an
 *  unpriced vendor can never sort above a priced one on price alone. */
function cheapestOption(g: { options: BackupOption[] }): number {
  const priced = g.options
    .map((o) => o.inputPer1M)
    .filter((p): p is number => p !== null);
  return priced.length === 0 ? Number.POSITIVE_INFINITY : Math.min(...priced);
}

/** One option's label: the id, then what it costs us. */
export function backupOptionLabel(o: BackupOption): string {
  if (o.inputPer1M === null && o.outputPer1M === null) {
    return `${o.model} — no price recorded`;
  }
  const inp = o.inputPer1M === null ? "—" : `$${o.inputPer1M}`;
  const out = o.outputPer1M === null ? "—" : `$${o.outputPer1M}`;
  return `${o.model} — ${inp} in / ${out} out per 1M`;
}
