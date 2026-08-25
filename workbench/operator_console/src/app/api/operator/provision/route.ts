import { provisionOrg } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/provision → Console POST /orgs/provision (the operator
// create-a-customer arm). The body ({slug, name, owner_email, deployment_label,
// gstin?, billing_state?, core_seats?}) is forwarded to the Console, which is
// the authority on every refusal: 400 (missing deployment_label under the
// operator scheme), 404 (unknown label), 409 (already placed on another
// deployment). `proxyToConsole` gates on staff and relays the Console's status +
// JSON verbatim, so the operator token stays server-side and a refusal stays a
// refusal.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole(() => provisionOrg(body));
}
