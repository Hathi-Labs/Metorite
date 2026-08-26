import { redirect } from "next/navigation";
import { ConsoleUnconfigured, listOperators } from "@/lib/console";
import { staffSession } from "@/lib/session";
import Header from "../Header";
import OperatorAdmin, { type OperatorRow } from "./OperatorAdmin";

export const dynamic = "force-dynamic";

// The operator registry — WS-31 CP-12g. Spec §6.1, D64.3.
//
// ⚠️ **Readable by every role.** Who holds power over our customers is the
// thing the team should be able to see without asking. Only the WRITES are
// admin, and the Console decides that, not this page.

export default async function OperatorsPage() {
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

  let rows: OperatorRow[] = [];
  let error: string | null = null;
  try {
    // The caller's own token, so even this READ is attributed to them.
    const result = await listOperators({ authToken: session.authToken });
    if (result.status === 200) {
      rows = (JSON.parse(result.body) as { operators: OperatorRow[] })
        .operators;
    } else {
      // ⚠️ Surfaced, never swallowed. A 500 here is most likely migration 009
      // not applied on the box (H-64), and an empty table would hide that.
      error = `The Console answered ${result.status}. ${result.body}`;
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
            <h1>Operators</h1>
            <p className="muted">
              Everybody who can reach this console. A viewer reads, an editor
              runs the business, an admin adds and removes people.
            </p>
          </div>
        </div>
        {error ? (
          <div className="banner">{error}</div>
        ) : (
          <OperatorAdmin rows={rows} />
        )}
      </main>
    </>
  );
}
