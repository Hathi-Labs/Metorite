import { redirect } from "next/navigation";

import { type EmailOtpEnv, isEmailOtpProviderReady } from "@/lib/emailOtp";

import CodeForm from "./CodeForm";

/**
 * The numeric-code entry page (WS-31 CP-2d slice 2, `customer_console.md`
 * §CP-2d clause 14).
 *
 * `auth.ts` points `pages.verifyRequest` here. Without it a person who asked
 * for a code lands on `@auth/core`'s built-in "check your email" page, which is
 * right for a magic link and a dead end for an OTP — there is nowhere to type
 * the code. Auth.js redirects here carrying the original query
 * (`@auth/core/lib/pages/index.js:107-110`).
 *
 * **Unreachable when the feature is dark**, and the ruling is a REDIRECT rather
 * than a 404 — the same call CP-2c slice 4 made for `/signup`. A 404 on a route
 * that exists in the bundle is a worse lie than a redirect to the page that can
 * actually sign you in, and an operator debugging a half-armed box learns more
 * from landing on `/signin` than from a not-found.
 *
 * Server component so the gate is read server-side: the client half receives
 * nothing but what the person typed.
 */

// The provider list — and therefore whether this page exists at all — is read
// per request, never baked at build. `signin/page.tsx` carries the measurement
// this mirrors.
export const dynamic = "force-dynamic";

export default function SignInCodePage() {
  if (!isEmailOtpProviderReady(process.env as EmailOtpEnv)) redirect("/signin");
  return <CodeForm />;
}
