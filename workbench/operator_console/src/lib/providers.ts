// Our provider accounts — display logic (CP-10 slice 4, rebuilt WS-31).
//
// Extracted from `app/providers/ProviderAdmin.tsx` so it can be tested: this
// console's suite is `lib/*.test.ts` and carries no React renderer.
//
// What is worth testing here is what an operator CONCLUDES from the page. Two
// wrong conclusions cost real money:
//
//   1. "We're covered" when the only live credential is scoped to one org.
//      Every other tenant's AI is then dead and the page said it was fine.
//   2. "We're covered" when the row they are reading is revoked.
//
// Both are the same mistake — counting rows instead of counting LIVE PLATFORM
// rows — so both are one function with tests on either side of it.
//
// ⚠️ **The type is `ProviderAccount` from `contract.ts`, not a second shape.**
// This file used to carry its own snake_case `ProviderCred` mirroring the
// Console's JSON. Two shapes for one object is how a vocabulary splits, so the
// mapping now happens once in `read.ts` and everything downstream speaks one
// language.

import type { ProviderAccount } from "./contract";

export type { ProviderAccount };

// ⚠️ **The health helpers are GONE, on a decision (2026-08-30), not lost.**
// `healthTone`/`healthLabel` painted a chip from `ProviderAccount.health`, and
// nothing on the backend probes a vendor account, so on live data every card
// wore a grey "never checked" — a column of nothing on the page an operator
// reads most. The contract keeps the `health` fields as the probe slice's
// promise; the display returns when something measures.

/** Live means not revoked. Nothing subtler, and nothing is inferred. */
export function isLive(c: ProviderAccount): boolean {
  return !c.revokedAt;
}

/** Platform means org-less — the row the Router falls back to for a tenant
 *  that has not brought its own key. */
export function isPlatform(c: ProviderAccount): boolean {
  return !c.orgSlug;
}

/** The providers we can actually call for a tenant with no BYOK credential.
 *
 * ⚠️ **Live AND platform, both.** A revoked row and an org-scoped row each
 * look like coverage in a list and neither is.
 */
export function armedProviders(creds: ProviderAccount[]): string[] {
  return [
    ...new Set(creds.filter((c) => isLive(c) && isPlatform(c)).map((c) => c.provider)),
  ].sort();
}

/** Organizations that insist on their own vendor account (§3.4 BYOK). */
export function byokOrgs(creds: ProviderAccount[]): string[] {
  return [
    ...new Set(
      creds.filter((c) => isLive(c) && !isPlatform(c)).map((c) => c.orgSlug as string),
    ),
  ].sort();
}

/** The banner an operator reads before anything else.
 *
 * 🔴 The zero case is the shipped state and it is the reason no AI call
 * works. It must not read as an empty table — an empty table looks like a
 * page nobody has used yet, not like a system that cannot serve.
 */
export function coverageLine(creds: ProviderAccount[]): string {
  const armed = armedProviders(creds);
  if (armed.length === 0) {
    return (
      "No platform credential is installed, so every AI call fails. " +
      "Install one below."
    );
  }
  return `Platform account armed for: ${armed.join(", ")}.`;
}

/** Would installing this provider REPLACE a live credential?
 *
 * ⚠️ The POST that installs is also the POST that rotates — it revokes the
 * live row in the same transaction. An operator who does not know that reads
 * a silent replacement as a duplicate insert.
 */
export function wouldRotate(
  creds: ProviderAccount[],
  provider: string,
  orgSlug: string | null,
): boolean {
  const target = (provider || "").trim().toLowerCase();
  if (!target) return false;
  return creds.some(
    (c) =>
      isLive(c) &&
      c.provider.toLowerCase() === target &&
      (c.orgSlug ?? null) === (orgSlug || null),
  );
}

/** What one row says about itself, in an operator's words. */
export function describeScope(c: ProviderAccount): string {
  return isPlatform(c) ? "platform (everyone)" : `BYOK — ${c.orgSlug}`;
}

