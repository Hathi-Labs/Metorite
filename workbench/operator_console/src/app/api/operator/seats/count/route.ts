import { setSeatCount } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/seats/count → Console POST /billing/seats/count. Set how
// many seats the organization holds on a plan. The Console refuses a count
// below the seats in use with a 409 that names the number, relayed verbatim.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setSeatCount(body, d));
}
