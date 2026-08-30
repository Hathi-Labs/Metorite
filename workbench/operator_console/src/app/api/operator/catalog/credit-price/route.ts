import { setCreditPrice } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → price the credit itself (migration 017) — the other half of H-42.
// Billing never reads what this writes: a call bills credits, and the tier
// card owns how many. Admin plus an elevation window; the Console enforces
// both.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setCreditPrice(body, d));
}
