/**
 * BFF: what each app spent, and which agents inside it. **Usage slice 3.**
 *
 * The read behind the "By app" panel on `settings/billing`. It is the
 * activity route grouped one level up: the Console's `GET /my/usage/apps`
 * returns one row per app with its agents nested.
 *
 * ⚠️ **A non-admin sees only their OWN apps.** The scope comes from
 * `requireSpendReader`, server-side, exactly as the activity route takes it.
 * There is no query parameter a browser could set to widen it.
 *
 * ⚠️ **Credits only.** The Console's customer read carries no cost, model or
 * tier (D66), and `test_usage_breakdown.py` walks that response for them.
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

  // ⚠️ Decided HERE, from the resolved session. An admin reads the
  // organization. Everybody else reads themselves.
  const scope = who.isAdmin ? "" : `?member=${encodeURIComponent(who.email)}`;
  try {
    const res = await fetch(`${config.url}/my/usage/apps${scope}`, {
      headers: consoleHeaders(config.key, who.email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
