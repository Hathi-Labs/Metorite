// The ONE place live Console responses become the contract — and the ONE place
// sample data gets its origin stamped.
//
// 🔴 **Every screen reads from here and nothing else.** That is what makes
// building the UI ahead of the backend safe. A screen never sees a Console JSON
// shape, so an endpoint that arrives late, or arrives different, is a change to
// one function in this file. And because `resolve` stamps the origin here, a
// screen physically cannot hold sample rows without also holding the value that
// draws the banner over them.
//
// ⚠️ **The live mapping is DELIBERATELY thin, and says so by returning null.**
// `model_capability` carries `task`, `invocation` and `streams` — no context
// window, no vendor price, no vision flag. Inventing those from a lookup table
// of model names would be the same lie as sample data, with none of the
// labelling. A null renders as "—" and the reader learns the truth: we do not
// store it yet.

import type {
  AiCatalog,
  CatalogModel,
  ModelKind,
  ModelRate,
  ProviderAccount,
  Task,
  Tier,
  TierJob,
} from "./contract";
import { EMPTY_CATALOG } from "./contract";
import {
  ConsoleUnconfigured,
  listProviderCreds,
  readModelCatalog,
  type Deps,
} from "./console";
import { OWED, SAMPLE_CATALOG } from "./sample";
import { type Sourced, resolve } from "./source";

// ── The live wire shapes, named so the mapping below reads as a mapping ─────

type WireCatalog = {
  tasks: Task[];
  capabilities: { model: string; task: string }[];
  profiles?: {
    model: string;
    label: string | null;
    context_window: number | null;
    max_output: number | null;
    vendor_input_per_1m_usd: string | null;
    vendor_cached_input_per_1m_usd?: string | null;
    vendor_output_per_1m_usd: string | null;
    description: string;
    reads_images: boolean;
    thinks_first: boolean;
  }[];
  bindings: { tier: string; task: string; model: string; rank?: number }[];
  // 013, slice 12's read half. Absent from a Console still mid-rollout.
  failovers?: {
    day: string; tier: string; task: string; model: string;
    rank?: number; requests?: number;
  }[];
  rates: {
    model: string;
    task: string;
    unit: string;
    pricing_mode: string;
    input_per_1k: string;
    output_per_1k: string;
    cached_input_per_1k: string;
    credits_per_unit: string;
  }[];
};

type WireCred = {
  id: string;
  provider: string;
  api_base: string | null;
  label: string | null;
  org_slug: string | null;
  scope: string;
  created_at: string | null;
  revoked_at: string | null;
};

/** A task slug that is also a model kind.
 *
 * ⚠️ **`vision` and `reasoning` are NOT here, and they are not guessed.** They
 * are not tasks — no tier binds them — they are properties of a chat model, and
 * `model_profile` records them as of migration 012. Before that column existed
 * this mapping had no way to know, and inferring from a model name ("anything
 * with `-4o` reads images") would have produced a filter that quietly returns
 * the wrong models. An empty kind list is honest. A wrong one is not. */
const KIND_FROM_TASK: Record<string, ModelKind> = {
  chat: "chat",
  image: "image",
  transcribe: "transcribe",
  speak: "speak",
  embed: "embed",
};

