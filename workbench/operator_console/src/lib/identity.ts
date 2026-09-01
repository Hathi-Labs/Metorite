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
// page byte-identical to what it printed before. The Console reads the SAME
// variable name server-side (`customer_console.operators.signin_provider`), and
// the two must agree: this page builds the Supabase authorize link, and the
// Console reads the claim off whatever comes back. A page that offered Google
// while the Console still expected `tid` would refuse every operator.

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
export function signinProvider(
  env: Record<string, string | undefined> = process.env,
): SigninProvider {
  const raw = (env[SIGNIN_PROVIDER_FLAG] ?? "").trim().toLowerCase();
  return raw in PROVIDER_LABELS ? (raw as SigninProvider) : DEFAULT_SIGNIN_PROVIDER;
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

const TRUTHY = new Set(["1", "true", "yes", "on"]);

export type IdentityMode = "session" | "interim";

export function identityMode(
  env: Record<string, string | undefined> = process.env,
): IdentityMode {
  const raw = (env[IDENTITY_FLAG] ?? "").trim().toLowerCase();
  return TRUTHY.has(raw) ? "session" : "interim";
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
