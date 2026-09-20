import { declareCapability, removeCapability } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → declare what a model can do, and which provider verb does it.
// `editor`, no window: a capability is a FACT about a model, it is reversible,
// and nobody is billed against it.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => declareCapability(body, d));
}

// DELETE → take a model out of the catalog.
//
// ⚠️ Same gate as the POST above, and for the same reason: a capability is a
// FACT about a model, nobody is billed against it, and the Console refuses
// while a tier still serves from it. The refusal relays verbatim.
export async function DELETE(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => removeCapability(body, d));
}