export function catalogFromWire(w: WireCatalog): AiCatalog {
  const priced = new Set(
    w.rates
      .filter((r) => (r.pricing_mode || "").trim().toLowerCase() !== "unpriced")
      .map((r) => r.model),
  );

  const kinds = new Map<string, Set<ModelKind>>();
  for (const c of w.capabilities) {
    const k = KIND_FROM_TASK[c.task];
    if (!k) continue;
    if (!kinds.has(c.model)) kinds.set(c.model, new Set());
    kinds.get(c.model)?.add(k);
  }

  const profiles = new Map((w.profiles ?? []).map((p) => [p.model, p]));

  // ⚠️ **A model with NO profile row is normal, not an error.** Nothing is
  // seeded — a table of hardcoded context windows is a mirror of eleven
  // vendors' documentation and starts lying the first time one ships a model.
  // A missing row renders as em dashes, which is true.
  //
  // ⚠️ **A number arrives as a STRING and stays one until it is drawn.** These
  // are NUMERIC in the database. `Number()` here is the last step before the
  // display, never a round trip.
  const num = (v: string | null): number | null => {
    if (v === null) return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  const models: CatalogModel[] = [...new Set(w.capabilities.map((c) => c.model))]
    .sort()
    .map((id) => {
      const p = profiles.get(id);
      const kindList = [...(kinds.get(id) ?? [])];
      if (p?.reads_images) kindList.push("vision");
      if (p?.thinks_first) kindList.push("reasoning");
      return {
        id,
        // The id IS the label until somebody records one — better than a
        // prettified guess that stops matching what the Router uses.
        label: p?.label ?? id,
        provider: id.includes("/") ? id.slice(0, id.indexOf("/")) : id,
        kinds: kindList,
        contextWindow: p?.context_window ?? null,
        maxOutput: p?.max_output ?? null,
        inputPer1M: num(p?.vendor_input_per_1m_usd ?? null),
        cachedInputPer1M: num(p?.vendor_cached_input_per_1m_usd ?? null),
        outputPer1M: num(p?.vendor_output_per_1m_usd ?? null),
        description: p?.description ?? "",
        declared: true,
        priced: priced.has(id),
      };
    });

  // Bindings arrive as one row per STEP (migration 011), already ordered by
  // rank, and several rows share a (tier, task). Group them back into chains.
  //
  // ⚠️ **A row with no rank is rank 1.** The Console fills the column in, but a
  // deployment mid-rollout can answer from older code — and defaulting to 0
  // would put an unranked step ahead of a real primary, which is the one
  // ordering mistake nobody would see until a failover.
  const jobs = new Map<string, TierJob>();
  for (const b of w.bindings) {
    const k = `${b.tier}::${b.task}`;
    if (!jobs.has(k)) jobs.set(k, { tier: b.tier, task: b.task, chain: [] });
    jobs.get(k)?.chain.push({ model: b.model, rank: b.rank ?? 1 });
  }

  const byTier = new Map<string, TierJob[]>();
  for (const job of jobs.values()) {
    if (!byTier.has(job.tier)) byTier.set(job.tier, []);
    byTier.get(job.tier)?.push(job);
  }

  const tiers: Tier[] = [...byTier.keys()].sort().map((slug) => ({
    slug,
    label: slug,
    blurb: "",
    jobs: byTier.get(slug) ?? [],
  }));

  const rates: ModelRate[] = w.rates.map((r) => ({
    model: r.model,
    task: r.task,
    unit: r.unit,
    mode: r.pricing_mode,
    // ⚠️ Passed through as the STRINGS the Console sent. These are money, the
    // ledger is NUMERIC(14,4), and a parsed float re-formatted is how a total
    // stops matching the sum of its rows.
    inputPer1k: r.input_per_1k,
    outputPer1k: r.output_per_1k,
    cachedInputPer1k: r.cached_input_per_1k,
    creditsPerUnit: r.credits_per_unit,
  }));

  const failovers = (w.failovers ?? []).map((f) => ({
    day: (f.day ?? "").slice(0, 10),
    tier: f.tier, task: f.task, model: f.model,
    rank: f.rank ?? 2, requests: f.requests ?? 0,
  }));
  return { tasks: w.tasks, models, rates, tiers, accounts: [], failovers };
}

export function accountsFromWire(creds: WireCred[]): ProviderAccount[] {
  return creds.map((c) => ({
    id: c.id,
    provider: c.provider,
    label: c.label,
    apiBase: c.api_base,
    orgSlug: c.org_slug,
    createdAt: c.created_at,
    revokedAt: c.revoked_at,
    // 🔴 Nothing probes a vendor account. `unknown` is the only truthful value
    // and it must not render green — a health dot nobody measured is a claim.
    health: "unknown",
    lastCheckedAt: null,
    healthNote: null,
  }));
}

// ── The reads a page calls ──────────────────────────────────────────────────

type Attempt<T> = { ok: boolean; data?: T; note?: string };

/** Run one Console call and turn every outcome into an `Attempt`.
 *
 * ⚠️ **An unconfigured deployment is NOT an error.** It is the expected state
 * of a box nobody has finished setting up, and a red banner there sends
 * somebody hunting a fault that does not exist. It falls through to the
 * `missing` path, which says what is not configured. */
async function attempt<T>(
  call: () => Promise<{ status: number; body: string }>,
  map: (parsed: unknown) => T,
): Promise<Attempt<T>> {
  try {
    const res = await call();
    if (res.status === 200) return { ok: true, data: map(JSON.parse(res.body)) };
    return { ok: false, note: `The Console answered ${res.status}. ${res.body}` };
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) return { ok: false };
    throw e;
  }
}

export async function readAiCatalog(deps: Deps): Promise<Sourced<AiCatalog>> {
  const [cat, creds] = await Promise.all([
    attempt(() => readModelCatalog(deps), (p) => catalogFromWire(p as WireCatalog)),
    attempt(() => listProviderCreds(true, deps), (p) =>
      accountsFromWire((p as { credentials: WireCred[] }).credentials),
    ),
  ]);

  // ⚠️ **The catalog decides the origin, not the accounts.** They are two
  // endpoints and one screen. Letting the second downgrade the first would
  // hide a working catalog behind a provider read that happened to fail.
  const merged: Attempt<AiCatalog> = cat.ok
    ? { ok: true, data: { ...(cat.data as AiCatalog), accounts: creds.data ?? [] } }
    : cat;

  return resolve(merged, {
    sample: SAMPLE_CATALOG,
    empty: EMPTY_CATALOG,
    owed: `${OWED.models} ${OWED.tiers}`,
  });
}

export async function readAccounts(deps: Deps): Promise<Sourced<ProviderAccount[]>> {
  const r = await attempt(() => listProviderCreds(true, deps), (p) =>
    accountsFromWire((p as { credentials: WireCred[] }).credentials),
  );
  return resolve(r, {
    sample: SAMPLE_CATALOG.accounts,
    empty: [],
    owed: OWED.providers,
  });
}
