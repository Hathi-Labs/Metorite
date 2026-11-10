import { saveAppConnection } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/apps/connection -> Console POST /apps/connection/settings.
// The hub's base URL and operator token. The token crosses this route ONCE, to
// the Console, which stores it encrypted and never sends it back.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => saveAppConnection(body, d));
}
