import { connectApp } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/apps/connection/connect -> Console
// POST /apps/connection/connect. OWNER-GATE: it mints (or rotates) the Aroh
// box key and pushes it to the hub. The key never reaches this app.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => connectApp(body, d));
}
