import { revokeProviderCred } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → revoke the live credential for a provider.
//
// 🔴 Revoking the PLATFORM credential stops every AI call that is not BYOK.
// That is what a revocation means and it is why the Console demands `admin`
// plus elevation. The row survives; only its `revoked_at` is set.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => revokeProviderCred(body, d));
}
