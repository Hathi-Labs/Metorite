import { closeElevation, openElevation, readElevation } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// The elevation window (CP-12e, D64.4).
//
// ⚠️ **Always for the CALLER.** There is no operator id on any of these, and
// that is the design: elevating somebody else would hand out a destructive
// privilege they did not ask for, and the audit row would name the wrong
// person. The Console reads the operator from the session.

// GET → is a window open, and until when. Every role may ask about its own.
export async function GET(): Promise<Response> {
  return proxyToConsole((d) => readElevation(d));
}

// POST → open one. Admin only, and the reason has a floor of 12 characters,
// because a reason is what makes the row answer *why* afterwards.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => openElevation(body, d));
}

// DELETE → close it early. Finishing the job should end the privilege, rather
// than waiting out the clock.
export async function DELETE(): Promise<Response> {
  return proxyToConsole((d) => closeElevation(d));
}
