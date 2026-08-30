import { setTierRate } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → price one (tier, task) — D65. What a customer pays is keyed on the
// tier they picked, so a failover moves our cost and never their price.
// Admin plus an elevation window; the Console enforces both.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setTierRate(body, d));
}
