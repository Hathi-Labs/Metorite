// The EMAIL CODE sign-in — WS-31 CP-12j. SERVER-SIDE decisions, pure.
//
// Spec: `project-docs/specs/operator_identity_and_access.md` §4.1b · **D71.3**.
//
// ⚠️ **This is a FALLBACK and never the front door.** D71.3 admits an email
// code when three things hold together: the Console runs in `registry`
// admission mode, `OPERATOR_ALLOW_EMAIL_OTP` is on, and the person's own
// `operator.allowed_methods` row permits it. This module answers only the
// SECOND of the three. The Console decides the other two, and it refuses on
// its own whatever this page offers — so a page that shows the form wrongly
// wastes somebody's time and opens nothing.
//
// ⚠️ **Why the browser talks to Supabase directly.** Supabase returns the
// code's session to whoever asked for it, and the OAuth path on this same page
// already sends the browser to Supabase. Proxying the two calls through the
// Next server would make this app a Supabase client for the first time, which
// is a new upstream for no gain. The browser posts the resulting access token
// to `POST /api/operator/session`, exactly as `login/callback` already does,
// and the `cc_sess_` token never leaves an httpOnly cookie.

export const EMAIL_OTP_FLAG = "OPERATOR_ALLOW_EMAIL_OTP";
export const ANON_KEY_FLAG = "OPERATOR_SUPABASE_ANON_KEY";

const TRUTHY = new Set(["1", "true", "yes", "on"]);

/** Is the email code offered on this deployment? */
export function emailOtpEnabled(
  env: Record<string, string | undefined> = process.env,
): boolean {
  return TRUTHY.has((env[EMAIL_OTP_FLAG] ?? "").trim().toLowerCase());
}

/**
 * 🔴 Is this key SAFE to hand to a browser?
 *
 * ⚠️ **This is the fence that makes the design above acceptable.** The anon
 * key is publishable by construction, and Supabase intends it to sit in a
 * browser. The `service_role` key is its exact opposite: same shape, same
 * place in the dashboard, one word different, and it bypasses row-level
 * security on every table. A person who pastes the wrong one into
 * `OPERATOR_SUPABASE_ANON_KEY` would publish it to every visitor of a login
 * page, and nothing would look wrong.
 *
 * So the page never renders a key this function refuses.
 *
 * Two key shapes exist, and both are checked:
 *   - the legacy JWT, whose payload carries `role` — `anon` passes, and
 *     `service_role` is refused.
 *   - the newer opaque key — `sb_publishable_…` passes, `sb_secret_…` is
 *     refused.
 *
 * ⚠️ **Anything it cannot parse is REFUSED, not allowed.** A key shape nobody
 * anticipated is exactly the case where "assume it is fine" publishes a
 * secret.
 */
export function isPublishableKey(key: string | undefined | null): boolean {
  const raw = (key ?? "").trim();
  if (!raw) return false;

  if (raw.startsWith("sb_publishable_")) return true;
  if (raw.startsWith("sb_secret_")) return false;

  // The legacy JWT. Read the payload's `role` claim. No signature check —
  // this asks "which key did somebody paste", not "is this token valid".
  const parts = raw.split(".");
  if (parts.length !== 3) return false;
  try {
    const json = Buffer.from(parts[1], "base64").toString("utf8");
    const claims = JSON.parse(json) as { role?: unknown };
    return claims.role === "anon";
  } catch {
    return false;
  }
}

/** Where the browser asks Supabase to send a code. */
export function otpStartUrl(base: string): string {
  return `${base.trim().replace(/\/$/, "")}/auth/v1/otp`;
}

/** Where the browser exchanges the code for a session. */
export function otpVerifyUrl(base: string): string {
  return `${base.trim().replace(/\/$/, "")}/auth/v1/verify`;
}

/**
 * Everything the client form needs, or `null` when the page must not show it.
 *
 * ⚠️ **`null` is the safe answer and it is returned for FOUR reasons**: the
 * flag is off, the project URL is unset, the key is unset, or the key is not
 * publishable. The caller renders nothing in all four, because a form that
 * cannot work is worse than no form — the person types their address, waits
 * for mail that never arrives, and has no way to tell which of the four it is.
 */
export function emailCodeConfig(
  env: Record<string, string | undefined> = process.env,
): { url: string; anonKey: string } | null {
  if (!emailOtpEnabled(env)) return null;
  const url = (env.OPERATOR_SUPABASE_URL ?? "").trim();
  const anonKey = (env[ANON_KEY_FLAG] ?? "").trim();
  if (!url || !anonKey) return null;
  if (!isPublishableKey(anonKey)) return null;
  return { url, anonKey };
}
