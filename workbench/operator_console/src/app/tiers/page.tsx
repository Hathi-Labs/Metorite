import { redirect } from "next/navigation";

import { readAiCatalog } from "@/lib/read";
import { staffSession } from "@/lib/session";
import Shell, { Unconfigured } from "../Shell";
import TierBoard from "./TierBoard";

export const dynamic = "force-dynamic";

// Tiers and their backups — WS-31, `specs/ai_metering_and_analytics.md` §5.
//
// 🔴 **A customer never sees a model, only a tier (D66, D32.7).** So this page
// is where the words a customer reads — Fast, Balanced, Powerful — get attached
// to the things we actually call. It is the only place in the product where
// those two vocabularies meet.
//
// ⚠️ **The armed-provider list decides what counts as a real backup.** A step
// whose provider we hold no live key for cannot be tried, so it is not a
// backup — it is a gap that only shows up after the first choice has already
// failed. `fallback.ts` refuses to count one.

export default async function TiersPage() {
  const session = await staffSession();
  if (!session.configured) return <Unconfigured />;
  if (!session.ok) redirect("/login");

  const catalog = await readAiCatalog({ authToken: session.authToken });

  // Live AND platform, both. A revoked row and an org-scoped BYOK row each look
  // like coverage in a list, and neither is coverage for everybody else.
  const armed = [
    ...new Set(
      catalog.data.accounts
        .filter((a) => !a.revokedAt && !a.orgSlug)
        .map((a) => a.provider),
    ),
  ];

  return (
    <Shell
      title="Tiers & backups"
      lede="What a customer picks, what it runs on, and where it goes when that stops answering."
      origin={catalog.origin}
      note={catalog.note}
    >
      <TierBoard catalog={catalog.data} armed={armed} />
    </Shell>
  );
}
