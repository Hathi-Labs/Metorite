import { tryDecision } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → send one operator-typed decision through the bound `tier-decide`
// chain (CP-13b, §6A.14).
//
// ⚠️ `admin`, and NO elevation window. It calls the vendor with OUR platform
// key, so it costs money, but it changes nothing a customer runs on or pays.
// The Console writes one audit row and no usage row. A refusal is relayed
// word for word.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => tryDecision(body, d));
}
