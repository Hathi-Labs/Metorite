// The readiness matrix — what the model catalog actually MEANS.
//
// 🔴 **This exists because three tables that must agree cannot be diffed by
// eye.** `model_capability` says what a model CAN do. `tier_binding` says what
// we USE it for. `model_rate_card` says what we CHARGE. A tier is only
// sellable when all three line up on the same (model, task), and until now the
// page showed them as three stacked tables and left the join to the operator.
//
// The old surface did carry the two gap lists (`unbound`, `unserved`), and
// they were the right instinct — but a list of pairs answers "what is wrong"
// without answering "what can I sell", and the second question is the one an
// operator has at 2am with a customer waiting.
//
// ⚠️ **The logic lives here, not in the component.** This app's suite has no
// React renderer, so anything expressed in JSX is untested by construction.
// Every judgement the matrix makes is therefore a pure function over the
// catalog, and `readiness.test.ts` is the fence.

export type CellState = "ready" | "broken" | "unpriced" | "empty";

export type CatalogLike = {
  tasks: { slug: string; label: string; natural_unit: string }[];
  capabilities: { model: string; task: string }[];
  bindings: { tier: string; task: string; model: string }[];
  rates: { model: string; task: string; pricing_mode: string }[];
};

export type Cell = {
  tier: string;
  task: string;
  /** NULL means no tier_binding row — nothing is pointed at this pair. */
  model: string | null;
  state: CellState;
};

export type MatrixRow = { tier: string; cells: Cell[] };

export type Matrix = {
  tiers: string[];
  tasks: { slug: string; label: string; natural_unit: string }[];
  rows: MatrixRow[];
  counts: Record<CellState, number>;
};

const key = (model: string, task: string) => `${model}::${task}`;

/** Tiers, in a stable order, derived from the bindings themselves.
 *
 * ⚠️ There is no tier registry to read. Tiers exist because a binding names
 * one, so inventing a hardcoded list here would silently hide any tier the
 * operator creates — which is the whole set of tiers this page is for. */
export function tiersIn(bindings: { tier: string }[]): string[] {
  return [...new Set(bindings.map((b) => b.tier).filter(Boolean))].sort();
}

/** The state of one (tier, task) pair.
 *
 * ⚠️ **Order of precedence is the design.** `broken` outranks `unpriced`
 * because a bound-but-incapable pair 500s on the first request — it is the
 * only state here that is actually failing, and an unpriced row that also
 * cannot be served must not read as merely a pricing chore.
 */
export function cellState(
  model: string | null,
  task: string,
  capable: Set<string>,
  priced: Map<string, string>,
): CellState {
  if (!model) return "empty";
  if (!capable.has(key(model, task))) return "broken";
  const mode = priced.get(key(model, task));
  // A missing rate row and an explicit `unpriced` are the same fact to an
  // operator: nobody has said what this costs. `absorbed` is a decision and
  // counts as ready.
  if (!mode || mode.trim().toLowerCase() === "unpriced") return "unpriced";
  return "ready";
}

export function buildMatrix(data: CatalogLike): Matrix {
  const capable = new Set(data.capabilities.map((c) => key(c.model, c.task)));
  const priced = new Map(
    data.rates.map((r) => [key(r.model, r.task), r.pricing_mode] as const),
  );
  // Last binding wins for a (tier, task). Bindings are INSERT-only (§6A.5), so
  // the table holds superseded rows too — the Console returns the in-force set,
  // and this keeps the behaviour correct even if it ever returns history.
  const bound = new Map<string, string>();
  for (const b of data.bindings) bound.set(key(b.tier, b.task), b.model);

  const tiers = tiersIn(data.bindings);
  const counts: Record<CellState, number> = {
    ready: 0, broken: 0, unpriced: 0, empty: 0,
  };

  const rows = tiers.map((tier) => ({
    tier,
    cells: data.tasks.map((t) => {
      const model = bound.get(key(tier, t.slug)) ?? null;
      const state = cellState(model, t.slug, capable, priced);
      counts[state] += 1;
      return { tier, task: t.slug, model, state };
    }),
  }));

  return { tiers, tasks: data.tasks, rows, counts };
}

/** The one-line verdict above the matrix.
 *
 * 🔴 The zero-tier case is the shipped state and must not read as an empty
 * table. An empty table looks like a page nobody has used yet; this is a
 * system that cannot serve anyone. */
export function readinessLine(m: Matrix): string {
  if (m.tiers.length === 0) {
    return "No tier is bound to any model, so no AI request can be served. Bind one below.";
  }
  if (m.counts.broken > 0) {
    return `${m.counts.broken} binding${m.counts.broken === 1 ? "" : "s"} point at a model that cannot serve the task — each one 500s on the first request.`;
  }
  if (m.counts.unpriced > 0) {
    return `${m.counts.unpriced} pair${m.counts.unpriced === 1 ? " is" : "s are"} servable but unpriced. They will run and bill nothing.`;
  }
  return `${m.counts.ready} of ${m.counts.ready + m.counts.empty} pairs are servable and priced.`;
}
