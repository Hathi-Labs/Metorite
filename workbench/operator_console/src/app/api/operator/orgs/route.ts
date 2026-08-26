import { listOrganizations } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

// GET /api/operator/orgs → Console GET /orgs (the §4.1a cross-org customer list,
// Operator-only). The token stays server-side; the browser gets the list JSON.
export async function GET(): Promise<Response> {
  return proxyToConsole((d) => listOrganizations(d));
}
