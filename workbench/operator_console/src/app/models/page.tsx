import { redirect } from "next/navigation";

import { ConsoleUnconfigured, readModelCatalog } from "@/lib/console";
import { staffSession } from "@/lib/session";
import Header from "../Header";
import CatalogAdmin, { type Catalog } from "./CatalogAdmin";

export const dynamic = "force-dynamic";

// The model catalog — WS-31 CP-10 slice 3. Spec §6A, D60, D61.
//
// ⚠️ **This surface RELOCATES a capability across the tenancy boundary.** A
// complete three-tab model console already exists at
// `workbench/control_plane/src/app/settings/models/page.tsx` — in the CUSTOMER
// product, hidden only by D49's `preview`. Model operations are an operator
// concern: the keys are ours, the rate card is ours, and a customer must never
// see a model at all (D32.7).
//
// The tenant-side copy is removed by CP-5, and it does not go quietly: H-70
// tracks the Providers tab that still renders there, and H-71 a DELETE the
// gateway never implemented.
//
// ⚠️ **Readable by every role.** What we can call, what we use it for and what
// it costs are all things the team should see without asking. Only the WRITES
// are gated, and the Console decides that — not this page.

export default async function ModelsPage() {
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

  let data: Catalog | null = null;
  let error: string | null = null;
  try {
    // The caller's own token, so even this READ is attributed to them.
    const result = await readModelCatalog({ authToken: session.authToken });
    if (result.status === 200) {
      data = JSON.parse(result.body) as Catalog;
    } else {
      // ⚠️ Surfaced, never swallowed. A 500 here is most likely migration 010
      // not applied on the box, and an empty table would hide that.
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
            <h1>Models</h1>
            <p className="muted">
              What we can call, what we use it for, and what it costs. Adding a
              model, re-pointing a tier and re-pricing one were hand-run SQL
              statements against the live database until this page existed.
            </p>
          </div>
        </div>
        {error ? (
          <div className="banner">{error}</div>
        ) : data ? (
          <CatalogAdmin data={data} />
        ) : null}
      </main>
    </>
  );
}
