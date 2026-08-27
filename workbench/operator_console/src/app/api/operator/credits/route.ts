import { grantCredits } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/credits → Console POST /credits/grant. Append-only ledger
// write; a correction is another row, never an edit. Body {org_slug, credits,
// reason?, ref?} is forwarded and the Console validates the reason.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => grantCredits(body, d));
}
