import { setLifecycle } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/lifecycle → Console POST /orgs/lifecycle. Suspend / resume
// / close — a TRANSITION, never a free-form status set; the Console's
// `assert_transition` graph refuses an illegal move (409). Body {org_slug,
// target, reason?}.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => setLifecycle(body, d));
}
