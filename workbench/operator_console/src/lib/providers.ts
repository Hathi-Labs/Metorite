// Our provider accounts — display logic (CP-10 slice 4).
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

export type ProviderCred = {
  id: string;
  provider: string;
  api_base: string | null;
  label: string | null;
  /** NULL means the PLATFORM account — the row the Router falls back to. */
  org_slug: string | null;
  scope: string;
  created_at: string | null;
  revoked_at: string | null;
};

export const PLATFORM = "platform";
export const BYOK = "byok";

/** Live means not revoked. Nothing subtler, and nothing is inferred. */
export function isLive(c: ProviderCred): boolean {
  return !c.revoked_at;
}

/** Platform means org-less. `scope` is the Console's word for the same fact,
 *  and this reads `org_slug` because that is the column the Router keys on. */
export function isPlatform(c: ProviderCred): boolean {
  return !c.org_slug;
}

/** The providers we can actually call for a tenant with no BYOK credential.
 *
 * ⚠️ **Live AND platform, both.** A revoked row and an org-scoped row each
 * look like coverage in a list and neither is.
 */
export function armedProviders(creds: ProviderCred[]): string[] {
  return [
    ...new Set(creds.filter((c) => isLive(c) && isPlatform(c)).map((c) => c.provider)),
  ].sort();
}

/** Organizations that insist on their own vendor account (§3.4 BYOK). */
export function byokOrgs(creds: ProviderCred[]): string[] {
  return [
    ...new Set(
      creds.filter((c) => isLive(c) && !isPlatform(c)).map((c) => c.org_slug as string),
    ),
  ].sort();
}

/** The banner an operator reads before anything else.
 *
 * 🔴 The zero case is the shipped state and it is the reason no AI call
 * works. It must not read as an empty table — an empty table looks like a
 * page nobody has used yet, not like a system that cannot serve.
 */
export function coverageLine(creds: ProviderCred[]): string {
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
  creds: ProviderCred[], provider: string, orgSlug: string | null,
): boolean {
  const target = (provider || "").trim().toLowerCase();
  if (!target) return false;
  return creds.some(
    (c) =>
      isLive(c) &&
      c.provider.toLowerCase() === target &&
      (c.org_slug ?? null) === (orgSlug || null),
  );
}

/** What one row says about itself, in an operator's words. */
export function describeScope(c: ProviderCred): string {
  return isPlatform(c) ? "platform (everyone)" : `BYOK — ${c.org_slug}`;
}
