import { redirect } from "next/navigation";

import { readAiCatalog } from "@/lib/read";
import { staffSession } from "@/lib/session";
import Shell, { Unconfigured } from "../Shell";
import DeclareModel from "./DeclareModel";
import ModelBrowser from "./ModelBrowser";

export const dynamic = "force-dynamic";

// The model catalog — WS-31 CP-10 slice 3, rebuilt as a browser.
//
// 🔴 **Tiers left this page.** It used to do two jobs: hold the catalog AND
// bind each tier to a model. That is why it was confusing — the reader had to
// know which of the two questions they were answering before they could read
// anything. Tiers and their backups now live at `/tiers`, and this page finds
// a model.
//
// ⚠️ **Readable by every role.** What we can call and what it costs are things
// the team should see without asking. Only the WRITES are gated, and the
// Console decides that — not this page.
//
// ⚠️ **Read HERE, server-side, with the caller's own token.** A shared token
// reaches the Console as `breakglass`, which bypasses the §5 role matrix and
// logs a warning on every page view.

export default async function ModelsPage() {
  const session = await staffSession();
  if (!session.configured) return <Unconfigured />;
  if (!session.ok) redirect("/login");

  const catalog = await readAiCatalog({ authToken: session.authToken });

  return (
    <Shell
      title="Models"
      lede="Everything we can call, what each one is good at, and whether we can sell it yet."
      origin={catalog.origin}
      note={catalog.note}
    >
      {/* D67: the pricing cockpit moved to /tiers — the customer buys a
          tier, so the price lives beside the tiers it prices. This page is
          the SUPPLY side: what exists, what it can do, what WE pay. */}
      <ModelBrowser models={catalog.data.models} feed={catalog.data.feed} />
      <DeclareModel tasks={catalog.data.tasks} accounts={catalog.data.accounts} />
    </Shell>
  );
}
