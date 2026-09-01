import { bindTier } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → point a (task, tier) pair at a model. INSERT, never UPDATE.
//
// ⚠️ `admin` AND a live elevation window. This decides what EVERY customer
// call runs on, and a wrong model here does not fail loudly — it answers,
// plausibly, at the wrong price.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => bindTier(body, d));
}
