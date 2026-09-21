/**
 * BFF: what each person in this organization spent. **D66 (b), H-134.**
 *
 * 🔴 **ADMIN ONLY, and this is the gate.** The row names a colleague and what
 * they cost. `settings/billing` refuses a non-admin, and the page's own
 * comment calls that "a COURTESY, not a security boundary" — so the gate that
 * matters is `requireSpendReader`, server-side, before the Console is touched.
 *
 * ⚠️ **This must never grow a cap column.** The upstream docstring says so:
 * showing a cap beside a spend implies the cap is enforced, and
 * `member_ai_cap` is NOT enforced while the member identity is still taken
 * from an inbound header (H-73). A number that looks like a control and is not
 * one is worse than an absent number.
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

  if (!who.isAdmin) {
    // ⚠️ 403 and a sentence that says who to ask. A member reading their own
    // spend has the activity route; this one is the whole company.
    return NextResponse.json(
      {
        detail:
          "Per-person spend is visible to organization admins. Your own usage is on the activity breakdown.",
      },
      { status: 403 },
    );
  }

  const config = consoleConfig();
  if (!config) return notConfigured();

  try {
    const res = await fetch(`${config.url}/my/usage/members`, {
      headers: consoleHeaders(config.key, who.email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
