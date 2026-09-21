/**
 * BFF: what this organization ran, and what it cost. **D66 (a), H-134.**
 *
 * 🔴 **The endpoint was built, tested and reachable, and nothing called it.**
 * `settings/billing` read only `/me/billing`, so a customer saw a balance, a
 * burn figure and a runway — and could not see what any of it went ON. A
 * customer who cannot see the breakdown cannot manage the spend, so every
 * credit question became a support conversation.
 *
 * ⚠️ **A non-admin sees only their OWN activity.** `GET /my/usage/activity`
 * takes a `member` parameter and its docstring is explicit that the workbench
 * fills it "from the signed-in session, never from the browser". This route
 * is where that promise is kept: the value comes from `requireSpendReader`,
 * and there is no query parameter a caller could set instead.
 *
 * The credential, the fail-closed 503 and the relay policy are
 * `_console.ts`'s — the same two environment variables the shipped read proxy
 * uses, because a third configuration surface for one credential is how a
 * deployment ends up half-configured in a way nobody can see.
 */
import { NextResponse } from "next/server";

import {
  consoleConfig,
  consoleHeaders,
  consoleUnavailable,
  notConfigured,
  relayConsole,
  requireSpendReader,
} from "../../_console";

export const dynamic = "force-dynamic";

export async function GET() {
  const who = await requireSpendReader();
  if (who instanceof NextResponse) return who;

  const config = consoleConfig();
  if (!config) return notConfigured();

  // ⚠️ The scope is decided HERE, from the resolved session. An admin reads
  // the organization; everybody else reads themselves.
  const scope = who.isAdmin
    ? ""
    : `?member=${encodeURIComponent(who.email)}`;

  try {
    const res = await fetch(`${config.url}/my/usage/activity${scope}`, {
      headers: consoleHeaders(config.key, who.email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
