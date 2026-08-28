import { redirect } from "next/navigation";

import { ConsoleUnconfigured, listProviderCreds } from "@/lib/console";
import type { ProviderCred } from "@/lib/providers";
import { staffSession } from "@/lib/session";
import Header from "../Header";
import ProviderAdmin from "./ProviderAdmin";

export const dynamic = "force-dynamic";

// Our provider accounts — WS-31 CP-10 slice 4. Spec §6A · §6B.2 · D57.7.
//
// ⚠️ **This is the last hand-run step in the AI chain.** The Console has had
// `GET/POST /providers/credentials` and revoke since CP-10 slice 1, and no
// page called them — so installing the key that lets the Router call a vendor
// meant curling the API. Measured 2026-08-28: `provider_credential` held 0
// live rows, which is the single reason no AI call can be served.
//
// ⚠️ **Not `llm_api_key`.** §6B.2 tabulates the pair because they are the most
// confusable objects in the system:
//
//   provider_credential  OURS   — presented BY the Router TO the vendor.
//   llm_api_key          THEIRS — presented BY a customer's box TO the Router.
//
// A leak of the first is our whole vendor bill. A leak of the second is one
// organization's AI spend.
//
// ⚠️ **The list is read HERE, server-side, with the caller's own token.** The
// secret is not in it and cannot be — the Console's query does not select the
// column. Reading it in the client would put the list on a path the browser
// can replay, for no benefit.

export default async function ProvidersPage() {
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

  let creds: ProviderCred[] = [];
  let error: string | null = null;
  try {
    const result = await listProviderCreds(true, { authToken: session.authToken });
    if (result.status === 200) {
      creds = (JSON.parse(result.body) as { credentials: ProviderCred[] }).credentials;
    } else {
      // Surfaced, never swallowed. An empty table would hide a 500 and read
      // as "nothing installed yet", which is a different and calmer fact.
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
            <h1>Providers</h1>
            <p className="muted">
              The vendor accounts the Router calls on. Installing one here is
              what arms AI for every customer who has not brought their own.
            </p>
          </div>
        </div>
        {error ? (
          <div className="banner">{error}</div>
        ) : (
          <ProviderAdmin creds={creds} />
        )}
      </main>
    </>
  );
}
