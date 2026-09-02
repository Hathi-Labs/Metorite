// Which sign-in the console is running — SERVER-SIDE ONLY.
//
// WS-31 CP-12g. Spec: `project-docs/specs/operator_identity_and_access.md`
// §8 · D64.
//
// ⚠️ **Two paths, one switch, and the switch defaults OFF.** The console has
// been reachable since 2026-08-22 on a shared passphrase. Replacing that in
// one step would mean the team could not sign in until the owner had finished
// configuring Supabase (H-54) — a live console locked behind an owner action.
// So both paths exist and `OPERATOR_IDENTITY_ENABLED` chooses between them.
//
// `interim` — the passphrase of `staff.ts`. The cookie holds the passphrase
//   itself (F2), one secret admits everybody (F1), and nothing can be revoked
//   for one person (F5). This is what runs today.
//
// `session` — the operator signs in to Supabase, the Console exchanges that
//   for a `cc_sess_` session, and the cookie holds THAT. One person, an
//   expiry, a row, and a real name in every audit line.
//
// The flip is the owner's act. Deleting the interim path is a LATER act still,
// and only after one real sign-in has been confirmed — the order is in H-56,
// and getting it backwards locks the team out.

export const IDENTITY_FLAG = "OPERATOR_IDENTITY_ENABLED";

// ── Which directory signs staff in — D70, 2026-09-01 ───────────────────────
//
// ⚠️ **The owner told us we hold no Microsoft Entra directory.** `hathilabs.com`
// is a Google Workspace domain with an admin console, so D70 moves the provider
// to Google and the claim to `hd`. D35.3's intent is unchanged: one directory,
// ours, admin-managed.
//
// ⚠️ **The default is `azure`, so this ships dark.** An unset variable keeps the
// LOGIN page byte-identical to what it printed before. This module serves two
// pages, and `login/callback/page.tsx` did change its copy under D70 — it now
// names no provider at all. So the byte-identical claim is the login page's
// alone.
//
// The Console reads the SAME variable name server-side
// (`customer_console.operators.signin_provider`), and the two must agree: this
// page builds the Supabase authorize link, and the Console reads the claim off
// whatever comes back. A page that offered Google while the Console still
// expected `tid` would refuse every operator.
//
// ⚠️ **The variable therefore lives in TWO containers.** The Console reads it
// in the API process, and this module reads it in the Next process at request
// time. Set it in one only and sign-in fails with no message that names the
// cause. `project-docs/HANDOFF.md` H-54 carries the owner's placement table.

export const SIGNIN_PROVIDER_FLAG = "OPERATOR_SIGNIN_PROVIDER";

export type SigninProvider = "azure" | "google";

//: What each provider is called on the button, and in a refusal. The slug is
//: what Supabase wants in `?provider=`, and it is NOT the label — `azure` reads
//: "Microsoft" to the person pressing it.
export const PROVIDER_LABELS: Record<SigninProvider, string> = {
  azure: "Microsoft",
  google: "Google",
};

export const DEFAULT_SIGNIN_PROVIDER: SigninProvider = "azure";

// The provider this deployment signs staff in with.
//
// ⚠️ **An unknown value falls back to the default and never to "anything the
// env said".** The Console refuses an unknown name with a 503, so the page
// would strand the reader either way. Falling back keeps the page able to
// render the recovery note rather than throwing on a server component.
//
// ⚠️ **`Object.hasOwn`, never `in`.** `in` walks the prototype chain, so
// `constructor` and `__proto__` both passed the allowlist and read back an
// Object.prototype member as the provider label. Measured 2026-09-01:
// `constructor` yielded `function Object() { [native code] }` on the button.
// The input is `process.env` alone, so there was no attack path — but the
// sentence above was false, and a copy of this helper on user input would be
// a real hole. R7 — the fence is
// `identity.test.ts` "rejects a prototype-chain name".
export function signinProvider(
  env: Record<string, string | undefined> = process.env,
): SigninProvider {
  const raw = (env[SIGNIN_PROVIDER_FLAG] ?? "").trim().toLowerCase();
  return Object.hasOwn(PROVIDER_LABELS, raw)
    ? (raw as SigninProvider)
    : DEFAULT_SIGNIN_PROVIDER;
}

// The name a person reads on the sign-in button.
export function providerLabel(
  env: Record<string, string | undefined> = process.env,
): string {
  return PROVIDER_LABELS[signinProvider(env)];
}

//: The cookie the `cc_sess_` token rides in. A DIFFERENT name from
//: `STAFF_COOKIE`, deliberately: flipping the flag must not make the console
//: read a passphrase as though it were a session, and a shared name would let
//: a stale cookie do exactly that.
export const SESSION_COOKIE = "operator_session";

// ── The passphrase BACK DOOR, and why it exists — CP-12k, 2026-09-02 ───────
//
// ⛔ **This weakens §8 done-when 29 ON PURPOSE, and the owner asked for it.**
// That rule says one door at a time: while `OPERATOR_IDENTITY_ENABLED` is on,
// a passphrase cookie is refused. The reasoning was sound — two live doors
// while only one is being reasoned about.
//
// 🔴 **What changed is evidence, not opinion.** On 2026-09-02 an agent flipped
// the identity flag on a box where no sign-in could succeed, and the console
// admitted NOBODY. Recovery needed an ssh session and an env edit. A console
// whose recovery path is "find somebody with shell access" is a console the
// owner does not control.
//
// ⚠️ **It defaults OFF, so done-when 29 still holds everywhere else.** A box
// that does not set this behaves exactly as it did before.
//
// ⚠️ **What it costs, stated plainly.** While this is on, ONE shared secret
// admits anybody who holds it, with no per-person identity and nothing to
// revoke for one leaver — F1, F2 and F5 all return. It is a backup key, and
// it should be turned off once email sign-in is proven.
export const PASSPHRASE_FALLBACK_FLAG = "OPERATOR_PASSPHRASE_FALLBACK";

const TRUTHY = new Set(["1", "true", "yes", "on"]);

export type IdentityMode = "session" | "interim";

export function identityMode(
  env: Record<string, string | undefined> = process.env,
): IdentityMode {
  const raw = (env[IDENTITY_FLAG] ?? "").trim().toLowerCase();
  return TRUTHY.has(raw) ? "session" : "interim";
}

/**
 * May a passphrase still open the console while identity sign-in is on?
 *
 * ⚠️ **Meaningless unless {@link usesSessions} is true.** On the interim path
 * the passphrase is the ONLY door, and this value changes nothing there. It
 * exists to answer "is there a second door BESIDE the identity one".
 */
export function passphraseFallbackEnabled(
  env: Record<string, string | undefined> = process.env,
): boolean {
  const raw = (env[PASSPHRASE_FALLBACK_FLAG] ?? "").trim().toLowerCase();
  return TRUTHY.has(raw);
}

// True when a `cc_sess_` token is what the console should be carrying.
export function usesSessions(
  env: Record<string, string | undefined> = process.env,
): boolean {
  return identityMode(env) === "session";
}

// ⚠️ Shape check only — this does NOT authenticate anything. The Console
// verifies the token against `operator_session` on every request. Checking the
// prefix here only stops the console from forwarding an obviously wrong value
// (a stale passphrase cookie, say) and reading the resulting 401 as "your
// session expired" when the real answer is "you are on the wrong path".
export function looksLikeSession(value: string | null | undefined): boolean {
  return (value ?? "").trim().startsWith("cc_sess_");
}
