/**
 * Friendly copy for the `?error=` codes Auth.js v5 sends back to the sign-in
 * page.
 *
 * v5 emits ERROR TYPES (`OAuthCallbackError`, `Configuration`,
 * `AccessDenied`), not the v4 codes (`OAuthSignin` / `OAuthCallback`) the old
 * page keyed on — so a user cancelling at the IdP consent screen used to fall
 * through to the raw-code fallback. Kept as a pure module so the mapping is
 * testable without dragging `next-auth/react` into a node test
 * (`signin.test.ts`).
 *
 * ## The two CP-2b codes, and why Auth.js could not supply them
 *
 * Auth.js v5's vocabulary is FIXED: returning `false` from the `signIn`
 * callback produces `AccessDenied` and nothing else. Its shipped copy —
 * *"Your account isn't authorized for this workspace"* — is the exact phrasing
 * **D33.1 forbids** for two of CP-2b's refusals:
 *
 *   * the Customer Console was unreachable (or the cached answer aged past the
 *     staleness ceiling). The person has done nothing wrong, and a
 *     wrong-looking denial generates a support ticket and a password reset
 *     that fix nothing; and
 *   * the person belongs to more than one organization on this deployment and
 *     there is no chooser yet. That is not a failed authorization check.
 *
 * Two distinguishable reasons therefore need two codes Auth.js does not
 * define, carried by the `signIn` callback returning
 * `"/signin?error=<code>"` (`@auth/core` 0.41.2 — a string return redirects).
 * `AccessDenied` keeps its shipped copy for the cases where it is TRUE: no
 * membership visible to this deployment at all, and a `deleted` organization.
 *
 * Spec: `customer_console.md` §6(g) · §6(j) · CP-2b clauses 6 and 9.
 * Fence: `signin.test.ts` — *"errorCopy speaks the two CP-2b codes"*, which
 * asserts neither new string matches /access denied|isn't authorized/i.
 */
export function signInErrorMessage(code: string | null): string | null {
  if (!code) return null;
  switch (code) {
    case "OAuthCallbackError":
      return "Sign-in was cancelled or failed. Try again.";
    case "Configuration":
      return "Sign-in is misconfigured on the server. Contact your admin.";
    case "AccessDenied":
      return "Your account isn't authorized for this workspace. Ask your admin for an invite.";
    case "ConsoleUnavailable":
      return "Sign-in is temporarily unavailable. This is a problem on our side, not with your account — please try again in a few minutes.";
    case "WorkspaceChooserRequired":
      // Names the CAUSE, not the count: `errorCopy` maps a code to a STATIC
      // string, so a count would have to ride the query string — a second
      // field on a public URL, for a number that changes nothing the person
      // can act on. D33.1 asks for the cause (§6(g)).
      return "Your account belongs to more than one organization on this deployment, and there is no way to choose between them yet. Please contact your operator.";
    // ── WS-31 CP-2c (self-serve signup) — three new codes; ConsoleUnavailable
    // is reused for a signup that could not reach the tenant plane or the
    // Console. customer_console.md §6 CP-2c item 5.
    case "SignupDisabled":
      // The feature is off on this deployment. Not the person's fault, and not
      // a denial — there is simply no self-serve signup here.
      return "Self-serve signup is not available on this deployment. Ask your administrator for an invitation.";
    case "AlreadyMember":
      // They already have an organization; signing up again would create a
      // second. The redirect carries their org name, but the static copy names
      // no third party.
      return "You already belong to an organization. Sign in instead.";
    case "SlugTaken":
      // Names only that the chosen name is unavailable — never who holds it,
      // nor whether it exists on another deployment (no cross-tenant oracle).
      return "That organization name is already taken. Please choose a different one.";
    case "ReservedSlug":
      // WS-29 MT-1f, owner ruling B7. DISTINCT from SlugTaken on purpose, and
      // the distinction is safe: the reserved set is static, public and the
      // same for every caller, so it reveals nothing about any organization —
      // which is exactly what "taken" would, if the two shared one code.
      return "That workspace address is reserved for the platform. Please choose a different one.";
    default:
      return `Authentication error: ${code}`;
  }
}
