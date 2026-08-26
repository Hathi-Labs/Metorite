import { redirect } from "next/navigation";
import { ConsoleUnconfigured, activityActions } from "@/lib/console";
import { staffSession } from "@/lib/session";
import Header from "../Header";
import ActivityFeed from "./ActivityFeed";

export const dynamic = "force-dynamic";

// The audit trail — WS-31 CP-12f, D64.5.
//
// Every act this company took against a customer, newest first, across every
// customer. Readable by every role: a record of who did what is worth nothing
// if seeing it needs a privilege.
//
// ⚠️ **The commercial record only.** `control_audit` holds OUR acts against a
// tenant. It is not a window into the tenant's own data, and this page must
// not become one (D64.5, NG-1).

export default async function ActivityPage() {
  const session = await staffSession();
  if (!session.configured) {
    return (
      <>
        <Header />
        <main className="wrap">
          <div className="banner">
            The staff gate is not configured on this deployment.
          </div>
        </main>
      </>
    );
  }
  if (!session.ok) redirect("/login");

  // The filter dropdown offers only actions that actually occurred, read from
  // the data rather than from a constant list that would drift.
  let actions: string[] = [];
  try {
    const result = await activityActions({ authToken: session.authToken });
    if (result.status === 200) {
      actions = (JSON.parse(result.body) as { actions: string[] }).actions;
    }
  } catch (e) {
    if (!(e instanceof ConsoleUnconfigured)) throw e;
  }

  return (
    <>
      <Header />
      <main className="wrap">
        <div className="pagehead">
          <div>
            <h1>Activity</h1>
            <p className="muted">
              Every act we took against a customer, newest first. Filter by
              person, by action or by company.
            </p>
          </div>
        </div>
        <ActivityFeed actions={actions} />
      </main>
    </>
  );
}
