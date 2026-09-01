import { redirect } from "next/navigation";

import { readAccounts } from "@/lib/read";
import { staffSession } from "@/lib/session";
import SectionTabs from "../SectionTabs";
import Shell, { Unconfigured } from "../Shell";
import ProviderAdmin from "./ProviderAdmin";

export const dynamic = "force-dynamic";

// Our provider accounts — WS-31 CP-10 slice 4. Spec §6A · §6B.2 · D57.7.
//
// ⚠️ **A TAB of the Models section since 2026-08-30** (owner directive): one
// sidebar entry, two tabs, both URLs kept. The account we call WITH and the
// models we call are one question, split by which half is being configured.
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
// can replay, for no benefit. The Console answered means a failure is surfaced
// rather than drawn as an empty table.

export default async function ProvidersPage() {
  const session = await staffSession();
  if (!session.configured) return <Unconfigured />;
  if (!session.ok) redirect("/login");

  const accounts = await readAccounts({ authToken: session.authToken });

  return (
    <Shell
      title="Models"
      lede="The vendor accounts the Router calls on. Installing one here is what arms AI for every customer who has not brought their own."
      origin={accounts.origin}
      note={accounts.note}
    >
      <SectionTabs current="/providers" />
      <ProviderAdmin creds={accounts.data} />
    </Shell>
  );
}
