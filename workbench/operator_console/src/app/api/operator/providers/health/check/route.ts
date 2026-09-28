import { checkProviderHealth } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → probe every vendor balance now, and answer the fresh health rows.
//
// ⚠️ `editor` on the Console. It spends one read-only request per vendor, so a
// viewer may read the balances but not drive our vendor quota. The body holds
// no secret: the Console builds it from `provider_health`, which stores none.
export async function POST(): Promise<Response> {
  return proxyToConsole((d) => checkProviderHealth(d));
}
