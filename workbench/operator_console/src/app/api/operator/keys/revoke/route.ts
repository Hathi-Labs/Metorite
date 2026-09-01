import { revokeKey } from "@/lib/console";
import { proxyToConsole, readJsonBody } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST /api/operator/keys/revoke → Console POST /keys/revoke. Kills one key by
// PREFIX — the only handle anybody has, because the token itself was never
// stored. Its own file rather than a DELETE on ../keys, matching
// `seats/release`: the Console models revocation as a POST, and a BFF that
// re-shaped the verb would make the two halves harder to line up in an audit.
//
// `admin` AND a live elevation window (the §5 matrix).
export async function POST(request: Request): Promise<Response> {
  const body = await readJsonBody(request);
  return proxyToConsole((d) => revokeKey(body, d));
}
