import { redirect } from "next/navigation";

import { configuredProviders, type AuthEnv } from "@/authPosture";
import { currentIdentity } from "@/lib/gateway";

import SignUpForm from "./SignUpForm";

/**
 * The self-serve signup page — the FORM over CP-2a's provision API (CP-2c
 * slice 4). A person who authenticated with the IdP but belongs to no
 * organization lands here (the `signIn` callback's zero-org limbo branch admits
 * that session and the ordinary redirect brings them where `/signup` is
 * reachable), names their organization, and this surface POSTs it — through the
 * `/api/signup` Next hop — to the gateway's `POST /signup/provision`.
 *
 * Server component on purpose, mirroring `signin/page.tsx`: the flag and the
 * provider env are server-only, and the client half (`SignUpForm`) receives the
 * derived provider list as props (it offers them when a caller reaches the form
 * without a session and the hop answers 401).
 */

// Without this the route is STATICALLY PRERENDERED and the flag freezes at
// `next build` time — an env-plus-restart flip would then never reach this
// surface. Same measured trap the signin page carries `force-dynamic` against.
// Fence: `signup.test.ts`.
export const dynamic = "force-dynamic";

export default async function SignUp() {
  // Ships dark. When `SELF_SERVE_SIGNUP_ENABLED` is not exactly `"true"` the
  // whole surface is unreachable — a REDIRECT to `/signin`, not a 404
  // (done-when 1, audit B4a). Read FIRST, before providers are derived or the
  // form renders, so a box that has not opted in serves nothing. `=== "true"`
  // EXACTLY, not truthiness (auth.ts:163's idiom): an operator who sets
  // `SELF_SERVE_SIGNUP_ENABLED=false` while debugging must get OFF, and every
  // truthy-string reading would arm it instead. Flipping it live is OWNER-GATE
  // (§8 gate 8).
  if (process.env.SELF_SERVE_SIGNUP_ENABLED !== "true") redirect("/signin");

  // ── done-when 8a (2026-08-24) · no session ⇒ /signin, not a dead form ──────
  //
  // Identity is IdP-attested BEFORE the form (CP-2c item 1): the owner of the
  // new organization is the SESSION email, resolved server-side, and the
  // `/api/signup` hop 401s without one. Rendering the four-field form to a
  // signed-out visitor therefore asked them to name an organization, a slug, a
  // state and a GSTIN before telling them the only thing that mattered.
  //
  // `currentIdentity()` and not a second session read: it is the same seam the
  // hop's `requireIdentity()` sits on, so "may this render" and "will the
  // submit work" cannot drift — and it carries the laptop bypass, so an
  // unconfigured dev box is unchanged.
  //
  // ⚠️ **Ordered flag-first, session-second, deliberately.** An un-opted-in
  // deployment must not disclose that this surface exists behind a sign-in;
  // both gates land on `/signin`, but only one of them may be reached by
  // someone who has not signed in.
  //
  // `SignUpForm`'s own `needsSignIn` arm STAYS: it is the answer to a session
  // that expired between render and submit, which this check cannot see.
  if (!(await currentIdentity())) redirect("/signin");

  return <SignUpForm providers={configuredProviders(process.env as AuthEnv)} />;
}
