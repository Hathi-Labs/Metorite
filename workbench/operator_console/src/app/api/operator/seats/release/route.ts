import { releaseSeat } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/seats/release → Console POST /billing/seats/release. Frees
// a seat immediately (D19.3).
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => releaseSeat(body, d));
}
