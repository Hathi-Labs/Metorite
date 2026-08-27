import { setModelRate } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → price one (model, task), in its own unit. INSERT, never UPDATE.
//
// ⚠️ `admin` AND a live elevation window: this is what customers are BILLED.
// 🔴 Setting a real number stays the OWNER's commercial act (H-42, §8). This
// route is the mechanism, and the ladder still ships every card `unpriced`.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setModelRate(body, d));
}
