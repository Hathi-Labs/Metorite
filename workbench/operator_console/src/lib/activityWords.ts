// The Activity page in words — WS-50 slice 5.
//
// 🔴 **Why.** The Activity page printed raw action codes ("org.lifecycle",
// "catalog.tier_rate") and each row's detail as a JSON blob. A person read
// both by decoding them. This file gives each code a sentence and each detail
// a line of "name: value" pairs. The code stays in a tooltip for a search.
//
// ⚠️ Pure functions only. `activityWords.test.ts` is the fence.

export const ACTION_WORDS: Record<string, string> = {
  refused: "Refused",
  "org.provision": "Created a customer",
  "org.lifecycle": "Changed account access",
  "org.purge": "Purged a customer's data",
  "subscription.activate_manual": "Started a paid plan",
  "credits.grant": "Added credits",
  "credits.starter": "Added starter credits",
  "credits.starter_skipped": "Skipped starter credits",
  "seat.assign": "Gave a seat",
  "seat.release": "Released a seat",
  "seat.count": "Changed the seat count",
  "member.add": "Added a member",
  "key.issue": "Issued an API key",
  "key.revoke": "Revoked an API key",
  "discount.issue": "Created a discount code",
  "discount.redeem": "Used a discount code",
  "order.create": "Started a payment",
  "payment.captured": "Payment received",
  "payments.amount_mismatch": "Payment amount did not match",
  "payments.attempt_failed": "Payment failed",
  "payments.capture_after_terminal": "Payment arrived after the order closed",
  "payments.webhook_unknown_order": "Payment for an unknown order",
  "payments.redeem_attempt": "Tried a discount code",
  "catalog.binding": "Changed a tier's models",
  "catalog.unbind": "Took a tier off the air",
  "catalog.capability": "Declared a model",
  "catalog.capability_removed": "Removed a model",
  "catalog.profile": "Saved a model's details",
  "catalog.tier_rate": "Changed a tier's price",
  "catalog.tier_margin": "Changed a tier's profit goal",
  "catalog.credit_price": "Changed the credit price",
  "provider.credential.install": "Installed a vendor key",
  "provider.credential.revoke": "Removed a vendor key",
  "operator.elevate": "Raised own access for a while",
  "operator.signin": "Signed in",
  "operator.add": "Added an operator",
  "catalog.feed_sync": "Fetched the vendor price list",
  "provider.health.check": "Checked the vendor balances",
  "operator.update": "Changed an operator",
  "registry.resolve": "Looked up a customer's box",
  "router.failover": "A backup model answered",
  "router.unpriced_tier": "Served a tier with no price",
  "router.capability_missing": "A model could not do the job",
  "router.run_ceiling_tripped": "Stopped a run at its cost limit",
  "router.byok_unbilled": "Served on the customer's own key, not charged",
  "router.decide_unreadable": "Could not read a decision reply",
  "router.usage_unreadable": "Could not read the vendor's usage",
  "router.usage_partition_failed": "Could not split a call's usage",
  "router.unmeasured_quantity": "Served a call with no measured size",
};

/** A code in words, or the code itself when it is new. */
export function actionWords(code: string): string {
  return ACTION_WORDS[code] ?? code;
}

const KEY_WORDS: Record<string, string> = {
  delta: "credits",
  price_paid_inr: "paid ₹",
  from: "from",
  to: "to",
  reason: "reason",
  plan_slug: "plan",
  seats: "seats",
  email: "person",
  member_email: "person",
  owner_email: "owner",
  ref: "reference",
  tier: "tier",
  task: "job",
  model: "model",
  provider: "vendor",
};

/** "name: value · name: value", from a detail object. Never a JSON blob. */
export function describeDetail(detail: unknown): string {
  if (detail === null || detail === undefined) return "";
  if (typeof detail !== "object") return String(detail);
  const parts: string[] = [];
  for (const [k, v] of Object.entries(detail as Record<string, unknown>)) {
    if (v === null || v === undefined || v === "") continue;
    const name = KEY_WORDS[k] ?? k.replace(/_/g, " ");
    const value =
      typeof v === "object" ? (Array.isArray(v) ? v.join(", ") : JSON.stringify(v)) : String(v);
    parts.push(`${name}: ${value.length > 80 ? `${value.slice(0, 77)}…` : value}`);
  }
  return parts.join(" · ");
}