// ── One card per vendor ─────────────────────────────────────────────────────
//
// 🔴 **ONE live platform key per vendor. The DATABASE says so, not this
// file.** `provider_credential_live_uniq` (004) is unique over
// `(provider, org)` where `revoked_at IS NULL`, and the second insert was
// tried against the real schema on 2026-08-30 and refused. An earlier version
// of this comment claimed a second platform key was how we survive a rate
// limit — that state cannot exist, and the sample data that drew it modelled
// something impossible. What a vendor CAN legally have beside its one
// platform key: one BYOK key per organization, and any number of revoked
// rows.

export type ProviderGroup = {
  provider: string;
  /** The live org-less key, or null. ⚠️ Typed SINGULAR because the database
   *  enforces it — a list here would invite the card to draw a state that
   *  cannot exist, which is exactly what the sample data did. */
  platform: ProviderAccount | null;
  /** Not revoked, scoped to one organization each. */
  byok: ProviderAccount[];
  /** Revoked, of either kind. Kept for the record, drawn quietly. */
  revoked: ProviderAccount[];
};

/** Group the flat list into one entry per vendor, live rows first.
 *
 * ⚠️ Sorted with ARMED vendors first, then alphabetically. A vendor we cannot
 * call is the one that needs attention, but it is also the one an operator is
 * least likely to be looking for — putting it at the top would bury the
 * working accounts under whatever was abandoned longest ago. The banner above
 * carries the alarm instead.
 *
 * 🔴 **`include` is what makes the page a CATALOGUE rather than a receipt.**
 * With no argument this returns only vendors we already hold a key for — which
 * on a fresh install is nothing at all, so the page that exists to get a key
 * installed showed an empty state and a row of pills. Passing the known vendor
 * list draws a card for every vendor whether or not it has a key, so
 * installing one is a click on the thing you were already looking at. A vendor
 * in `creds` but not in `include` still appears: we hold its key, and hiding a
 * live credential because it is not on a list is how a key gets forgotten. */
export function groupByProvider(
  creds: ProviderAccount[],
  include: readonly string[] = [],
): ProviderGroup[] {
  const names = [
    ...new Set([...creds.map((c) => c.provider), ...include]),
  ].sort();
  const groups = names.map((provider) => {
    const mine = creds.filter((c) => c.provider === provider);
    // ⚠️ `[0] ?? null`, deliberately shrugging at a duplicate. The database
    // makes a second live platform row impossible, so defending against one
    // here would be code that can never run — and if the read ever DID hand
    // us two, drawing the first is no worse than any other recovery.
    return {
      provider,
      platform: mine.find((c) => isLive(c) && isPlatform(c)) ?? null,
      byok: mine.filter((c) => isLive(c) && !isPlatform(c)),
      revoked: mine.filter((c) => !isLive(c)),
    };
  });
  return groups.sort(
    (a, b) =>
      Number(b.platform !== null) - Number(a.platform !== null) ||
      a.provider.localeCompare(b.provider),
  );
}

/** Where one vendor stands, in one word. The filter chips count these.
 *
 * ⚠️ **`untouched` and `dropped` are different facts and must not merge.**
 * A vendor nobody has set up is a to-do. A vendor whose only key is revoked is
 * a decision somebody took, and drawing it as "not set up" invites the next
 * operator to quietly undo it. */
export type GroupStatus = "connected" | "byok-only" | "dropped" | "untouched";

export function groupStatus(g: ProviderGroup): GroupStatus {
  if (g.platform) return "connected";
  if (g.byok.length > 0) return "byok-only";
  return g.revoked.length > 0 ? "dropped" : "untouched";
}

/** What one vendor card says in its header, in one line. */
export function groupLine(g: ProviderGroup): string {
  if (!g.platform) {
    if (g.byok.length > 0) {
      return `No account for everyone — ${g.byok.length} organization${
        g.byok.length === 1 ? "" : "s"
      } bring their own.`;
    }
    // ⚠️ Two different zeroes. See `groupStatus`.
    return g.revoked.length > 0
      ? "The key was removed. Nothing here can be called."
      : "Not set up.";
  }
  const since = g.platform.createdAt
    ? `Serving every customer since ${g.platform.createdAt.slice(0, 10)}.`
    : "Serving every customer.";
  return g.byok.length > 0
    ? `${since} ${g.byok.length} bring their own.`
    : since;
}
