/**
 * GET /api/health — is Metorite up, and which build is this workbench?
 *
 * The probe of the shell's update notice (`lib/shell/serviceHealth.ts`).
 * Spec: `navigation_shell.md` §7.3.
 *
 * - `gateway`: "up" when the gateway's own `/health` answers 200, otherwise
 *   "down". It asks ONCE, with no retry: the point is to learn NOW whether
 *   the gateway is restarting, and `gatewayFetch`'s 25 s hold would hide that.
 * - `build`: the workbench's Next build id. A tab compares it with the one it
 *   loaded with, to learn that a new version is live.
 *
 * It always answers 200 while the workbench runs. When the workbench itself
 * is down, Caddy answers with its updating page and a 503, and the client
 * reads that as "down" too.
 *
 * ⚠️ PUBLIC (`proxy.ts` PUBLIC_API_PATHS), so the sign-in page can say
 * "updating" too. It reveals nothing a stranger could use: the gateway's
 * `/health` is already public (`PUBLIC_ROUTES`), and a build id is a random
 * name for a set of public script files.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { NextResponse } from "next/server";

import { GATEWAY_URL, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** Read once per server process. A deploy starts a new process. */
const BUILD: string | null = (() => {
  try {
    return readFileSync(join(process.cwd(), ".next", "BUILD_ID"), "utf8").trim() || null;
  } catch {
    return null;
  }
})();

export async function GET() {
  let up = false;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/health`,
      { cache: "no-store", signal: AbortSignal.timeout(3_000) },
      { retry: false },
    );
    up = res.ok;
  } catch {
    up = false;
  }
  return NextResponse.json(
    { gateway: up ? "up" : "down", build: BUILD },
    { headers: { "Cache-Control": "no-store" } },
  );
}
