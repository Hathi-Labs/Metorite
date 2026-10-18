/**
 * askOutcome — the one line an answered card folds to.
 *
 * Owner report, 2026-10-19: after the member sent a plan, the plan card
 * folded to an empty "> Plan" header above the approval that followed, and
 * the owner could not tell what it was. A card that waited on the member
 * now folds, once it has its answer, to a line that says what happened
 * (spec `projects_ai_chat.md` §29, rule 1). The line is the summary of the
 * card's fold (`RollupCard` `summary`), so the header reads
 * "Plan  19 tasks sent for approval".
 *
 * Pure, so the node-env vitest holds it. Fence: `askOutcome.test.ts`.
 */

import { planOutcome, planRowsFrom } from "@/app/projects/lib/planCard";
import { unfenced } from "@/lib/fencedText";

/**
 * The answer a "Not now" leaves on a card. PR #1152 owns the constant
 * (`lib/declineAnswer.ts` `DECLINED_ANSWER`). This copy goes when that PR
 * lands, and `askOutcome.test.ts` holds the words.
 */
const DECLINED = "__declined__";

/** The longest outcome line, in characters. The header cuts it anyway. */
export const OUTCOME_MAX = 80;

function clip(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > OUTCOME_MAX ? `${t.slice(0, OUTCOME_MAX - 1)}…` : t;
}

/** The template a spec draws at its root, with its data, or null. */
function rootTemplate(spec: unknown): { name: string; data: Record<string, unknown> } | null {
  if (!spec || typeof spec !== "object") return null;
  const s = spec as Record<string, unknown>;
  const root = (s.root ?? s.view ?? s) as Record<string, unknown>;
  if (!root || root.type !== "template") return null;
  const props = (root.props && typeof root.props === "object" ? root.props : {}) as Record<string, unknown>;
  const data = (props.data && typeof props.data === "object" ? props.data : {}) as Record<string, unknown>;
  return typeof props.name === "string" ? { name: props.name, data } : null;
}

/**
 * The outcome of an answered card, in a few words, or "" when it has no
 * answer. Each kind says what the member did:
 *
 * - a plan: "19 tasks sent for approval", or "cancelled, nothing created";
 * - an option picker: "Sent: Weekly";
 * - a form: "Sent";
 * - a decline ("Not now"): "Not answered", or a cancel on a plan;
 * - any other card that waited: "Answered".
 */
export function askOutcome(spec: unknown, answered: string | null | undefined): string {
  if (!answered) return "";
  const t = rootTemplate(spec);
  // The plan card's Cancel sends the decline since #1152, and it means a
  // cancel there: `planOutcome` reads any answer but a submit as one.
  if (t?.name === "planCard") return planOutcome(planRowsFrom(t.data.tasks), answered);
  if (answered === DECLINED) return "Not answered";
  switch (t?.name) {
    case "optionPicker": {
      const picked = answered.replace(/^\s*Selected:\s*/i, "");
      return picked ? clip(`Sent: ${unfenced(picked)}`) : "Sent";
    }
    case "formCard":
      return "Sent";
    default:
      return "Answered";
  }
}

/**
 * The values a form sent, from its answer "<label> — <json>", or null. A
 * form locked after a reload shows them in place of its defaults (review of
 * #1160). Anything but a JSON object is null.
 */
export function sentFormValues(answered: string | null | undefined): Record<string, unknown> | null {
  if (!answered) return null;
  const at = answered.indexOf(" — ");
  if (at < 0) return null;
  try {
    const v: unknown = JSON.parse(answered.slice(at + 3));
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
