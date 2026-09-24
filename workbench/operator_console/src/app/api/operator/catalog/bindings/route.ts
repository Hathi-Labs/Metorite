import { bindTier, unbindTier } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → point a (task, tier) pair at a model. INSERT, never UPDATE.
//
// ⚠️ `admin` AND a live elevation window. This decides what EVERY customer
// call runs on, and a wrong model here does not fail loudly — it answers,
// plausibly, at the wrong price.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => bindTier(body, d));
}

// DELETE → take a (task, tier) OFF the air (H-178).
//
// 🔴 There was no way to do this, and that made a broken tier permanent:
// `tier-stt` pointed at a Groq model on a box holding only a DeepSeek key, so
// every transcription failed at the provider and billed zero on the way.
//
// ⚠️ The SAME bar as binding. The blast radius is the same size in the other
// direction — a wrong bind answers at the wrong price, a wrong unbind stops
// answering at all.
export async function DELETE(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => unbindTier(body, d));
}
