import { readActivity } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

//: Only these reach the Console. ⚠️ An allowlist rather than a pass-through of
//: `request.nextUrl.search`: a caller could otherwise append any parameter it
//: liked, and a future Console route that grew one would be reachable from the
//: browser without anybody deciding it should be.
const ALLOWED = ["actor", "action", "org_slug", "limit", "cursor"] as const;

// GET /api/operator/activity → Console GET /activity.
//
// The cross-org audit trail. Readable by every role, and keyset-paginated —
// ⚠️ the cursor is EPHEMERAL and must not be persisted. `operator_activity.py`
// carries the measured reason (H-7).
export async function GET(request: Request): Promise<Response> {
  const source = new URL(request.url).searchParams;
  const forwarded = new URLSearchParams();
  for (const name of ALLOWED) {
    const value = source.get(name);
    if (value !== null && value.trim() !== "") {
      forwarded.set(name, value.trim());
    }
  }
  return proxyToConsole((d) => readActivity(forwarded.toString(), d));
}
