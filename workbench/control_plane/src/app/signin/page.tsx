import { configuredProviders, type AuthEnv } from "@/authPosture";

import SignInForm from "./SignInForm";

/**
 * The sign-in page derives its buttons from the CONFIGURED providers via the
 * one provider seam (`@/authPosture`, the same module `auth.ts` registers
 * from and `proxy.ts` decides with) — so offering a customer's IdP is
 * configuration, never a code change. This page used to hardcode a single
 * Microsoft button plus copy claiming a domain restriction nothing enforced;
 * as a SaaS product it could onboard exactly one customer that way.
 *
 * Server component on purpose: provider env vars are server-only, and the
 * client half (`SignInForm`) receives the derived list as props.
 */

// Without this the route is STATICALLY PRERENDERED and the provider list
// freezes at `next build` time — while NextAuth reads the same env at
// runtime, so an env-plus-restart change would register a provider whose
// button never appears. Measured on Next 16: `.next/server/app/signin.html`
// baked the build-time list. Fence: `signin.test.ts`.
export const dynamic = "force-dynamic";

export default function SignIn() {
  return <SignInForm providers={configuredProviders(process.env as AuthEnv)} />;
}
