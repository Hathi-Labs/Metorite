import { readModelCatalog } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

// GET /api/operator/catalog/models → Console GET /catalog/models.
//
// Everything the operator manages, and the two GAPS between the tables:
// `unbound` (capable and unused) and `unserved` (bound and NOT capable — a
// 500 waiting for the first request). Readable by a `viewer`.
export async function GET(): Promise<Response> {
  return proxyToConsole((d) => readModelCatalog(d));
}
