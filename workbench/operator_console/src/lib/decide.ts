// "Try a decision" — the rules behind the panel on the `tier-decide` card.
//
// Spec: `project-docs/specs/customer_console.md` §6A.14, CP-13b.
//
// 🔴 **The panel does NOT call the decide door.** It calls the operator route
// `POST /catalog/decide/try`, which uses the PLATFORM key, writes one audit
// row and writes NO usage row. A test through the door would land in a
// customer's usage.
//
// ⚠️ **The Console judges the request, not this file.** Clause 13's limits
// live in `decide.py`. This file only says whether the form is complete
// enough to send, and a Console 400 is relayed word for word.
//
// ⚠️ Pure on purpose. This app has no React renderer in its suite, so the
// logic sits here and `decide.test.ts` is the fence (DESIGN.md §7 rule 5).

/** The one tier the route tries. Migration `033` registers it. */
export const DECIDE_TIER = "tier-decide";

export const QUESTION_TYPES = ["boolean", "choice", "score"] as const;
export type QuestionType = (typeof QUESTION_TYPES)[number];

export const QUESTION_TYPE_LABEL: Record<QuestionType, string> = {
  boolean: "Yes or no",
  choice: "One of a list",
  score: "A score",
};

/** One criterion row on the form: a key and what it means. */
export type CriterionDraft = { key: string; text: string };

export type TryDraft = {
  state: string;
  type: QuestionType;
  instructions: string;
  criteria: CriterionDraft[];
};

/** The id the one question travels under. The answer comes back under it. */
export const QUESTION_ID = "q";

/** The criteria the Console will read. A row with no key is dropped. */
export function criteriaOf(rows: CriterionDraft[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) out[key] = row.text.trim();
  }
  return out;
}

/** Why the form cannot be sent yet, or null when it can.
 *
 * ⚠️ Only what a person can see is missing. The vendor's own limits (a score
 * takes 2 to 10 levels) are stated here too, because the Console would refuse
 * the same request with a 400, and the button can say so first. */
export function tryBlocker(d: TryDraft): string | null {
  if (!d.state.trim()) return "Type the state the question is about.";
  if (!d.instructions.trim()) return "Type the question.";
  const n = Object.keys(criteriaOf(d.criteria)).length;
  if (d.type === "choice" && n < 2) return "A choice needs two or more options.";
  if (d.type === "score" && (n < 2 || n > 10)) {
    return "A score needs from 2 to 10 levels.";
  }
  return null;
}

/** The request body for `POST /catalog/decide/try`. */
export function tryBody(d: TryDraft): {
  state: string;
  questions: Record<string, { type: QuestionType; instructions: string; criteria: Record<string, string> }>;
} {
  return {
    state: d.state,
    questions: {
      [QUESTION_ID]: {
        type: d.type,
        instructions: d.instructions.trim(),
        criteria: criteriaOf(d.criteria),
      },
    },
  };
}

/** What the route answers with. */
export type TryResult = {
  tier: string;
  model: string;
  answer: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  /** A decimal string, or null when the model profile holds no price. */
  vendorCostUsd: string | null;
};

const pct = (p: unknown): string | null =>
  typeof p === "number" && Number.isFinite(p) ? `${Math.round(p * 100)}%` : null;

/** One answer in words. `boolean` reads as a probability of "yes". */
export function answerText(raw: unknown): string {
  if (!raw || typeof raw !== "object") return "no readable answer";
  const a = raw as Record<string, unknown>;
  if (a.type === "boolean") {
    const p = pct(a.probability);
    return p ? `yes, with probability ${p}` : "no readable answer";
  }
  // CP-13h: a score names its nearest `level` and its 0-based `score`
  // position, which can be fractional (1.3).
  if (a.type === "score" && typeof a.level === "string" && typeof a.score === "number") {
    const c = pct(a.confidence);
    const where = `position ${a.score}`;
    return c ? `${a.level} (${where}, confidence ${c})` : `${a.level} (${where})`;
  }
  const pick = a.type === "choice" ? a.choice : a.score;
  if (typeof pick !== "string" && typeof pick !== "number") return "no readable answer";
  const c = pct(a.confidence);
  return c ? `${pick} (confidence ${c})` : String(pick);
}

const num = (v: unknown): number =>
  typeof v === "number" && Number.isFinite(v) ? v : 0;

/** The Console's JSON, in the shape the panel draws. Null when unreadable. */
export function readTryResult(body: unknown): TryResult | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const answers = b.answers as Record<string, unknown> | undefined;
  if (!answers || typeof answers !== "object") return null;
  const usage = (b.usage ?? {}) as Record<string, unknown>;
  const cost = b.vendor_cost_usd;
  return {
    tier: typeof b.tier === "string" ? b.tier : DECIDE_TIER,
    model: typeof b.model === "string" ? b.model : "",
    answer: answerText(answers[QUESTION_ID]),
    inputTokens: num(usage.input_tokens),
    outputTokens: num(usage.output_tokens),
    latencyMs: num(b.latency_ms),
    vendorCostUsd: typeof cost === "string" ? cost : null,
  };
}

/** The vendor cost, as a person reads it. Eight places, because
 *  `vendor_cost_usd` quantizes to eight and a rounded figure would hide a
 *  one-token call. */
export function costText(usd: string | null): string {
  if (usd === null) return "not priced";
  return `$${usd}`;
}

/** 🔴 A green panel with ZERO input tokens is the metering fault from clause
 *  8, not a success. The panel says so. */
export function meteringWarning(r: TryResult): string | null {
  return r.inputTokens === 0
    ? "The vendor reported zero input tokens. The meter cannot read this call."
    : null;
}
