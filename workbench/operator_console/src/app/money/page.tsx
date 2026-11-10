import { redirect } from "next/navigation";

import { ConsoleUnconfigured, listOrganizations, orgUsage, usageDaily } from "@/lib/console";
import { fleetMoney } from "@/lib/fleet";
import { partitionRoster, type OrgList, type OrgRow } from "@/lib/format";
import { readProviderSpend } from "@/lib/read";
import { staffSession } from "@/lib/session";
import type { OrgUsageRow, OrgUsageView, UsageDay } from "@/lib/usage";
import { consoleQuery, rangeFrom } from "@/lib/range";
import RangePicker from "../RangePicker";
import Header from "../Header";
import { Unconfigured } from "../Shell";
import MoneyBoard from "./MoneyBoard";
import VendorSpend from "./VendorSpend";

export const dynamic = "force-dynamic";

// Money — WS-50 slice 4, decision D94. It replaces "AI usage" (/usage now
// redirects here).
//
// ⚠️ **Read HERE, server-side, with the caller's own token.** A shared token
// reaches the Console as `breakglass`, which bypasses the role matrix and
// logs a warning on every page view. These reads are cross-tenant, so they
// never go to the browser as a replayable request.
//
// 🔴 **Four reads, and a failed one is SAID.** The roster gives seats and
// status, the usage read gives credits, cost and draws, the daily series
// draws the line, and the vendor read gives the bill by vendor. An empty table
// over a failed read would say "no customer spent anything".

const WINDOW_DAYS = 30;

export default async function MoneyPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string }>;
}) {
  const session = await staffSession();
  if (!session.configured) return <Unconfigured />;
  if (!session.ok) redirect("/login");
  // WS-50 slice 7: the period comes from the URL.
  const range = rangeFrom(await searchParams, new Date());
  const rangeQ = consoleQuery(range);

  const deps = { authToken: session.authToken };
  let orgs: OrgRow[] = [];
  let view: Partial<OrgUsageView> | null = null;
  let silentSlugs: string[] = [];
  let unbilled = { orgs: 0, calls: 0, tokens: 0 };
  let days: UsageDay[] = [];
  let spikes: string[] = [];
  let seriesError: string | null = null;
  let error: string | null = null;

  try {
    const [orgRes, usageRes, seriesRes] = await Promise.all([
      listOrganizations(deps),
      orgUsage(WINDOW_DAYS, deps, rangeQ),
      usageDaily(WINDOW_DAYS, undefined, deps, rangeQ),
    ]);
    if (orgRes.status === 200) {
      orgs = (JSON.parse(orgRes.body) as OrgList).organizations;
    } else {
      error = `The customer list did not load: the Console answered ${orgRes.status}.`;
    }
    if (usageRes.status === 200) {
      const body = JSON.parse(usageRes.body) as Partial<OrgUsageView> & {
        silentSlugs?: string[];
        unbilledOrgs?: number;
        unbilledCallsTotal?: number;
        unbilledTokensTotal?: number;
      };
      view = body;
      silentSlugs = body.silentSlugs ?? [];
      unbilled = {
        orgs: body.unbilledOrgs ?? 0,
        calls: body.unbilledCallsTotal ?? 0,
        tokens: body.unbilledTokensTotal ?? 0,
      };
    } else {
      error = `The usage figures did not load: the Console answered ${usageRes.status}.`;
    }
    if (seriesRes.status === 200) {
      const series = JSON.parse(seriesRes.body) as { days?: UsageDay[]; spikes?: string[] };
      days = series.days ?? [];
      spikes = series.spikes ?? [];
    } else {
      seriesError = `The daily chart did not load: the Console answered ${seriesRes.status}.`;
    }
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) {
      error = "The Customer Console is not configured on this deployment.";
    } else {
      throw e;
    }
  }

  const spend = await readProviderSpend(deps, rangeQ);
  // 🔴 A Console before the range read ignores `from`, answers the last 30
  // days, and sends no `rangeFrom`. Say so rather than label 30 days as the
  // chosen period.
  const rangeIgnored = range.from !== null && view !== null && !("rangeFrom" in view);
  // 🔴 Deleted customers stay OUT of the totals, the same as on the
  // customer list, so the two pages quote one profit (review, slice 4).
  // Their rows are computed apart and shown only on request.
  const { roster, purged } = partitionRoster(orgs);
  const now = new Date();
  const fleet = fleetMoney(roster, view, now);
  const purgedRows = fleetMoney(purged, view, now).rows;
  const usageRows: OrgUsageRow[] = view?.rows ?? [];

  return (
    <>
      <Header />
      <main className="wrap">
        <div className="pagehead">
          <div>
            <h1>Money</h1>
            <p className="muted">
              What each customer paid us, what their AI cost us, and the profit,
              in rupees. Choose the period below.
            </p>
          </div>
        </div>
        <RangePicker path="/money" range={range} />
        {rangeIgnored && (
          <div className="banner">
            <strong>This Console build cannot answer a date range yet,</strong> so the
            figures below are the last 30 days. The range works after the next
            Console deploy.
          </div>
        )}
        {error ? (
          <div className="banner danger">{error}</div>
        ) : (
          <MoneyBoard
            periodLabel={rangeIgnored ? "last 30 days" : range.label}
            fleet={fleet}
            purgedRows={purgedRows}
            usageRows={usageRows}
            days={days}
            spikes={spikes}
            seriesError={seriesError}
            silentSlugs={silentSlugs}
            unbilled={unbilled}
          />
        )}
        {/* What the vendors billed US, by vendor. It moved here from the
            Providers tab: it is a bill, and a bill belongs with the money. */}
        <VendorSpend spend={spend.data} days={rangeIgnored ? WINDOW_DAYS : range.days} />
      </main>
    </>
  );
}
