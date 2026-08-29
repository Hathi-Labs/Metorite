import { setModelProfile } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → record what a model IS: window, output cap, what the vendor charges
// us, and whether it reads images.
//
// ⚠️ `editor`, and NO elevation window — the only catalog write that needs
// neither `admin` nor an open window. It changes neither what runs nor what we
// charge, and gating reference data behind elevation would teach people to
// reach for the break-glass token for routine work.
//
// ⚠️ **UPSERT, unlike every other catalog write.** A tier binding and a rate
// card are insert-only so a past invoice stays readable against the decision
// that produced it. A context window is a fact about the world.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setModelProfile(body, d));
}
