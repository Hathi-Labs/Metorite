import { DEFAULT_OTP_MAX_AGE_S } from "@/lib/emailOtp";

/** How long an email code lives, in the minutes the copy names. */
const OTP_MINUTES = Math.round(DEFAULT_OTP_MAX_AGE_S / 60);

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
 *
 * ## Where an error lands (task 1529, 2026-11-03)
 *
 * `auth.ts` sets `pages.error` to `/signin`, so no Auth.js error reaches
 * `@auth/core`'s built-in error page. That page was a dead end. Its "Sign in"
 * button linked to `/api/auth/error?error=Verification/signin`, which rendered
 * a bare "Error" card. {@link codeStepRedirect} then sends the errors of the
 * code step back to `/signin/code`, where the person can type the code again.
 */
export function signInErrorMessage(code: string | null): string | null {
  if (!code) return null;
  switch (code) {
    case "OAuthCallbackError":
      return "Sign-in was cancelled or failed. Try again.";
    case "Verification":
      // The email code did not verify: mistyped, expired, already used, or
      // the code from an email sent to a different address. The gateway
      // cannot tell these apart, so the copy names all four and the two ways
      // out. A wrong code does not spend the right one (`consume_token`), so
      // typing it again is a real option.
      return `That code did not work. Check that you typed the newest code sent to this address. A code expires after ${OTP_MINUTES} minutes and works once. If it still fails, start again to get a new code.`;
    case "EmailSignInError":
      // The send leg threw: the hourly send limit, or the mail provider
      // refused. Both clear with time, and the person can do nothing else.
      return "We could not send a sign-in code. If you asked for several codes, wait a while before you ask again. Codes you already have still work.";
    case "Configuration":
      // `@auth/core` maps EVERY error it does not expose to this one type: a
      // gateway that did not answer, too many wrong codes in a row, a real
      // config fault. So the copy says "our side" and "try again", and does not
      // claim a misconfiguration that is usually not there.
      return "Sign-in could not finish because of a problem on our side. Wait a minute and try again. If it keeps failing, contact your admin.";
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
    // ── The SHAPE refusals `gateway/routes/signup.py` answers with a 400 ──────
    //
    // Added 2026-09-15. `SignUpForm` renders whatever `code` the hop returns
    // through this one seam, and these six were absent — so a person who got
    // one read *"Authentication error: InvalidGstin"*, which names an internal
    // symbol, calls a signup problem an authentication problem, and gives them
    // nothing to do about it.
    //
    // ⚠️ They are hard to reach, and that is not a reason to leave them raw.
    // The form gates every one client-side, so reaching one means the form and
    // the gateway have DRIFTED — exactly the moment a person deserves a
    // sentence rather than a symbol.
    case "MissingSlug":
      return "Please choose a workspace address for your organization.";
    case "InvalidSlug":
      return "That workspace address can't be used. Use lowercase letters, numbers and hyphens, with no leading or trailing hyphen.";
    case "MissingState":
      return "Please select the state your organization is registered in.";
    case "InvalidGstin":
      return "That GSTIN doesn't look valid. Check it, or leave it blank and add it later.";
    case "InvalidTeamSize":
      return "Please enter how many people will use Metorite, as a whole number.";
    case "InvalidBody":
      // R11: the request asserted a tenant or an identity. A person cannot
      // cause this from the form, so it names no field — it is "this is our
      // bug, not yours" copy, with the one action that might actually help.
      return "Something went wrong with that request. Please reload the page and try again.";
    default:
      // The type stays in the copy so a person who reports it gives us the
      // one word that finds it in the log.
      return `Sign-in failed (${code}). Try again. If it keeps failing, contact your admin.`;
  }
}

/**
 * The page to send an Auth.js error to when it belongs to the CODE step, or
 * `null` when `/signin` is the right page for it.
 *
 * `Verification` is the only such error. On `/signin` the person would have to
 * ask for a new code. On `/signin/code` the address is still filled in (the
 * `sessionStorage` hand-off) and they can type the code again.
 */
export function codeStepRedirect(code: string | null): string | null {
  return code === "Verification" ? "/signin/code?error=Verification" : null;
}
