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
