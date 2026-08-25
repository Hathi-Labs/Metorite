// The staff-only access gate — SERVER-SIDE ONLY.
//
// INTERIM — replace with staff Entra, CP-8 (D35.3). The Operator Console is
// meant to pin OUR OWN Microsoft Entra directory (staff-only; the inverse of
// the customer product's multi-directory rule). That staff Entra app is an
// OWNER dependency that is NOT set up yet, so — to make this app testable DARK
// now — access is gated behind a server-side shared secret. This is NOT the
// intended auth; it is the placeholder that keeps the surface staff-only until
// the Entra staff app exists.
//
// ⚠️ The gate is PLATFORM-STAFF identity, never "any org owner". A customer's
// own org-owner is NOT a platform operator: this surface is cross-org, and
// widening its gate to a customer identity is exactly the cross-customer
// exposure D35 exists to prevent. When the staff Entra app lands, this whole
// module is replaced by an Entra-directory check — the secret is not a
// long-term credential.
//
// Fails CLOSED when the secret is unset, exactly as the Console's own operator
// gate 503s without its operator token configured (D33.1's lesson).

export class StaffUnconfigured extends Error {}
export class StaffForbidden extends Error {}

//: The cookie/header the interim secret is presented in. Named once here so the
//: session route, the page guard and the BFF routes cannot drift.
export const STAFF_COOKIE = "operator_staff";

// Constant-time string comparison — a length-leaking `===` on a shared secret
// is the kind of thing a security review flags even in a dark interim gate.
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export function staffSecret(
  env: Record<string, string | undefined> = process.env,
): string | undefined {
  const s = (env.OPERATOR_CONSOLE_STAFF_SECRET ?? "").trim();
  return s.length > 0 ? s : undefined;
}

// Returns true if the presented value authenticates a staff operator. Throws
// StaffUnconfigured (fail closed) when the secret is unset.
export function isStaff(
  presented: string | null | undefined,
  env: Record<string, string | undefined> = process.env,
): boolean {
  const expected = staffSecret(env);
  if (!expected) {
    throw new StaffUnconfigured(
      "OPERATOR_CONSOLE_STAFF_SECRET is not configured — the operator console " +
        "refuses everyone until the staff gate (interim secret, later Entra) is set",
    );
  }
  const token = (presented ?? "").trim();
  return token.length > 0 && constantTimeEqual(token, expected);
}

// The imperative gate for a route/page: raises StaffForbidden on a bad or
// missing secret, StaffUnconfigured when the secret is unset.
export function requireStaff(
  presented: string | null | undefined,
  env: Record<string, string | undefined> = process.env,
): void {
  if (!isStaff(presented, env)) {
    throw new StaffForbidden("platform-staff secret required");
  }
}
