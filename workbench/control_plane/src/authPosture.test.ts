/**
 * Authentication fails CLOSED, and accepts directories we do not own.
 *
 * Spec: `project-docs/specs/customer_console.md` CP-0 ·
 * `project-docs/specs/saas_operations_doctrine.md` §4 findings 1–2 · decision D33.1.
 *
 * ## The two defects this pins
 *
 * Metorite was one company's brain, and `auth.ts` said so: *"the
 * tenant-level app registration ensures only users in the Fracktal Microsoft 365
 * directory can sign in."* Two consequences, both fatal for a product sold to
 * other companies:
 *
 *   1. **A customer's staff are not in our directory.** One provider pinned to
 *      one Entra tenant onboards exactly one customer: us.
 *   2. **Auth failed OPEN.** `isAuthEnabled` was `Boolean(client_id)`, and every
 *      downstream reader treated false as "run wide open" — so a *production*
 *      box that lost its auth env served every route to anyone, while looking
 *      healthy. Losing a secret is not exotic; it is a bad deploy.
 *
 * ## Why these assertions are shaped this way
 *
 * The interesting row of the matrix is the one that never existed before: **no
 * provider AND production.** Under the old code it produced `isAuthEnabled ===
 * false` (open); here it must produce `true` (enforce). A test that only checked
 * "configured ⇒ enabled" would have passed against the defect — which is exactly
 * how the defect survived.
 *
 * Companion: `src/lib/authFailsClosed.test.ts` pins what the *callers* do with
 * these predicates.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { authPosture } from "@/authPosture";

const ENTRA = "entra-client-id";
const GOOGLE = "google-client-id";

describe("the posture matrix", () => {
  it("laptop (no provider, not production) is the ONLY open case", () => {
    expect(authPosture({ NODE_ENV: "development" })).toEqual({
      isAuthConfigured: false,
      isDevBypass: true,
      isAuthEnabled: false, // open — deliberately, on a laptop
    });
  });

  it("unconfigured PRODUCTION enforces — the fail-open defect (D33.1)", () => {
    // No provider to enforce *with*, but it must still enforce. The old gate
    // returned "not enabled" here and every reader took that as "wide open".
    // `proxy.ts` answers this combination with 503, not an open door.
    expect(authPosture({ NODE_ENV: "production" })).toEqual({
      isAuthConfigured: false,
      isDevBypass: false,
      isAuthEnabled: true,
    });
  });

  it("a configured provider enforces in development too", () => {
    expect(
      authPosture({ NODE_ENV: "development", AUTH_MICROSOFT_ENTRA_ID_ID: ENTRA }),
    ).toEqual({ isAuthConfigured: true, isDevBypass: false, isAuthEnabled: true });
  });

  it("treats an absent NODE_ENV as non-production", () => {
    // Vitest and plain `node script.js` both leave it unset. Enforcing here
    // would break every local test run; the production path is keyed on the
    // explicit value, which `next start` always sets.
    expect(authPosture({}).isDevBypass).toBe(true);
  });

  it("an empty-string client id is not a provider", () => {
    // `.env` files produce "" rather than undefined for a blank key, and a
    // truthiness check that got this wrong would enforce with nothing to
    // enforce with — a 503'd deployment that looks configured.
    expect(
      authPosture({ NODE_ENV: "production", AUTH_MICROSOFT_ENTRA_ID_ID: "" })
        .isAuthConfigured,
    ).toBe(false);
  });
});

describe("identities may come from directories we do not own", () => {
  it("Google alone is a complete configuration", () => {
    // A customer not on Microsoft must be able to sign in. Before CP-0 the only
    // provider was Entra, so this configuration authenticated nobody.
    expect(
      authPosture({ NODE_ENV: "production", AUTH_GOOGLE_ID: GOOGLE }),
    ).toEqual({ isAuthConfigured: true, isDevBypass: false, isAuthEnabled: true });
  });

  it("Entra defaults to ANY work directory, not a pinned tenant", () => {
    // `organizations` is the multi-tenant issuer. Hard-coding a tenant id would
    // silently restore "only our company can sign in" — the whole defect. A
    // silo deployment may pin it through env; the default may not.
    const source = readFileSync(new URL("./auth.ts", import.meta.url), "utf-8");

    expect(source).toContain('AUTH_MICROSOFT_ENTRA_ID_TENANT ?? "organizations"');
  });

  it("registers a provider for each configured directory", () => {
    // Guards the wiring the matrix above cannot see: both providers must be
    // *conditional*, so configuring only one does not construct the other with
    // an empty client id (which NextAuth surfaces as a broken sign-in button
    // rather than an error).
    const source = readFileSync(new URL("./auth.ts", import.meta.url), "utf-8");

    expect(source).toContain("if (process.env.AUTH_MICROSOFT_ENTRA_ID_ID)");
    expect(source).toContain("if (process.env.AUTH_GOOGLE_ID)");
  });
});
