// The Operator Console's ONE tone vocabulary.
//
// ⚠️ **This is `control_plane/src/lib/statusAccent.ts`'s idea, re-expressed.**
// It is not an import and must never become one: `customer_console.md` §2.4
// measures the cross-import count from the customer workbench as ZERO, and
// D35.4 keeps this app off the customer design system on purpose.
//
// What it borrows is the RULE, which is the part that matters — *one place a
// state becomes a look*. Before this file, `.pill.active` in `globals.css`,
// `.elevation.elevated`'s two hardcoded hex values and each page's ad-hoc
// `className={ok ? "ok-t" : "warn-t"}` were three vocabularies for one idea,
// and they already disagreed: a suspended org drew amber in the customers
// table and plain grey on the detail page.
//
// A tone is a MEASUREMENT — how broken, how urgent. It is never an identity.
// If you need "which tier is this" rather than "how bad is this", that is a
// categorical hue and it does not belong here.

export type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";

/** The class a chip wears. `globals.css` is the only place these are drawn. */
export function chipClass(tone: Tone, extra?: string): string {
  const base = tone === "neutral" ? "chip" : `chip ${tone}`;
  return extra ? `${base} ${extra}` : base;
}

/** Organization lifecycle. The four states the Console actually returns.
 *
 * ⚠️ `trial` is ACCENT, not ok. A trial is not a healthy paying customer and
 * an operator scanning for revenue must not read it as one. */
const LIFECYCLE: Record<string, Tone> = {
  active: "ok",
  trial: "accent",
  suspended: "warn",
  past_due: "warn",
  cancelled: "danger",
  deleted: "danger",
};

export function lifecycleTone(status: string): Tone {
  return LIFECYCLE[(status || "").trim().toLowerCase()] ?? "neutral";
}

/** Rate-card pricing mode (§6A.5).
 *
 * 🔴 `unpriced` is WARN and `absorbed` is not. D19.2's embeddings are free on
 * purpose, and drawing "deliberately free" the same as "nobody has set this
 * yet" is how a draft price ships — the exact confusion `describeRate` exists
 * to prevent in words. */
export function pricingTone(mode: string): Tone {
  const m = (mode || "").trim().toLowerCase();
  if (m === "unpriced") return "warn";
  if (m === "absorbed") return "accent";
  if (m === "priced") return "ok";
  return "neutral";
}
