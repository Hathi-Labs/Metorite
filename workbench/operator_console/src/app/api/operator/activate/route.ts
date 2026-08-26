import { activateSubscription } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/activate → Console POST /billing/subscriptions/activate.
// Manual / bank-transfer activation of a PAID plan (§6 item (j)). The body
// ({org_slug, plan_slug, seats, credits?, reference?}) is forwarded to the
// Console, which is the authority on the double-grant 409.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => activateSubscription(body, d));
}
