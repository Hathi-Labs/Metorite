import { assignSeat } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/seats → Console POST /billing/seats. Assign a seat on a
// plan; the Console 409s at the cap with a buy-more payload, relayed verbatim.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => assignSeat(body, d));
}
