import { redirect } from "next/navigation";

import { configuredProviders, type AuthEnv } from "@/authPosture";

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

export default function SignUp() {
  // Ships dark. When `SELF_SERVE_SIGNUP_ENABLED` is not exactly `"true"` the
  // whole surface is unreachable — a REDIRECT to `/signin`, not a 404
  // (done-when 1, audit B4a). Read FIRST, before providers are derived or the
  // form renders, so a box that has not opted in serves nothing. `=== "true"`
  // EXACTLY, not truthiness (auth.ts:163's idiom): an operator who sets
  // `SELF_SERVE_SIGNUP_ENABLED=false` while debugging must get OFF, and every
  // truthy-string reading would arm it instead. Flipping it live is OWNER-GATE
  // (§8 gate 8).
  if (process.env.SELF_SERVE_SIGNUP_ENABLED !== "true") redirect("/signin");

  return <SignUpForm providers={configuredProviders(process.env as AuthEnv)} />;
}
