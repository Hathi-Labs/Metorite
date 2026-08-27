import { issueKey, listKeys } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// GET /api/operator/keys?org_slug=… → Console GET /keys. Metadata only: the
// prefix, the label, who minted it and when it was revoked. Never a hash and
// never a token — `store.list_keys` cannot return one.
//
// `viewer` may read this (the §5 matrix). Knowing WHICH keys exist for a
// customer is support information; being able to mint one is not.
export async function GET(request: Request): Promise<Response> {
  const slug = new URL(request.url).searchParams.get("org_slug") ?? "";
  return proxyToConsole((d) => listKeys(slug, d));
}

// POST /api/operator/keys → Console POST /keys. Mints one organization key.
//
// ⚠️ **The Console returns the token exactly once**, and this route relays the
// body verbatim like every other. That is deliberate: re-shaping the response
// here would put a second opinion between the only copy of a secret and the
// person who needs to store it.
//
// `admin` AND a live elevation window (the §5 matrix). A caller with neither
// gets the Console's own 403, relayed unchanged.
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => issueKey(body, d));
}
