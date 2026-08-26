import { catalog } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

// GET /api/operator/catalog → Console GET /billing/catalog. The priced ladder
// the activate form picks a plan from (customer_console.md §9.1).
export async function GET(): Promise<Response> {
  return proxyToConsole((d) => catalog(d));
}
