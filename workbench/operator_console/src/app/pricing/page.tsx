import { redirect } from "next/navigation";

import { readAiCatalog } from "@/lib/read";
import { staffSession } from "@/lib/session";
import Shell, { Unconfigured } from "../Shell";
import CreditPrice from "./CreditPrice";
import PriceBoard from "./PriceBoard";

export const dynamic = "force-dynamic";

// The money page — what a customer PAYS. Owner IA directive, 2026-08-30.
//
// 🔴 **Split out of /tiers because the two questions have two audiences.**
// The tier board answers "what serves, and what happens when it stops" — an
// operations question. This page answers "what do we charge, and what do we
// keep" — a commercial one. On one screen the second question was a panel
// under the first and the owner could not find it.
//
// 🔴 **Two sections since 2026-09-20, and it was five.** The five were a
// read-only price list, a suggestion board, a hand form and a margin monitor,
// plus the credit price. Four of them answered ONE question — "what do we
// charge for this tier, and is it enough?" — in four places, and three of them
// separately reported the same unsaved credit price. `PriceBoard` puts the
// whole decision on the tier's own card, which is the unit being priced.
//
// ⚠️ **This page still prices per (tier, task) — D67 is untouched.** The key
// did not move; the panels did. A failover changes our cost, never the
// customer's price.
//
// ⚠️ **Setup order lives on the go-live rail, not here.** The rail on the
// Organizations page already walks install → declare → bind → price in
// order, and a second copy here would be the mirror that goes stale.

export default async function PricingPage() {
  const session = await staffSession();
  if (!session.configured) return <Unconfigured />;
  if (!session.ok) redirect("/login");

  const catalog = await readAiCatalog({ authToken: session.authToken });

  return (
    <Shell
      title="Pricing"
      lede="What a customer pays for AI, per tier — and what that leaves us over the vendor's price."
      origin={catalog.origin}
      note={catalog.note}
    >
      <CreditPrice price={catalog.data.creditPrice} />
      <PriceBoard catalog={catalog.data} />
      <p className="note">
        New here? The go-live rail on the{" "}
        <a href="/">Organizations page</a> walks the whole setup order — keys,
        models, tiers, then prices. Credits themselves are granted per
        customer, on the customer&apos;s own page.
      </p>
    </Shell>
  );
}
