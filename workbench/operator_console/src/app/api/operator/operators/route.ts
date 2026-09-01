import { addOperator, listOperators } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// GET /api/operator/operators → Console GET /operators.
//
// A `viewer` may read this, deliberately. Who holds power over our customers
// is exactly the thing the team should see without asking (D64.3).
export async function GET(): Promise<Response> {
  return proxyToConsole((d) => listOperators(d));
}

// POST /api/operator/operators → Console POST /operators. Admin only.
//
// ⚠️ No validation here beyond passing the body on. The four guards of §6.1
// live in `operators.py` and the Console applies them — a second copy in this
// file would be a mirror, and a mirror goes stale and then lies.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => addOperator(body, d));
}
