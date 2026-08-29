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

/** The vendor a model id belongs to.
 *
 * ⚠️ **Split on the FIRST slash only.** Ids are `vendor/model`, and the model
 * half legitimately contains more slashes — `openrouter/anthropic/claude-3` is
 * one OpenRouter model, not an Anthropic one. Splitting on the last slash, or
 * on every slash, attributes it to the wrong vendor and the chip then claims a
 * credential we may not hold.
 *
 * An id with no slash is its own vendor: `whisper-1` is served by whoever the
 * platform credential belongs to, and guessing here would be worse than
 * showing the id back. */
export function providerOf(model: string): string {
  const s = (model || "").trim();
  const i = s.indexOf("/");
  return i > 0 ? s.slice(0, i) : s;
}

/** Every model that has DECLARED it can serve this task.
 *
 * 🔴 This is the list the tier form must offer. The old form was a free-text
 * box, so an operator could bind a tier to any string at all — and a model
 * that has not declared the capability produces a 500 on the first request
 * rather than a validation error. Offering only capable models makes the
 * broken state unreachable by hand. */
export function capableModelsFor(
  capabilities: { model: string; task: string }[],
  task: string,
): string[] {
  return [
    ...new Set(
      capabilities.filter((c) => c.task === task).map((c) => c.model),
    ),
  ].sort();
}

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

export type NextStep = {
  title: string;
  detail: string;
  tone: "ok" | "warn" | "danger";
};

/** The ONE thing to do next, in plain words.
 *
 * 🔴 **This replaced a 4×5 grid.** The matrix showed twenty cells to say four
 * facts — sixteen of them read "not bound" — and it still needed a horizontal
 * scrollbar. A grid earns its place when the data is dense. This data is
 * sparse, so the grid made a reader cross-reference a row against a column to
 * learn one thing.
 *
 * ⚠️ **One step at a time, and the order is the design.** Nothing can be
 * priced before it can be served, so "cannot serve" always outranks "unpriced".
 * Showing both at once makes a reader choose, and the wrong choice wastes an
 * afternoon pricing a binding that will never run.
 */
export function nextStep(m: Matrix): NextStep {
  if (m.tiers.length === 0) {
    return {
      title: "Set up your first tier",
      detail:
        "A tier is a speed and quality setting a customer picks — Fast, " +
        "Balanced or Powerful. Until one points at a model, every AI request " +
        "fails.",
      tone: "danger",
    };
  }
  if (m.counts.broken > 0) {
    const n = m.counts.broken;
    return {
      title: `Fix ${n} broken ${n === 1 ? "tier" : "tiers"}`,
      detail:
        `${n === 1 ? "A tier points" : "Some tiers point"} at a model that ` +
        "cannot do that job. The first customer request will fail. Change the " +
        "model, or tell us the model can do it.",
      tone: "danger",
    };
  }
  if (m.counts.unpriced > 0) {
    const n = m.counts.unpriced;
    return {
      // "Set your prices" while nothing is priced at all, then a countdown.
      title: m.counts.ready === 0 ? "Set your prices" : `Price ${n} more`,
      detail:
        `${n} ${n === 1 ? "model is" : "models are"} connected and ready, and ` +
        "none of them has a price. They will answer customers and charge " +
        "nothing. Setting a price is a business decision, not a technical one.",
      tone: "warn",
    };
  }
  return {
    title: "Everything is ready",
    detail:
      `${m.counts.ready} ${m.counts.ready === 1 ? "model" : "models"} can ` +
      "serve customers and every one has a price.",
    tone: "ok",
  };
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
