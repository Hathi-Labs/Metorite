// Where a customer is in its life, and what the operator does next.
// WS-50 slice 3, decision D94.
//
// 🔴 **Why this exists.** The customer page showed a status chip and a dozen
// panels, and nothing said which act came next. "Activate subscription" and
// "Activate account" were two different acts with almost the same name. This
// file draws the whole path, marks where the customer is, and names the ONE
// next act and the tab it lives on.
//
// ⚠️ Pure functions only. `lifecycle.test.ts` is the fence.

export type StepState = "done" | "current" | "todo";

export type LifecycleStep = { key: string; label: string; state: StepState };

/** The path, in order. Suspended sits beside Paid: it is a pause, not a step. */
const PATH = [
  { key: "trial", label: "Trial" },
  { key: "active", label: "Paid" },
  { key: "cancelled", label: "Cancelled" },
  { key: "deleted", label: "Deleted" },
] as const;

/** Which point on the path a status sits at. */
function position(status: string): number {
  if (status === "trial") return 0;
  if (status === "active" || status === "suspended" || status === "past_due") return 1;
  if (status === "cancelled") return 2;
  if (status === "deleted") return 3;
  return 0;
}

export function lifecycleSteps(status: string): LifecycleStep[] {
  const at = position(status);
  return PATH.map((p, i) => ({
    key: p.key,
    label: i === 1 && status === "suspended" ? "Paid (suspended)" : p.label,
    state: i < at ? "done" : i === at ? "current" : "todo",
  }));
}

export type TabKey = "overview" | "billing" | "people" | "access";

export const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "billing", label: "Billing & credits" },
  { key: "people", label: "People & seats" },
  { key: "access", label: "Access & keys" },
];

/** A `?tab=` value from the URL, or the overview when it is not a tab. */
export function tabFrom(raw: string | string[] | undefined): TabKey {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return TABS.some((t) => t.key === v) ? (v as TabKey) : "overview";
}

/** The one next act for this customer, and the tab it lives on. */
export function nextStep(
  status: string,
  subscriptionStatus: string | null,
): { text: string; tab: TabKey } | null {
  if (status === "trial" && subscriptionStatus === "active") {
    return {
      text: "Their paid plan is active, but the account still says trial. End the trial.",
      tab: "access",
    };
  }
  if (status === "trial") {
    return {
      text: "On a free trial. When they pay you, start their paid plan.",
      tab: "billing",
    };
  }
  if (status === "past_due" || subscriptionStatus === "past_due") {
    return { text: "A payment is overdue. Collect it, or suspend access.", tab: "access" };
  }
  if (status === "suspended") {
    return {
      text: "Suspended: they can sign in to pay, but AI and seat changes are locked. Resume access when they pay.",
      tab: "access",
    };
  }
  if (status === "cancelled") {
    return {
      text: "Cancelled: they can sign in to export their data. Reinstate them, or mark the account deleted when the window ends.",
      tab: "access",
    };
  }
  if (status === "deleted") {
    return {
      text: "Deleted: nobody can sign in. Their data stays until you purge it.",
      tab: "access",
    };
  }
  return null;
}
