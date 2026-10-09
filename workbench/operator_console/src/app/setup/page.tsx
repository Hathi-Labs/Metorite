import { redirect } from "next/navigation";

import { listOrganizations } from "@/lib/console";
import { partitionRoster, type OrgList, type OrgRow } from "@/lib/format";
import { readAiCatalog } from "@/lib/read";
import { staffSession } from "@/lib/session";
import GoLiveRail from "../GoLiveRail";
import Shell, { Unconfigured } from "../Shell";

export const dynamic = "force-dynamic";

// The go-live checklist — WS-50 slice 5. It used to sit at the top of the
// customer list, where it pushed the customers below the fold every day for a
// job done once. The list now carries one line that links here.

export default async function SetupPage() {
  const gate = await staffSession();
  if (!gate.configured) return <Unconfigured />;
  if (!gate.ok) redirect("/login");

  const deps = { authToken: gate.authToken };
  const catalog = await readAiCatalog(deps);
  let orgs: OrgRow[] = [];
  try {
    const res = await listOrganizations(deps);
    if (res.status === 200) {
      orgs = partitionRoster((JSON.parse(res.body) as OrgList).organizations).roster;
    }
  } catch {
    // Step 5 then reads "unknown" rather than red: `customersWithoutKeys`
    // treats a missing list as no evidence, never as no keys.
    orgs = [];
  }

  return (
    <Shell
      title="Setup checklist"
      lede="Everything AI needs before a customer can be served and charged, in the order it has to happen."
      origin={catalog.origin}
      note={catalog.note}
    >
      <GoLiveRail catalog={catalog.data} orgs={orgs.length > 0 ? orgs : undefined} />
    </Shell>
  );
}
