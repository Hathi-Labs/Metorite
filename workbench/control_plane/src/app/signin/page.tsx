import { redirect } from "next/navigation";

import { configuredProviders, type AuthEnv } from "@/authPosture";

import SignInForm from "./SignInForm";
import { codeStepRedirect } from "./errorCopy";

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

export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<{ error?: string | string[] }>;
}) {
  // `pages.error` is this page (`auth.ts`), so a wrong email code arrives here
  // as `?error=Verification`. Send it back to the page where the code is typed.
  const { error } = await searchParams;
  const back = codeStepRedirect(typeof error === "string" ? error : null);
  if (back) redirect(back);

  return <SignInForm providers={configuredProviders(process.env as AuthEnv)} />;
}
