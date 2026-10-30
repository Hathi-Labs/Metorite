import { disconnectApp } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/apps/connection/disconnect -> Console
// POST /apps/connection/disconnect. OWNER-GATE: it revokes the Aroh box key,
// then tells the hub to drop it.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => disconnectApp(body, d));
}
