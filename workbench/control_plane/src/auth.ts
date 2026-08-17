import NextAuth from "next-auth";
import type { Provider } from "next-auth/providers";
import MicrosoftEntraId from "next-auth/providers/microsoft-entra-id";
import Google from "next-auth/providers/google";

/**
 * Sign-in for Metorite — **a platform, not one company's app** (WS-31 CP-0,
 * decision D33.1).
 *
 * ## What this used to be, and why it had to change
 *
 * This file used to carry one provider and this comment: *"the tenant-level app
 * registration ensures only users in the Fracktal Microsoft 365 directory can
 * sign in — no domain check needed."* That was correct while Metorite was
 * one company's brain and every user was a colleague. It is a **defect** for a
 * SaaS product, for the plainest possible reason: **a paying customer's staff
 * are not in our directory.** As written it could onboard exactly one customer.
 *
 * Two things follow, and both are implemented here:
 *
 * 1. **Identities arrive from directories we do not control.** Entra's issuer
 *    defaults to `organizations` (any work/school tenant, not just ours) and
 *    Google is offered beside it for customers who are not on Microsoft.
 *    ⚠️ The *code* being multi-directory is necessary and not sufficient — the
 *    Entra **app registration must itself be multi-tenant**, which is Azure
 *    configuration and 🔴 OWNER-GATE (`saas_operations_doctrine.md` §8).
 *
 * 2. **Auth fails CLOSED.** It used to fail OPEN: `isAuthEnabled` was false
 *    whenever no client id was set, and everything downstream read that as
 *    "run wide open". A production box that lost its auth env was therefore
 *    fully public *and reported itself healthy*. Now the bypass requires
 *    `NODE_ENV !== "production"` as well, so an unconfigured production
 *    deployment refuses traffic (`proxy.ts`) instead of admitting everyone as
 *    `DEV_IDENTITY` (`lib/gateway.ts`).
 *
 * ## The three exported predicates, which are NOT interchangeable
 *
 * - {@link isAuthConfigured} — a real provider exists. False on a laptop.
 * - {@link isDevBypass}      — no provider AND not production, i.e. the laptop
 *                              case, and the ONLY case that may run open.
 * - {@link isAuthEnabled}    — **auth is enforced.** This is what call sites
 *                              want, and it is deliberately `!isDevBypass`
 *                              rather than `hasProvider`: an unconfigured
 *                              production box must enforce, even though it has
 *                              no provider to enforce *with*. That combination
 *                              is a misconfiguration, and `proxy.ts` answers it
 *                              with a 503 rather than a login loop.
 *
 * Fenced by `src/lib/authPosture.test.ts`.
 */
// The three predicates live in `@/authPosture` — a pure, framework-free module —
// because they decide whether anonymous callers get in and must be testable
// without standing up NextAuth. Re-exported here so every call site keeps
// importing `@/auth`. See that file for why each is shaped as it is.
export { isAuthConfigured, isDevBypass } from "@/authPosture";
import { isAuthConfigured as hasProvider } from "@/authPosture";

const providers: Provider[] = [];
if (process.env.AUTH_MICROSOFT_ENTRA_ID_ID) {
  providers.push(
    MicrosoftEntraId({
      clientId: process.env.AUTH_MICROSOFT_ENTRA_ID_ID,
      clientSecret: process.env.AUTH_MICROSOFT_ENTRA_ID_SECRET ?? "",
      // `organizations` = any Entra work/school directory. Pin to a single
      // tenant id ONLY for a single-customer silo deployment; the pooled tier
      // must stay multi-directory or it cannot serve a second customer.
      issuer: `https://login.microsoftonline.com/${process.env.AUTH_MICROSOFT_ENTRA_ID_TENANT ?? "organizations"}/v2.0`,
    }),
  );
}
if (process.env.AUTH_GOOGLE_ID) {
  providers.push(
    Google({
      clientId: process.env.AUTH_GOOGLE_ID,
      clientSecret: process.env.AUTH_GOOGLE_SECRET ?? "",
    }),
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  secret: process.env.AUTH_SECRET ?? "dev-local-insecure-change-me",
  providers,
  callbacks: {
    async jwt({ token, profile, account }) {
      if (profile?.email) {
        token.email = profile.email as string;
      }
      if (account?.provider) {
        token.provider = account.provider;
      }
      return token;
    },
    async session({ session, token }) {
      if (token?.email) {
        session.user.email = token.email as string;
      }
      if (token?.name) {
        session.user.name = token.name as string;
      }
      return session;
    },
  },
  pages: {
    signIn: "/signin",
  },
});

/**
 * **Authentication is enforced.** The predicate every call site should use.
 *
 * Note it is `!isDevBypass`, NOT "a provider is configured": an unconfigured
 * *production* deployment enforces. See `@/authPosture` for why that combination
 * exists and `proxy.ts` for how it is answered.
 */
export { isAuthEnabled } from "@/authPosture";