import { redirect } from "next/navigation";

import { ConsoleUnconfigured, orgUsage, usageDaily } from "@/lib/console";
import { staffSession } from "@/lib/session";
import type { OrgUsageRow, UsageDay } from "@/lib/usage";
import Header from "../Header";
import UsageBoard from "./UsageBoard";

export const dynamic = "force-dynamic";

// AI usage across every customer — WS-31, `specs/ai_metering_and_analytics.md`
// §5. This is the operator's "how are our customers using AI" question, and it
// is the one surface in this app that reads across every tenant.
//
// ⚠️ **Read HERE, server-side, with the caller's own token.** A shared token
// reaches the Console as `breakglass`, which bypasses the §5 role matrix and
// logs a warning on every page view. Reading in the client would also put a
// cross-tenant response on a path the browser can replay.
//
// 🔴 **This ships to an empty table and must say so.** `usage_event` holds no
// rows until three owner acts land: a provider credential exists, the rate card
// is priced (H-42) and `ROUTER_SERVING_ENABLED` is on (H-69). An empty chart
// reads as a quiet week, which is a different and much calmer fact.

const WINDOW_DAYS = 30;

export default async function UsagePage() {
  const session = await staffSession();
  if (!session.configured) {
    return (
      <>
        <Header />
        <main className="wrap">
          <div className="banner">
            The staff gate is not configured on this deployment, so nobody can
            sign in. Set it server-side and reload.
          </div>
        </main>
      </>
    );
  }
  if (!session.ok) redirect("/login");

  let rows: OrgUsageRow[] = [];
  let total = 0;
  let silentSlugs: string[] = [];
  let days: UsageDay[] = [];
  let spikes: string[] = [];
  let error: string | null = null;

  try {
    const deps = { authToken: session.authToken };
    const [orgs, series] = await Promise.all([
      orgUsage(WINDOW_DAYS, deps),
      usageDaily(WINDOW_DAYS, undefined, deps),
    ]);
    if (orgs.status === 200) {
      const body = JSON.parse(orgs.body) as {
        rows: OrgUsageRow[];
        total?: number;
        silentSlugs?: string[];
      };
      rows = body.rows;
      silentSlugs = body.silentSlugs ?? [];
      // ⚠️ The page is capped and the rows sort by spend, so the customers
      // that fall off are the QUIET ones — the exact rows the read LEFT JOINs
      // to include. Carrying the total is what stops the table looking whole.
      total = body.total ?? body.rows.length;
    } else {
      // Surfaced, never swallowed. An empty table would hide a 500 and read as
      // "nobody has used AI yet".
      error = `The Console answered ${orgs.status}. ${orgs.body}`;
    }
    if (series.status === 200) {
      const parsed = JSON.parse(series.body) as {
        days: UsageDay[];
        spikes: string[];
      };
      days = parsed.days;
      spikes = parsed.spikes;
    }
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) {
      error = "The Customer Console is not configured on this deployment.";
    } else {
      throw e;
    }
  }

  return (
    <>
      <Header />
      <main className="wrap">
        <div className="pagehead">
          <div>
            <h1>AI usage</h1>
            <p className="muted">
              Every customer organization, what they ran, and what it cost us.
              The last {WINDOW_DAYS} days.
            </p>
          </div>
        </div>
        {error ? (
          <div className="banner danger">{error}</div>
        ) : (
          <UsageBoard rows={rows} days={days} spikes={spikes} total={total} silentSlugs={silentSlugs} />
        )}
      </main>
    </>
  );
}
