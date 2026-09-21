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
import type { OrgRow } from "./format";
import { type ChainContext, tierNextStep } from "./fallback";
import { armedProviders, reportsCost } from "./providers";
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

/** Whether every live customer can actually be served — step 5's judgement.
 *
 * 🔴 **A customer with no `cc_live_` key cannot be served at all.** Their
 * deployment presents that key to the Router on every AI call. This step was
 * `info` forever, with the comment "the catalog read does not carry balances
 * or keys", so it could never go green and never go red. `hathi-labs-llp` was
 * provisioned and ran for weeks with zero keys, and nothing anywhere said so.
 * Measured 2026-09-21. `GET /orgs` carries the count now.
 *
 * ⚠️ **`undefined` is NOT zero.** A Console predating the field sends nothing,
 * and reading that as "no key" would put a red step on a customer who may well
 * have one. Unknown stays `info`, which is what it was.
 *
 * ⚠️ **Only ACTIVE organizations are judged.** A suspended or cancelled
 * customer needs no key, and nagging for one sends an operator to do work
 * that changes nothing — `reportsCost`'s argument, one surface over.
 */
export function customersWithoutKeys(
  orgs: OrgRow[] | undefined,
): { known: boolean; missing: string[]; live: number } {
  if (!orgs || orgs.length === 0) return { known: false, missing: [], live: 0 };
  const live = orgs.filter((o) => o.status === "active");
  // One organization that did not carry the field is enough to make the
  // whole answer unknown: a partial read must not read as a whole one.
  const known = live.every((o) => typeof o.live_keys === "number");
  if (!known) return { known: false, missing: [], live: live.length };
  return {
    known: true,
    missing: live.filter((o) => (o.live_keys ?? 0) === 0).map((o) => o.slug),
    live: live.length,
  };
}

/** The six steps, judged. Pure, so `golive.test.ts` can hold every state. */
export function goLiveSteps(cat: AiCatalog, orgs?: OrgRow[]): GoLiveStep[] {
  const armed = armedProviders(cat.accounts);
  const declared = cat.models.filter((m) => m.declared);
  // 🔴 **A model on a REPORTING vendor needs no recorded price** (migration
  // 031). Until OpenRouter, every unpriced model was a real hole: nothing
  // could cost its calls. A vendor that states its own charge fills that hole
  // with nobody typing a number, so nagging for one sends an operator to do
  // work that changes nothing. `reportsCost` is the single list.
  const unprofiled = declared.filter(
    (m) => m.inputPer1M === null && !reportsCost(m.provider),
  );
  const costedByVendor = declared.filter(
    (m) => m.inputPer1M === null && reportsCost(m.provider),
  );

  const keys = customersWithoutKeys(orgs);
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
      state: cat.accountsKnown && armed.length > 0 ? "done" : "todo",
      // ⚠️ A failed credential READ is not the fact "no credential exists".
      // Asserting "every AI call fails" from absent evidence sent operators
      // hunting a fault that may not exist — say UNKNOWN and point at the
      // refusal instead.
      detail: !cat.accountsKnown
        ? "The credential list did not load, so whether vendor keys are " +
          "armed is UNKNOWN — open Providers to see the Console's refusal."
        : armed.length > 0
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
            : costedByVendor.length > 0
              ? `${declared.length} declared. ${costedByVendor.length} are ` +
                "on a vendor that reports what each call cost, so they need " +
                "no price from you — the rest have theirs recorded. Every " +
                "call is costed."
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
      // 🔴 DERIVABLE since 2026-09-21. `GET /orgs` carries `live_keys`, so
      // this step can finally say whether a customer can be served — see
      // `customersWithoutKeys` for why `undefined` stays `info`.
      state: !keys.known
        ? "info"
        : keys.missing.length === 0
          ? "done"
          : "todo",
      detail: !keys.known
        ? "On the customer's page: issue their cc_live_ key (shown exactly " +
          "once) and grant credits. Their deployment presents that key to " +
          "the Router on every call."
        : keys.missing.length === 0
          ? `Every active customer holds a key (${keys.live}). Grant them ` +
            "credits on the customer's page if you have not."
          : `${keys.missing.length} of ${keys.live} active ` +
            `customer${keys.live === 1 ? "" : "s"} ` +
            `${keys.missing.length === 1 ? "holds" : "hold"} NO cc_live_ ` +
            `key: ${keys.missing.slice(0, 3).join(", ")}` +
            `${keys.missing.length > 3 ? "…" : ""}. Nothing their ` +
            "deployment does can be served until one is issued — on their " +
            "page, shown exactly once.",
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
