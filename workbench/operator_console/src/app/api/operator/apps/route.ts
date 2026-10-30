import { setOrgApp } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/apps -> Console POST /orgs/apps (CP-APP, task 1105). Turn
// Aroh on or off for an organization and set its seats. The Console refuses a
// count below the seats in use with a 409, relayed verbatim.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setOrgApp(body, d));
}
