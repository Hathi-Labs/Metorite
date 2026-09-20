import { setTierMargin } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → the margin we INTEND on one tier, and the floor that alarms (029).
//
// 🔴 **The write half migration 029 never got.** It said "an agent builds the
// mechanism, the owner sets the figures". The table and three reads shipped
// and no route did, so the only way in was hand-written SQL — and the board's
// alarm could never fire.
//
// Admin plus an elevation window; the Console enforces both. The NUMBERS stay
// the owner's commercial act (H-42).
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setTierMargin(body, d));
}
