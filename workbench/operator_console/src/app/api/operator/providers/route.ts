import { installProviderCred } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → install or ROTATE one provider credential (CP-10 slice 4).
//
// ⚠️ `admin` AND a live elevation window: this secret is what the Router
// presents to the vendor, so a leak is our whole bill rather than one
// customer's spend. The Console enforces the role; this route relays it.
//
// ⚠️ There is no GET here. The list is read by the page's server component
// through `listProviderCreds`, so the secret never crosses a browser boundary
// in either direction.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => installProviderCred(body, d));
}
