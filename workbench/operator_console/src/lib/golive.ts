// The go-live rail — the six steps between an empty console and a served
// customer, each judged from the live catalog.
//
// 🔴 **This exists because the owner said the system was confusing.** Every
// piece worked and nothing said how they compose: keys arm vendors, models
// join the catalog, tiers point at models, prices make calls bill, a customer
// needs a key and credits, and two flags turn it on. Six steps, in order,
// with the state of each derived from the same reads the pages use — never a
// second bookkeeping table that can disagree with the pages.
//
// ⚠️ **A step this console cannot VERIFY is `info`, never `done`.** The two
// flags live in a box's environment; claiming them done from here would be a
// green light nobody measured — the same lie the health dot told, removed for
// the same reason.
//
// ⚠️ Judgements are IMPORTED where they already exist. The tier step is
// `tierNextStep` (fallback.ts) verbatim — a second verdict on the same chains
// would disagree with the tiers page within a month.

import type { AiCatalog } from "./contract";
import { type ChainContext, tierNextStep } from "./fallback";
import { armedProviders } from "./providers";
import type { Tone } from "./tone";

export type StepState = "done" | "partial" | "todo" | "info";

export type GoLiveStep = {
  /** Stable key, also the anchor. */
  key: string;
  n: number;
  title: string;
  state: StepState;
  /** One or two sentences: what is true, and what to do about it. */
  detail: string;
  /** Where the work happens. */
  href: string;
  linkText: string;
};

export function stepTone(s: StepState): Tone {
  if (s === "done") return "ok";
  if (s === "partial") return "warn";
  if (s === "todo") return "danger";
  return "neutral";
}

/** The six steps, judged. Pure, so `golive.test.ts` can hold every state. */
export function goLiveSteps(cat: AiCatalog): GoLiveStep[] {
  const armed = armedProviders(cat.accounts);
  const declared = cat.models.filter((m) => m.declared);
  const unprofiled = declared.filter((m) => m.inputPer1M === null);

  const ctx: ChainContext = { models: cat.models, armed };
  const tierVerdict = tierNextStep(cat.tiers, ctx);

  // Priced or absorbed both count as DECIDED. `unpriced` is the omission.
  // D67: the price is per (tier, job) — the thing a customer actually buys —
  // so what needs pricing is every BOUND job, not every bound model.
  const decidedTier = new Set(
    cat.tierRates
      .filter((r) => r.mode !== "unpriced")
      .map((r) => `${r.tier}::${r.task}`),
  );
  const boundJobs = cat.tiers.flatMap((t) =>
    t.jobs.filter((j) => j.chain.length > 0).map((j) => ({
      tier: t.slug, task: j.task,
    })),
  );
  const boundUndecided = boundJobs.filter(
    (j) => !decidedTier.has(`${j.tier}::${j.task}`),
  );

  const steps: GoLiveStep[] = [
    {
      key: "keys",
      n: 1,
      title: "Install a vendor key",
      state: armed.length > 0 ? "done" : "todo",
      detail:
        armed.length > 0
          ? `Armed for ${armed.join(", ")}. Every customer without their own ` +
            "key runs on these accounts."
          : "No platform credential is installed, so every AI call fails. " +
            "Two vendors is the useful minimum — the second is the backup.",
      href: "/providers",
      linkText: "Providers",
    },
    {
      key: "models",
      n: 2,
      title: "Declare the models",
      state:
        declared.length === 0
          ? "todo"
          : unprofiled.length > 0
            ? "partial"
            : "done",
      detail:
        declared.length === 0
          ? "Nothing is declared, so no tier can point at anything. Declare " +
            "each model you intend to sell, under the vendor's own id."
          : unprofiled.length > 0
            ? `${declared.length} declared · ${unprofiled.length} missing ` +
              "vendor prices. A call on those cannot be COSTED, so their " +
              "margin reads as unknown until the price is entered."
            : `${declared.length} declared, each with the vendor's prices ` +
              "recorded — every call is costed.",
      href: "/models",
      linkText: "Models",
    },
    {
      key: "tiers",
      n: 3,
      title: "Point the tiers at models",
      // The tiers page's own verdict, translated to a rail state.
      state:
        tierVerdict.tone === "ok"
          ? "done"
          : tierVerdict.tone === "warn"
            ? "partial"
            : "todo",
      detail: `${tierVerdict.title}. ${tierVerdict.detail}`,
      href: "/tiers",
      linkText: "Tiers",
    },
    {
      key: "prices",
      n: 4,
      title: "Price the tiers",
      state:
        boundJobs.length === 0
          ? "info"
          : boundUndecided.length === 0
            // Tier rates alone are half the answer: until the credit has
            // its rupee price, a bank transfer has no official conversion.
            ? cat.creditPrice
              ? "done"
              : "partial"
            : decidedTier.size === 0
              ? "todo"
              : "partial",
      detail:
        boundJobs.length === 0
          ? "Nothing is bound yet, so there is nothing to price. Come back " +
            "after step 3."
          : boundUndecided.length === 0
            ? `Every tier job a customer can call has a decided price ` +
              `(${decidedTier.size} priced or absorbed). ` +
              (cat.creditPrice
                ? "A failover changes our cost, never theirs (D67)."
                : "One gap: the credit itself has no rupee price - save " +
                  "it on the Pricing page (H-42), or a bank transfer has " +
                  "no official credit conversion.")
            : `${boundUndecided.length} bound tier ${
                boundUndecided.length === 1 ? "job" : "jobs"
              } will answer customers and bill NOTHING — ` +
              boundUndecided
                .slice(0, 3)
                .map((j) => `${j.tier} (${j.task})`)
                .join(", ") +
              `${boundUndecided.length > 3 ? "…" : ""}. Price them, or mark ` +
              "them absorbed on purpose. What a credit costs in rupees is " +
              "saved on the same page (H-42).",
      href: "/pricing",
      linkText: "Price the tiers",
    },
    {
      key: "customer",
      n: 5,
      title: "Arm a customer",
      // The catalog read does not carry balances or keys, and this rail must
      // not claim what it cannot see.
      state: "info",
      detail:
        "On the customer's page: issue their cc_live_ key (shown exactly " +
        "once) and grant credits. Their deployment presents that key to the " +
        "Router on every call.",
      href: "/",
      linkText: "Customers",
    },
    {
      key: "flags",
      n: 6,
      title: "Turn it on",
      state: "info",
      detail:
        "Two switches on the customer's box, in this order: " +
        "ROUTER_SERVING_ENABLED routes their traffic through the Router " +
        "(H-69), and CUSTOMER_CONSOLE_SPEND_GATE starts refusing at zero " +
        "balance — only after prices are set, or funded customers get all " +
        "of the gate and none of the billing. Owner acts. Verify by the " +
        "deploy log line, not by this page.",
      href: "/usage",
      linkText: "Watch usage",
    },
  ];
  return steps;
}

/** One line when the derivable steps are all green, so the rail can shrink.
 *  Null while anything still needs doing — the full rail must stay up. */
export function railSummary(steps: GoLiveStep[]): string | null {
  const judged = steps.filter((s) => s.state !== "info");
  if (judged.every((s) => s.state === "done")) {
    return (
      "AI serving is configured: keys, models, tiers and prices are all " +
      "set. What remains is per-customer (step 5) and the two switches " +
      "(step 6)."
    );
  }
  return null;
}
